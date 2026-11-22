"""
NorthStar - publish drift and business metrics to CloudWatch. Lab 6 Task 1.

Runs on the LAPTOP after the Evidently processing job returns, never inside
it: installing Evidently in the sklearn container pushes urllib3 past what the
container's botocore supports, so a put-metric-data call from inside the job
is not trustworthy. The job writes drift_violations.json; this reads it.

Namespace NorthStar/ChurnModel, every metric carries EndpointName:

  MODEL layer
    DriftDetected        0 or 1          the number the P3 drift alarm watches
    DriftViolationCount  features over their threshold
    FeaturePSI           one per PSI feature (+ Feature dimension); alert > 0.2
    FeatureKSPValue      one per KS feature  (+ Feature dimension); alert < 0.05
  BUSINESS layer
    ChurnAlertVolume       predictions at or above the churn-alert score
    ChurnAlertVolumeRatio  today's volume / trailing 7-day average (1.0 with
                           no history); the P3 volume-drop alarm fires < 0.7

PSI and KS go to different metric names on purpose. They move in opposite
directions, so one "drift value" graph with one threshold line would be wrong
for half its series.

    python monitoring/publish_metrics.py
    python monitoring/publish_metrics.py --violations s3://<bucket>/monitoring/reports/<job>/drift_violations.json
    python monitoring/publish_metrics.py --dry-run
    python monitoring/publish_metrics.py --mlflow-arn <MLflow App ARN> --job-name nsc-evidently-...
"""

import argparse
import datetime as dt
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import ENDPOINT, NAMESPACE, REGION, ROOT  # noqa: E402

REPORTS = os.path.join(ROOT, "monitoring", "reports")
# Evidently method -> (metric name, CloudWatch unit)
METRIC_FOR_METHOD = {"psi": "FeaturePSI", "ks": "FeatureKSPValue"}


def read_json(path: str, s3):
    if path.startswith("s3://"):
        bucket, _, key = path[len("s3://"):].partition("/")
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    with open(path) as fh:
        return json.load(fh)


def drift_metric_data(summary: dict, endpoint: str, now) -> list:
    """Model-layer datapoints from a drift_violations.json summary."""
    ep = [{"Name": "EndpointName", "Value": endpoint}]
    data = [
        {"MetricName": "DriftDetected", "Dimensions": ep, "Timestamp": now,
         "Value": 1.0 if summary["drift_detected"] else 0.0, "Unit": "Count"},
        {"MetricName": "DriftViolationCount", "Dimensions": ep, "Timestamp": now,
         "Value": float(summary["violation_count"]), "Unit": "Count"},
    ]
    for feature, d in summary["per_feature"].items():
        name = METRIC_FOR_METHOD.get(d["method"].lower())
        if name is None:
            sys.exit(f"FATAL: no CloudWatch metric mapped for method {d['method']!r}. "
                     "Add it to METRIC_FOR_METHOD (statistics and p-values must "
                     "not share a metric name).")
        data.append({"MetricName": name, "Timestamp": now, "Value": float(d["value"]),
                     "Unit": "None",
                     "Dimensions": ep + [{"Name": "Feature", "Value": feature}]})
    return data


def trailing_average(cw, endpoint: str, now, days: int = 7):
    """Mean daily ChurnAlertVolume over the previous `days`, or None with no history."""
    r = cw.get_metric_statistics(
        Namespace=NAMESPACE, MetricName="ChurnAlertVolume",
        Dimensions=[{"Name": "EndpointName", "Value": endpoint}],
        StartTime=now - dt.timedelta(days=days), EndTime=now - dt.timedelta(minutes=1),
        Period=86400, Statistics=["Sum"])
    sums = [p["Sum"] for p in r.get("Datapoints", [])]
    return (sum(sums) / len(sums)) if sums else None


def business_metric_data(traffic: dict, endpoint: str, now, average) -> list:
    ep = [{"Name": "EndpointName", "Value": endpoint}]
    volume = float(traffic["churn_alerts"])
    ratio = 1.0 if not average else volume / average
    return [
        {"MetricName": "ChurnAlertVolume", "Dimensions": ep, "Timestamp": now,
         "Value": volume, "Unit": "Count"},
        {"MetricName": "ChurnAlertVolumeRatio", "Dimensions": ep, "Timestamp": now,
         "Value": ratio, "Unit": "None"},
    ]


def log_to_mlflow(arn: str, summary: dict, job_name: str, variant: str) -> None:
    """One MLflow run per drift job in experiment northstar-drift (optional)."""
    import mlflow

    # MLflow prints an emoji when a run ends; a cp1252 Windows console raises on it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    mlflow.set_tracking_uri(arn)
    mlflow.set_experiment("northstar-drift")
    with mlflow.start_run(run_name=f"drift-{job_name}"):
        mlflow.log_params({"baseline_records": summary["baseline_records"],
                           "captured_records": summary["captured_records"],
                           "variant": variant, "job_name": job_name})
        for feature, d in summary["per_feature"].items():
            mlflow.log_metric(f"{d['method']}_{feature}", d["value"])
        mlflow.log_metric("violation_count", summary["violation_count"])
        html = os.path.join(REPORTS, "drift_report.html")
        if os.path.exists(html):
            mlflow.log_artifact(html)
    runs = mlflow.search_runs(experiment_names=["northstar-drift"])
    out = os.path.join(ROOT, "docs", "lab6-mlflow-runs.txt")
    cols = [c for c in runs.columns
            if c in ("run_id", "status", "start_time", "tags.mlflow.runName")
            or c.startswith(("metrics.", "params."))]
    with open(out, "w", newline="\n") as fh:
        fh.write("mlflow.search_runs(experiment_names=['northstar-drift'])\n\n")
        fh.write(runs[cols].to_string(index=False) + "\n")
    print(f"mlflow   : logged drift-{job_name}; wrote docs/lab6-mlflow-runs.txt")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--violations", default=os.path.join(REPORTS, "drift_violations.json"),
                    help="Local path or s3:// URI of drift_violations.json")
    ap.add_argument("--traffic", default=os.path.join(REPORTS, "traffic_summary.json"),
                    help="traffic_summary.json from invoke_traffic.py")
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--variant", default="champion")
    ap.add_argument("--dry-run", action="store_true", help="Print the datapoints; send nothing.")
    ap.add_argument("--mlflow-arn", default=None, help="MLflow App ARN (optional)")
    ap.add_argument("--job-name", default="manual", help="Processing job name, for the MLflow run")
    args = ap.parse_args()

    session = boto3.Session(region_name=REGION)
    cw, s3 = session.client("cloudwatch"), session.client("s3")
    now = dt.datetime.now(dt.timezone.utc)

    summary = read_json(args.violations, s3)
    if summary.get("underpowered_window"):
        print("WARNING: this drift run was underpowered (< 500 captured records). "
              "Publishing it anyway would put an untrustworthy point on the dashboard.")
    data = drift_metric_data(summary, args.endpoint, now)

    if os.path.exists(args.traffic) or args.traffic.startswith("s3://"):
        traffic = read_json(args.traffic, s3)
        average = None if args.dry_run else trailing_average(cw, args.endpoint, now)
        data += business_metric_data(traffic, args.endpoint, now, average)
    else:
        print(f"WARNING: {args.traffic} not found; business-layer metrics NOT published.")

    for d in data:
        dims = ",".join(f"{x['Name']}={x['Value']}" for x in d["Dimensions"])
        print(f"  {d['MetricName']:<22} {d['Value']:>12.4f}  {dims}")
    if args.dry_run:
        print(f"dry run: {len(data)} datapoints NOT sent to {NAMESPACE}")
        return 0

    cw.put_metric_data(Namespace=NAMESPACE, MetricData=data)
    print(f"published {len(data)} datapoints to {NAMESPACE} at "
          f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    if args.mlflow_arn:
        try:
            log_to_mlflow(args.mlflow_arn, summary, args.job_name, args.variant)
        except Exception as exc:  # optional: never fail the publish over it
            print(f"mlflow   : skipped ({exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
