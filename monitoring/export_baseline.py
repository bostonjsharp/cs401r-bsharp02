"""
NorthStar - export the drift baseline. Lab 6 Task 1.

Writes monitoring/baseline/baseline.csv: a header row plus one row per
TRAINING customer, with exactly the 11 features the endpoint receives, in
FEATURE_COLUMNS order. No customer_id, no loyalty_tier, no churn_label, no
churn_risk_score: the baseline describes model INPUTS and nothing else.

"Training customer" means the 70% side of the Lab 3/4 split (see
monitoring/features.py). The 30% the model was validated on is deliberately
left out, so invoke_traffic.py can send it as traffic the baseline has never
seen.

    python monitoring/export_baseline.py              # read S3, write CSV, upload
    python monitoring/export_baseline.py --features-dir .dryrun/features --no-upload
"""

import argparse
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import FEATURE_COLUMNS, REGION, ROOT, data_bucket, load_split  # noqa: E402

OUT = os.path.join(ROOT, "monitoring", "baseline", "baseline.csv")
S3_KEY = "monitoring/baseline/baseline.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features-dir", default=None,
                    help="Local directory of feature parquet. Default: download "
                         "s3://<bucket>/features/customers/.")
    ap.add_argument("--no-upload", action="store_true",
                    help="Write the CSV only; do not put it in S3.")
    args = ap.parse_args()

    session = None if (args.features_dir and args.no_upload) else boto3.Session(region_name=REGION)
    train, val = load_split(session, args.features_dir)

    baseline = train[FEATURE_COLUMNS].astype(float)
    if baseline.isna().any().any():
        sys.exit("FATAL: baseline has nulls; the Glue job asserts there are none.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # repr-precision floats so a value in the baseline and the same value sent
    # as a payload compare equal after a CSV round trip.
    baseline.to_csv(OUT, index=False, lineterminator="\n")
    print(f"baseline : {len(baseline):,} training customers x {len(FEATURE_COLUMNS)} features")
    print(f"held out : {len(val):,} validation customers (traffic source)")
    print(f"wrote    : {os.path.relpath(OUT, ROOT)}")

    if not args.no_upload:
        bucket = data_bucket(session)
        session.client("s3").upload_file(OUT, bucket, S3_KEY)
        print(f"uploaded : s3://{bucket}/{S3_KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
