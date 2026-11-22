"""
NorthStar - shared laptop-side helpers for Lab 6 monitoring.

Imported by export_baseline.py, invoke_traffic.py and publish_metrics.py.
NOT imported by evidently_drift.py: that file is uploaded to S3 on its own and
runs alone inside the processing container, so it carries its own copy of
FEATURE_COLUMNS. monitoring/tests/test_drift_direction.py asserts the two
lists are identical.

The point of this module is one question: which customers did the model
train on? Lab 3/4 split the feature table 70/30 with
train_test_split(random_state=42, stratify=churn_label) after sorting by
customer_id (models/churn/churn_model.py, models/churn/train_sagemaker.py).
The same call on the same rows reproduces the same split, so

    train side (6,999 rows)  -> the drift BASELINE (what the model learned from)
    val side   (3,000 rows)  -> clean TRAFFIC the model never trained on
"""

import glob
import os
import sys
import tempfile

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Single source of truth for the training-side constants.
from models.churn.churn_model import FEATURE_COLUMNS, LABEL, TEST_SIZE  # noqa: E402

REGION = "us-east-1"
ENDPOINT = "northstar-churn-prod"
NAMESPACE = "NorthStar/ChurnModel"
FEATURES_PREFIX = "features/customers/"
SPLIT_SEED = 42


def data_bucket(session) -> str:
    account = session.client("sts").get_caller_identity()["Account"]
    return f"northstar-dev-data-{account}"


def download_features(session, bucket: str, dest: str) -> str:
    """Copy every parquet part under features/customers/ into dest."""
    s3 = session.client("s3")
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=FEATURES_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                s3.download_file(bucket, obj["Key"],
                                 os.path.join(dest, os.path.basename(obj["Key"])))
                n += 1
    if n == 0:
        sys.exit(f"FATAL: no parquet under s3://{bucket}/{FEATURES_PREFIX}. "
                 "Run scripts/run-lab2-pipeline.sh first.")
    return dest


def read_feature_table(directory: str) -> pd.DataFrame:
    """Same read, dedup and ordering as train_sagemaker.read_channel().

    Sorting the part files and then the rows by customer_id is load-bearing:
    the row order reaching train_test_split decides who lands on which side.
    """
    parts = sorted(glob.glob(os.path.join(directory, "**", "*.parquet"), recursive=True))
    if not parts:
        sys.exit(f"FATAL: no .parquet files under {directory}")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    if "event_time" in df.columns:
        df = df.sort_values(["customer_id", "event_time"]).drop_duplicates(
            subset=["customer_id"], keep="last")
    else:
        df = df.drop_duplicates(subset=["customer_id"], keep="last")
    df = df.sort_values("customer_id").reset_index(drop=True)
    return df.dropna(subset=[LABEL]).reset_index(drop=True)


def split_train_val(df: pd.DataFrame):
    """Return (train_df, val_df) exactly as churn_model.train() split them.

    The split is a function of n, the labels, the seed and the test size only,
    so splitting row positions gives the same partition as splitting X.
    """
    from sklearn.model_selection import train_test_split

    y = df[LABEL].to_numpy().astype(int)
    train_idx, val_idx = train_test_split(
        list(range(len(df))), test_size=TEST_SIZE, random_state=SPLIT_SEED, stratify=y)
    return df.iloc[train_idx].reset_index(drop=True), df.iloc[val_idx].reset_index(drop=True)


def load_split(session=None, features_dir: str | None = None):
    """(train_df, val_df) from a local directory, or from S3 when none is given."""
    if features_dir:
        return split_train_val(read_feature_table(features_dir))
    with tempfile.TemporaryDirectory() as tmp:
        download_features(session, data_bucket(session), tmp)
        return split_train_val(read_feature_table(tmp))
