"""
NorthStar - send monitored traffic to the endpoint. Lab 6 Task 1.

Sends N DISTINCT validation customers (the 30% the model never trained on,
see monitoring/features.py), ONE ROW PER REQUEST. Both details matter:

  * one row per request: a multi-row body is captured as a single string, so
    200 batched predictions become one row in the comparison window;
  * distinct rows: the drift job drops byte-identical payloads, so repeats
    add nothing to the window.

The endpoint splits traffic 9:1, and capture is partitioned per variant. The
drift job reads the champion prefix and needs >= 500 rows there, so the
default of 800 leaves about 720 on champion.

Writes monitoring/reports/traffic_summary.json, which publish_metrics.py reads
for the business-layer metric (how many predictions crossed the churn-alert
threshold).

    python monitoring/invoke_traffic.py --n 800
    python monitoring/invoke_traffic.py --n 5 --dry-run --features-dir .dryrun/features
"""

import argparse
import collections
import datetime as dt
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import ENDPOINT, FEATURE_COLUMNS, REGION, ROOT, load_split  # noqa: E402

SUMMARY = os.path.join(ROOT, "monitoring", "reports", "traffic_summary.json")

# A prediction at or above this score is a "churn alert": the customer would
# be handed to the offer generator. 0.5 is the natural cut for a model trained
# with scale_pos_weight (scores are balanced around it, not around the 22%
# base rate).
ALERT_THRESHOLD = 0.5


def payload(row) -> bytes:
    """One CSV line, no header, repr floats: '14.0,332.0,...'."""
    return (",".join(repr(float(row[c])) for c in FEATURE_COLUMNS) + "\n").encode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--features-dir", default=None,
                    help="Local feature parquet. Default: read from S3.")
    ap.add_argument("--alert-threshold", type=float, default=ALERT_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the payloads and print the first; call nothing.")
    args = ap.parse_args()

    session = None if (args.features_dir and args.dry_run) else boto3.Session(region_name=REGION)
    _, val = load_split(session, args.features_dir)

    rows = val[FEATURE_COLUMNS].astype(float).drop_duplicates().reset_index(drop=True)
    if len(rows) < args.n:
        sys.exit(f"FATAL: only {len(rows)} distinct validation rows, asked for {args.n}")
    rows = rows.iloc[:args.n]
    print(f"{len(rows)} distinct validation rows, one request each -> {args.endpoint}")

    if args.dry_run:
        print("first payload:", payload(rows.iloc[0]).decode().strip())
        return 0

    rt = session.client("sagemaker-runtime")
    per_variant = collections.Counter()
    alerts = collections.Counter()
    scores, errors = [], 0
    started = dt.datetime.now(dt.timezone.utc)
    for i, (_, row) in enumerate(rows.iterrows(), 1):
        try:
            r = rt.invoke_endpoint(EndpointName=args.endpoint, ContentType="text/csv",
                                   Body=payload(row))
            variant = r["InvokedProductionVariant"]
            score = float(r["Body"].read().decode().strip().split(",")[0])
        except Exception as exc:  # keep going: one failure must not waste the run
            errors += 1
            print(f"  request {i} failed: {exc}")
            continue
        per_variant[variant] += 1
        scores.append(score)
        if score >= args.alert_threshold:
            alerts[variant] += 1
        if i % 100 == 0:
            print(f"  {i} sent  {dict(per_variant)}")
    finished = dt.datetime.now(dt.timezone.utc)

    summary = {
        "endpoint": args.endpoint,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_utc": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requests_sent": len(rows),
        "errors": errors,
        "per_variant": dict(per_variant),
        "alert_threshold": args.alert_threshold,
        "churn_alerts": sum(alerts.values()),
        "churn_alerts_per_variant": dict(alerts),
        "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
    }
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    with open(SUMMARY, "w", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(json.dumps(summary, indent=2))

    champion = per_variant.get("champion", 0)
    if champion < 500:
        print(f"WARNING: only {champion} requests reached champion; the drift job "
              "needs >= 500 there. Send more (use a larger --n; rows must be new).")
    print("Capture is written asynchronously. Wait ~5 minutes before the drift job.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
