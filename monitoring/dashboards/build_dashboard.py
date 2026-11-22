"""
NorthStar - build, publish and export the NorthStar-AI-Platform dashboard.
Lab 6 Task 1.

One row per monitoring layer, top to bottom in the order an on-call engineer
reads them: is the box alive, is data arriving, is the model still seeing the
world it was trained on, are callers being served, is the business outcome
still there.

    python monitoring/dashboards/build_dashboard.py            # print the body
    python monitoring/dashboards/build_dashboard.py --put      # create/update it
    python monitoring/dashboards/build_dashboard.py --export   # get-dashboard ->
                                                # monitoring/dashboards/northstar-dashboard.json

The committed northstar-dashboard.json is always the EXPORT (what CloudWatch
actually holds), never this script's draft.

Units worth remembering while reading the widgets:
  * ModelLatency is MICROSECONDS. The 200 ms alert line is drawn at 200000.
  * CPUUtilization is percent of ONE vCPU summed over vCPUs, so an
    ml.m5.large (2 vCPU) tops out at 200.
  * FeaturePSI alerts ABOVE 0.2; FeatureKSPValue alerts BELOW 0.05.
"""

import argparse
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from features import ENDPOINT, NAMESPACE, REGION, ROOT  # noqa: E402

NAME = "NorthStar-AI-Platform"
EXPORT = os.path.join(ROOT, "monitoring", "dashboards", "northstar-dashboard.json")
VARIANTS = ["champion", "canary"]
GLUE_JOBS = ["northstar-dev-transform", "northstar-dev-feature-engineer"]
GLUE_RULE = "northstar-glue-job-failed"          # monitoring/alerts/main.tf

# Must match DRIFT_PLAN in monitoring/evidently_drift.py.
PSI_FEATURES = ["days_since_last_purchase", "purchase_frequency_30d", "avg_order_value"]
KS_FEATURES = ["category_diversity_score", "total_spend_90d"]


def text(y, markdown):
    return {"type": "text", "x": 0, "y": y, "width": 24, "height": 1,
            "properties": {"markdown": markdown}}


def graph(x, y, w, title, metrics, stat="Average", period=300, annotations=None,
          view="timeSeries", h=6, yaxis=None):
    props = {"title": title, "region": REGION, "view": view, "stacked": False,
             "metrics": metrics, "stat": stat, "period": period}
    if annotations:
        props["annotations"] = {"horizontal": annotations}
    if yaxis:
        props["yAxis"] = yaxis
    return {"type": "metric", "x": x, "y": y, "width": w, "height": h, "properties": props}


def line(value, label, color="#d62728"):
    return {"value": value, "label": label, "color": color}


def body(endpoint: str = ENDPOINT) -> dict:
    ep = ["EndpointName", endpoint]
    widgets = []

    # ── 1. Infrastructure ────────────────────────────────────────────────────
    widgets.append(text(0, "## 1. Infrastructure - endpoint instances "
                           "(`/aws/sagemaker/Endpoints`, published as MLEngineer)"))
    widgets.append(graph(0, 1, 12, "CPU utilization (alert > 80)",
                         [["/aws/sagemaker/Endpoints", "CPUUtilization", *ep, "VariantName", v]
                          for v in VARIANTS],
                         period=60, annotations=[line(80, "P2 CPU > 80")]))
    widgets.append(graph(12, 1, 12, "Memory and disk utilization (%)",
                         [["/aws/sagemaker/Endpoints", m, *ep, "VariantName", "champion"]
                          for m in ("MemoryUtilization", "DiskUtilization")],
                         period=60))

    # ── 2. Pipeline ──────────────────────────────────────────────────────────
    widgets.append(text(7, "## 2. Pipeline - Glue jobs (`Glue` task metrics, published as "
                           "DataEngineer; failures come from the EventBridge rule, "
                           "because task metrics miss driver failures)"))
    widgets.append(graph(0, 8, 12, "Glue tasks completed / failed per job",
                         [["Glue", f"glue.driver.aggregate.{m}", "JobName", j,
                           "JobRunId", "ALL", "Type", "count"]
                          for j in GLUE_JOBS for m in ("numCompletedTasks", "numFailedTasks")],
                         stat="Sum"))
    widgets.append(graph(12, 8, 12, "Glue job FAILED / ERROR / TIMEOUT events (alert >= 1)",
                         [["AWS/Events", "TriggeredRules", "RuleName", GLUE_RULE]],
                         stat="Sum", annotations=[line(1, "P3 any failure")]))

    # ── 3. Model ─────────────────────────────────────────────────────────────
    widgets.append(text(14, "## 3. Model - data drift, Evidently processing job vs the "
                            "training baseline (`NorthStar/ChurnModel`, published from "
                            "`monitoring/publish_metrics.py`)"))
    widgets.append(graph(0, 15, 8, "PSI per feature (drift ABOVE 0.2)",
                         [[NAMESPACE, "FeaturePSI", *ep, "Feature", f] for f in PSI_FEATURES],
                         stat="Maximum", annotations=[line(0.2, "PSI > 0.2 = drift")]))
    widgets.append(graph(8, 15, 8, "KS p-value per feature (drift BELOW 0.05)",
                         [[NAMESPACE, "FeatureKSPValue", *ep, "Feature", f] for f in KS_FEATURES],
                         stat="Minimum", annotations=[line(0.05, "p < 0.05 = drift")]))
    widgets.append(graph(16, 15, 8, "Drift detected (0/1) and violation count",
                         [[NAMESPACE, "DriftDetected", *ep],
                          [NAMESPACE, "DriftViolationCount", *ep]],
                         stat="Maximum", annotations=[line(1, "P3 drift")]))

    # ── 4. Application ───────────────────────────────────────────────────────
    widgets.append(text(21, "## 4. Application - inference latency and errors "
                            "(`AWS/SageMaker`; ModelLatency is in MICROSECONDS)"))
    widgets.append(graph(0, 22, 12, "ModelLatency p50 / p95 / p99, champion (microseconds)",
                         [["AWS/SageMaker", "ModelLatency", *ep, "VariantName", "champion",
                           {"stat": s, "label": s}] for s in ("p50", "p95", "p99")],
                         stat="p95", period=60,
                         annotations=[line(200000, "P2 p95 > 200 ms (200000 us)", "#ff7f0e"),
                                      line(20000, "SLO p95 20 ms (20000 us)", "#2ca02c")]))
    widgets.append(graph(12, 22, 12, "Invocations and errors per variant",
                         [["AWS/SageMaker", m, *ep, "VariantName", v]
                          for v in VARIANTS
                          for m in ("Invocations", "Invocation5XXErrors", "Invocation4XXErrors")],
                         stat="Sum", period=60))

    # ── 5. Business ──────────────────────────────────────────────────────────
    widgets.append(text(28, "## 5. Business - churn alert volume, the proxy for offers sent "
                            "(`NorthStar/ChurnModel`)"))
    widgets.append(graph(0, 29, 12, "Churn alert volume (predictions >= alert score)",
                         [[NAMESPACE, "ChurnAlertVolume", *ep]], stat="Sum", period=3600))
    widgets.append(graph(12, 29, 12, "Volume vs trailing 7-day average (alert < 0.7)",
                         [[NAMESPACE, "ChurnAlertVolumeRatio", *ep]], stat="Minimum",
                         period=3600, annotations=[line(0.7, "P3 drop > 30%")],
                         yaxis={"left": {"min": 0}}))

    return {"start": "-PT12H", "periodOverride": "inherit", "widgets": widgets}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--put", action="store_true", help="put-dashboard")
    ap.add_argument("--export", action="store_true",
                    help="get-dashboard and write northstar-dashboard.json")
    args = ap.parse_args()

    if not (args.put or args.export):
        print(json.dumps(body(args.endpoint), indent=2))
        return 0

    cw = boto3.Session(region_name=REGION).client("cloudwatch")
    if args.put:
        r = cw.put_dashboard(DashboardName=NAME, DashboardBody=json.dumps(body(args.endpoint)))
        problems = r.get("DashboardValidationMessages", [])
        for p in problems:
            print("VALIDATION:", json.dumps(p))
        print(f"put {NAME}: {len(body(args.endpoint)['widgets'])} widgets, "
              f"{len(problems)} validation message(s)")
        if problems:
            return 1
    if args.export:
        live = json.loads(cw.get_dashboard(DashboardName=NAME)["DashboardBody"])
        with open(EXPORT, "w", newline="\n") as fh:
            json.dump(live, fh, indent=2)
            fh.write("\n")
        print(f"exported {NAME} -> {os.path.relpath(EXPORT, ROOT)} "
              f"({len(live['widgets'])} widgets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
