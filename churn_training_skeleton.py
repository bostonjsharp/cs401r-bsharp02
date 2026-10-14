"""
NorthStar Retail — Churn Prediction Training Script (Skeleton)
CS 401R Lab 3 Starter Kit

This script is the SageMaker XGBoost training entry point for the NorthStar
churn prediction model. It reads features from the SageMaker Feature Store,
trains an XGBoost classifier, tracks the run in a SageMaker MLflow App, and
registers the model in the Model Registry.

Experiment tracking needs two packages that are NOT in the training container:

    pip install mlflow sagemaker-mlflow

`sagemaker-mlflow` is the SigV4 auth plugin for arn:aws:sagemaker:... tracking
URIs. Install `mlflow` alone and you get a connection error that never mentions
credentials. See track_run() for the cost warning about the OTHER MLflow
product -- read it before you create anything.

Usage (local testing):
    python churn_training_skeleton.py \
        --feature-group-name northstar-dev-customer-features \
        --artifacts-bucket northstar-dev-data-<account> \
        --max-depth 6 --eta 0.1 --num-round 200 \
        --mlflow-arn arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX \
        --run-name xgb-depth6-eta0.1

Usage (SageMaker training job — parameters passed via hyperparameter dict):
    Configured via the SageMaker Python SDK Estimator.
"""

import argparse
import io
import json
import os
import sys
import tarfile
import time
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd

# The kit's original Feature Store path used the `sagemaker` SDK's
# FeatureGroup.athena_query(), which sagemaker 3.x removed (defect 55). Rather
# than pin an old SDK for one helper, load_features_from_feature_store() below
# talks to SageMaker and Athena through boto3 directly: describe the feature
# group to find its Glue table, run the query, download the CSV result. Same
# query, same result set, two fewer packages. The SDK is not installed here.

try:
    import xgboost as xgb
except ImportError:
    raise ImportError("xgboost is required: pip install xgboost")

from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split

# ── Argument Parsing ───────────────────────────────────────────────────────────
# SageMaker passes hyperparameters as CLI arguments.

def parse_args():
    parser = argparse.ArgumentParser(description="NorthStar churn prediction training")

    # Data configuration
    # Names come from Lab 2's Terraform: `terraform output -raw feature_group_name`
    # and `terraform output -raw s3_bucket_name`. Do not invent new ones.
    parser.add_argument("--feature-group-name", type=str,
                        default="northstar-dev-customer-features",
                        help="SageMaker Feature Store feature group name (Lab 2 output)")
    # These dates bound the INGEST batch, not the feature window. Lab 2 stamps
    # event_time with the wall-clock time its Glue job ran (time.time()), not
    # with the feature cutoff T = 2026-04-01 -- T was already enforced when the
    # features were computed. A window that ends before your Lab 2 run (the old
    # default here was 2026-06-01) matches zero rows. These defaults match the
    # reference implementation and cover any Lab 2 run during the semester.
    parser.add_argument("--training-start-date", type=str, default="2026-01-01",
                        help="Earliest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--training-end-date", type=str, default="2027-01-01",
                        help="Latest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--artifacts-bucket", type=str, required=True,
                        help="Your Lab 1 data bucket, northstar-dev-data-<account>. "
                             "Athena results and model artifacts go under its artifacts/ "
                             "prefix, the only prefix your MLEngineer role can write")
    parser.add_argument("--local-data-path", type=str, default=None,
                        help="Path to local feature CSV for development testing (bypasses Feature Store)")

    # XGBoost hyperparameters
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--eta", type=float, default=0.1)
    parser.add_argument("--num-round", type=int, default=200)
    parser.add_argument("--min-child-weight", type=int, default=5)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--scale-pos-weight", type=float, default=None,
                        help=(
                            "XGBoost positive-class weight. Leave unset and it is computed "
                            "from YOUR training split as negatives/positives, which is what "
                            "you want. The reference 10,000-customer dataset is 22.0%% "
                            "churners, giving (1 - 0.220) / 0.220 = 3.545. Passing a value "
                            "tuned for a different class balance will quietly skew your "
                            "precision/recall trade-off, so only override this deliberately."
                        ))
    # Experiment tracking (Task 1, 5 points)
    parser.add_argument("--mlflow-arn", type=str,
                        default=os.environ.get("MLFLOW_APP_ARN"),
                        help=(
                            "ARN of your SageMaker MLflow APP, e.g. "
                            "arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX. "
                            "Create it once with `aws sagemaker create-mlflow-app`. "
                            "This is NOT an MLflow Tracking Server -- see the warning "
                            "on track_run() below. Omit to skip tracking."
                        ))
    parser.add_argument("--mlflow-experiment", type=str, default="northstar-churn",
                        help="MLflow experiment name; runs group under this")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Name for this run. Make it descriptive -- 'run3' tells "
                             "you nothing three weeks later.")
    # Feature-set switch, so the "with and without churn_risk_score" comparison
    # the lab asks for is a flag between two tracked runs, not a code edit.
    parser.add_argument("--include-risk-score", action="store_true",
                        help=f"Add {BASELINE_COLUMN} to the feature set. It is a pure "
                             "recency heuristic (the baseline you must beat), so Lab 3 "
                             "requires reporting results with AND without it.")
    # Registration is opt-in and meant for exactly one run: the final graded
    # one. The evidence collector requires the registered version, one MLflow
    # run and docs/lab3-evidence/evaluation_metrics.json to carry the same
    # numbers, which is easiest to guarantee when only one run registers.
    parser.add_argument("--register", action="store_true",
                        help="Upload model.tar.gz to artifacts/models/ and register a "
                             "PendingManualApproval version in northstar-churn-models. "
                             "Off by default; set it on the final graded run only.")

    parser.add_argument("--model-dir", type=str,
                        default=os.environ.get("SM_MODEL_DIR", "./model"))
    parser.add_argument("--output-data-dir", type=str,
                        default=os.environ.get("SM_OUTPUT_DATA_DIR", "./output"))

    return parser.parse_args()


# ── Feature Store Loading ──────────────────────────────────────────────────────

# These are exactly the features your Lab 2 pipeline wrote to the Feature
# Group. If a name here does not match your feature group, the Athena query
# below will fail - check `aws sagemaker describe-feature-group` first.
#
# Note what is NOT in this list: churn_label (the target) and customer_id
# (an identifier, not a signal). Including either as an input is leakage.
FEATURE_COLUMNS = [
    # Recency and tenure
    "days_since_last_purchase",
    "customer_tenure_days",
    # Frequency
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    # Monetary
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    # Behavioural - these are the features that catch churners who still
    # look active on recency alone. Drop them and your model collapses
    # toward the recency-only baseline.
    "category_diversity_score",
    "online_to_store_ratio",
]

# churn_risk_score is available in the feature group but deliberately excluded
# here: it is a pure recency heuristic and doubles as the baseline you must
# beat. If you add it, report your metrics both with and without it.
# (--include-risk-score appends it to FEATURE_COLUMNS at startup for exactly
# that with/without comparison.)
BASELINE_COLUMN = "churn_risk_score"

# SageMaker's managed XGBoost inference image for us-east-1 (the account ID in
# the registry path is region-specific). Lab 5 deploys from this spec. The
# container is XGBoost 1.7; a model saved by 3.x in JSON format is the most
# likely to load there, which Lab 5 will verify.
XGBOOST_INFERENCE_IMAGE = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"

# Exact name required: Lab 5 deploys from this group by name.
MODEL_PACKAGE_GROUP = "northstar-churn-models"

# The recency-only baseline model uses just this one feature. Task 1 requires
# you to train it and show your full model beats it by a margin whose 95%
# confidence interval excludes zero. There is no fixed AUC-lift threshold: a
# ">= 0.03 lift" gate lived here until 2026-08-02 and was removed because 0.03
# is smaller than the metric's own run-to-run standard deviation, so it failed
# on 21% of splits regardless of model quality. See EVAL_THRESHOLDS below.
BASELINE_FEATURE = "days_since_last_purchase"

LABEL_COLUMN = "churn_label"

# Not a feature. This is the column Task 1's slice evaluation groups by, so it
# has to come back from the query even though it never enters the model. Keep it
# out of FEATURE_COLUMNS: loyalty_tier correlates with spend, and feeding it in
# both leaks and makes the per-tier fairness check circular.
SLICE_COLUMN = "loyalty_tier"


def load_features_from_feature_store(feature_group_name: str,
                                     start_date: str,
                                     end_date: str,
                                     artifacts_bucket: str) -> pd.DataFrame:
    """
    Load features from the SageMaker Feature Store offline store using Athena.

    Args:
        feature_group_name: your Lab 2 feature group
        start_date, end_date: ISO dates (YYYY-MM-DD) bounding the event_time range
        artifacts_bucket: S3 bucket that Athena writes its result set to. Athena
            will not run a query without an output location, so this has to be
            threaded in from --artifacts-bucket rather than assumed.

    Implementation (plain boto3, no sagemaker SDK):
      1. sagemaker.describe_feature_group -> OfflineStoreConfig.DataCatalogConfig
         gives the Glue database + table the offline store is catalogued as.
      2. athena.start_query_execution with that database and an OutputLocation
         under artifacts/ (Athena refuses to run without one).
      3. Poll get_query_execution until SUCCEEDED/FAILED/CANCELLED.
      4. Print the QueryExecutionId (the evidence collector looks it up) and
         download the result CSV Athena wrote to OutputLocation.
    """
    # event_time was written as Fractional (epoch seconds) by the Lab 2 job, so
    # the date bounds must be converted before comparison. Passing ISO strings
    # straight into the query fails with TYPE_MISMATCH rather than returning
    # rows, so at least the failure is loud.
    start_epoch = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    end_epoch = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    sm = boto3.client("sagemaker")
    athena = boto3.client("athena")
    s3 = boto3.client("s3")

    fg = sm.describe_feature_group(FeatureGroupName=feature_group_name)
    catalog = fg["OfflineStoreConfig"]["DataCatalogConfig"]
    database, table_name = catalog["Database"], catalog["TableName"]
    output_location = f"s3://{artifacts_bucket}/artifacts/athena-results/"
    print(f"Loading features from Feature Store offline store via Athena: "
          f"{database}.{table_name}")

    # The query itself is the kit's, verbatim. Three things in it are easy to
    # get wrong, so read them:
    #
    # 1. event_time is Fractional - Unix epoch SECONDS, not an ISO string.
    #    Comparing it to '2026-04-01' fails the query outright with:
    #      TYPE_MISMATCH: Cannot check if double is BETWEEN varchar(10)...
    #    Convert your date bounds to epoch seconds before substituting them.
    #
    # 2. The offline store is append-only. Re-running the Lab 2 feature job
    #    writes a SECOND record for every customer, and a naive SELECT then
    #    returns duplicate customers with stale feature values. Deduplicate
    #    with a window function keyed on customer_id, keeping the most recent
    #    event_time. Filtering on a global MAX(write_time) does NOT work -
    #    it keeps only the customers written in the final microsecond.
    #
    #    The offline store also soft-deletes: rows carry is_deleted, and
    #    deleted records must be excluded.
    #
    # 3. The outer SELECT must ORDER BY customer_id, and this is the one people
    #    skip because it looks cosmetic. It is not. Athena parallelises the scan
    #    across the offline store's Parquet objects and returns rows in whatever
    #    order the splits happen to finish, which varies from run to run.
    #    train_test_split(random_state=42) is deterministic only for a GIVEN row
    #    order -- so without ORDER BY, the identical data produces a different
    #    train/test split, and therefore different metrics, on every single run.
    #
    #    This was measured, not theorised: four runs on byte-identical data
    #    produced AUC between 0.7276 and 0.7431, and a Platinum-slice AUC
    #    between 0.430 and 0.700. An entire "the model is worse than random on
    #    your best customers" finding turned out to be an artefact of row order.
    #
    #    The rn = 1 filter guarantees customer_id is unique here, so ordering on
    #    it is a TOTAL order and the pipeline becomes reproducible.
    #
    #    If your metrics move between runs and your data did not, this is why.

    #    loyalty_tier is selected but is NOT in FEATURE_COLUMNS, and that is
    #    deliberate. It is not an input to the model - it is the column Task 1's
    #    slice evaluation groups by. Keep it out of the feature matrix (see
    #    preprocess_features, which selects FEATURE_COLUMNS explicitly) and
    #    carry it alongside X and y through the split so each row's prediction
    #    can still be attributed to a tier:
    #
    #        X_train, X_val, y_train, y_val, tier_train, tier_val = \
    #            train_test_split(X, y, df[SLICE_COLUMN], test_size=0.30,
    #                             random_state=42, stratify=y)
    #
    #    Split X and y without it and you cannot line the tiers back up.

    query_string = f"""
        WITH ranked AS (
            SELECT
                customer_id,
                {", ".join(FEATURE_COLUMNS)},
                {LABEL_COLUMN},
                {SLICE_COLUMN},
                event_time,
                ROW_NUMBER() OVER (
                    PARTITION BY customer_id
                    ORDER BY event_time DESC, write_time DESC
                ) AS rn
            FROM "{database}"."{table_name}"
            WHERE event_time BETWEEN {start_epoch} AND {end_epoch}
              AND NOT is_deleted
        )
        SELECT
            customer_id,
            {", ".join(FEATURE_COLUMNS)},
            {LABEL_COLUMN},
            {SLICE_COLUMN}
        FROM ranked
        WHERE rn = 1
        ORDER BY customer_id
    """

    qid = athena.start_query_execution(
        QueryString=query_string,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
    )["QueryExecutionId"]

    # Poll. Athena is asynchronous; a few-MB scan finishes in seconds.
    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = status["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        raise RuntimeError(f"Athena query {qid} ended {state}: "
                           f"{status.get('StateChangeReason', 'no reason given')}")

    # The evidence collector regexes exactly this line out of train-final.log
    # and exports the query Athena recorded under this ID. Keep the prefix.
    print(f"Athena QueryExecutionId: {qid}")

    # Athena writes the full result set as <OutputLocation>/<qid>.csv. Reading
    # that one object is simpler than paginating get_query_results (1,000 rows
    # a page) and gives pandas the whole table at once.
    bucket, key_prefix = output_location[len("s3://"):].split("/", 1)
    obj = s3.get_object(Bucket=bucket, Key=f"{key_prefix}{qid}.csv")
    df = pd.read_csv(io.BytesIO(obj["Body"].read()))
    print(f"  {len(df):,} rows x {df.shape[1]} columns from Athena")
    return df


def load_features_local(path: str) -> pd.DataFrame:
    """Load features from a local CSV file (development mode)."""
    print(f"Loading features from local file: {path}")
    df = pd.read_csv(path)
    missing = [c for c in FEATURE_COLUMNS + [LABEL_COLUMN] if c not in df.columns]
    if missing:
        raise ValueError(f"Local data missing required columns: {missing}")
    # Not fatal - the model trains fine without it - but Task 1's slice
    # evaluation cannot be done at all, so fail the task rather than the run.
    if SLICE_COLUMN not in df.columns:
        print(f"WARNING: '{SLICE_COLUMN}' not in local data — Task 1 slice "
              f"evaluation will not be possible with this file")
    return df


# ── Feature Preprocessing ──────────────────────────────────────────────────────

def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """
    Clean and prepare features for XGBoost training.
    Returns (X, y, tiers): the feature matrix, the integer label, and the
    loyalty_tier column aligned row-for-row with X and y. The tier is returned
    from here (not re-derived from df later) so the one dropna below cannot
    put X and the tiers out of step.

    Missing-value strategy: NaN is passed through to XGBoost unchanged.
    XGBoost learns a default branch direction for missing values at every
    split, which is strictly better than a sentinel: a -1 in
    days_since_last_purchase would be read by the trees as "bought yesterday",
    the opposite of what an unknown recency means. Athena returns NULL for
    missing Feature Store values and pandas reads it as NaN, so nothing else
    is needed. The Lab 2 feature table has zero nulls anyway (verified on the
    dry run and asserted below as a loud report, not a silent fill).
    """
    print(f"Preprocessing {len(df):,} samples...")

    # Drop rows where label is missing (none expected; the label is Integral in
    # the Feature Group, but the Athena CSV would carry an empty cell if so).
    df = df.dropna(subset=[LABEL_COLUMN]).reset_index(drop=True)

    null_counts = df[FEATURE_COLUMNS].isna().sum()
    if null_counts.sum():
        print("  Nulls per feature (left as NaN for XGBoost's native handling):")
        for col, n in null_counts[null_counts > 0].items():
            print(f"    {col}: {n}")
    else:
        print("  No missing feature values")

    X = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = df[LABEL_COLUMN].to_numpy().astype(int)
    tiers = df[SLICE_COLUMN].astype(str).reset_index(drop=True)

    print(f"  Positive class rate: {y.mean():.1%}")
    print(f"  Feature matrix shape: {X.shape}")

    return X, y, tiers


# ── Model Training ─────────────────────────────────────────────────────────────

def train_model(X_train: np.ndarray,
                y_train: np.ndarray,
                X_val: np.ndarray,
                y_val: np.ndarray,
                args) -> xgb.Booster:
    """Train the XGBoost churn model."""

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)

    # Class imbalance, computed from the training split rather than hardcoded.
    # On the reference dataset this lands at 3.545.
    spw = (args.scale_pos_weight if args.scale_pos_weight is not None
           else float((y_train == 0).sum() / max((y_train == 1).sum(), 1)))

    # Record the RESOLVED value so track_run() can log what was actually used.
    # Logging args.scale_pos_weight instead would log None on every run that
    # let it compute itself -- which is most of them -- and your MLflow
    # comparison would be missing the one parameter most likely to explain a
    # precision/recall difference between runs.
    args.resolved_scale_pos_weight = spw

    params = {
        # eval_metric ORDER MATTERS. XGBoost early-stops on the LAST metric in
        # this list, not the first. This used to read ["auc", "logloss"], which
        # silently made logloss the early-stopping criterion: under
        # scale_pos_weight the reweighted logloss keeps improving long after
        # ranking quality peaks, so training ran the full 200 rounds instead of
        # stopping near round 31 and validation AUC came out 0.7603 instead of
        # 0.7822. Measured across 50 splits, that cost the model the promotion
        # gate on 28% of them -- a student doing everything right was told their
        # features did not beat recency. Keep "auc" last (defect 49).
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "max_depth": args.max_depth,
        "eta": args.eta,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": spw,
        "seed": 42,
        "verbosity": 1,
    }

    print(f"\nTraining XGBoost with params: {json.dumps(params, indent=2)}")

    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_round,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=20,
        verbose_eval=25,
    )

    return model


# ── Evaluation ─────────────────────────────────────────────────────────────────

EVAL_THRESHOLDS = {
    "precision_top10":   0.50,   # Minimum precision @ top 10% scored customers
    "recall_top10":      0.25,   # Minimum recall @ top 10% scored customers
    # Note the recall ceiling: with ~22% positives, targeting the top 10% of
    # customers caps achievable recall near 0.45. Hitting 0.25 means capturing
    # roughly half of what is reachable within that contact budget.
}

# There is deliberately NO absolute AUC threshold. One used to live here
# (auc_roc >= 0.72) and it was removed on 2026-08-02: measured across 200
# random train/test splits of the same data, the reference model fell below
# 0.72 on 58% of them. A gate the reference clears by luck of the shuffle
# grades your random seed, not your model. Report AUC; do not gate on it.
#
# The baseline gate is an INTERVAL, not a threshold: the 95% CI on
# (your AUC - recency-only baseline AUC) must exclude zero.
# BASELINE_FEATURE is defined once, near FEATURE_COLUMNS above.
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42


def train_recency_baseline(X_train: np.ndarray,
                           y_train: np.ndarray,
                           X_val: np.ndarray) -> np.ndarray:
    """Train the recency-only baseline and score the validation split.

    The baseline is a model over `days_since_last_purchase` ALONE, trained on
    the same training rows and scored on the same validation rows as the full
    model. That is what makes the comparison fair.

    It is deliberately NOT a constant predictor. A constant prediction has an
    AUC of exactly 0.5 by construction, so "beating" it proves only that your
    model is better than a coin flip. The question Lab 3 asks is whether your
    feature engineering beats the rule the business already has for free:
    "contact whoever has not purchased in a while."
    """
    i = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtr = xgb.DMatrix(X_train[:, [i]], label=y_train, feature_names=[BASELINE_FEATURE])
    dva = xgb.DMatrix(X_val[:, [i]], feature_names=[BASELINE_FEATURE])
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": "binary:logistic", "eval_metric": "auc",
        "max_depth": 4, "eta": 0.1, "subsample": 0.9,
        "colsample_bytree": 0.9, "scale_pos_weight": spw, "seed": 42,
    }
    return xgb.train(params, dtr, num_boost_round=200).predict(dva)


def bootstrap_lift_ci(y_true: np.ndarray,
                      proba: np.ndarray,
                      baseline_proba: np.ndarray,
                      n: int = BOOTSTRAP_N,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI for (model AUC - baseline AUC), by resampling the val set.

    PAIRED: each replicate resamples row indices once and scores BOTH models on
    those same rows, so the interval is on the difference and the correlation
    between the two models is preserved. Resampling them independently breaks
    the pairing and inflates the interval, which would make a real improvement
    look inconclusive.

    Replicates whose resample happens to be single-class are skipped, because
    AUC is undefined there.

    The seed is fixed so the gate is reproducible: the same split must always
    produce the same verdict.
    """
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    idx = np.arange(len(y_true))
    diffs = []
    for _ in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        ys = y_true[s]
        if len(np.unique(ys)) < 2:
            continue
        diffs.append(roc_auc_score(ys, proba[s]) - roc_auc_score(ys, baseline_proba[s]))
    if len(diffs) < n // 2:
        raise ValueError(
            "Too few usable bootstrap replicates to form a confidence interval. "
            "Your validation set is too small or too imbalanced to support this gate."
        )
    d = np.sort(diffs)
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


def evaluate_model(model: xgb.Booster,
                   X_val: np.ndarray,
                   y_val: np.ndarray,
                   baseline_proba: np.ndarray) -> dict:
    """
    Evaluate the trained model against required thresholds.
    Returns a metrics dict. Raises ValueError if thresholds are not met.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)
    y_pred_binary = (y_pred_proba > 0.5).astype(int)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

    # Precision and recall @ top 10%
    top10_threshold = np.percentile(y_pred_proba, 90)
    top10_mask = y_pred_proba >= top10_threshold
    precision_top10 = y_val[top10_mask].mean() if top10_mask.sum() > 0 else 0.0
    recall_top10 = y_val[top10_mask].sum() / y_val.sum() if y_val.sum() > 0 else 0.0

    # Baseline: a model trained on days_since_last_purchase ALONE.
    #
    # This used to be `np.full_like(y_pred_proba, y_val.mean())` - a constant
    # prediction, whose AUC is exactly 0.5 by definition. Beating a coin flip
    # is not evidence that your feature engineering did anything, and it is not
    # what Lab 3 asks for. The comparison that matters is against recency,
    # because recency is the rule the business already has for free.
    #
    baseline_auc = roc_auc_score(y_val, baseline_proba)
    auc_vs_baseline = auc_roc - baseline_auc

    lift_ci_low, lift_ci_high = bootstrap_lift_ci(
        y_val, y_pred_proba, baseline_proba)

    # Confusion matrix at the BUSINESS threshold: the top-10% score cutoff,
    # i.e. "contact the riskiest 10% of customers". XGBoost's 0.5 default is
    # meaningless here because scale_pos_weight shifts the whole score scale.
    tn, fp, fn, tp = confusion_matrix(y_val, top10_mask.astype(int), labels=[0, 1]).ravel()

    metrics = {
        "auc_roc": round(float(auc_roc), 4),
        "precision_top10": round(float(precision_top10), 4),
        "recall_top10": round(float(recall_top10), 4),
        "auc_vs_baseline": round(float(auc_vs_baseline), 4),
        "baseline_auc": round(float(baseline_auc), 4),
        "lift_ci_low": round(float(lift_ci_low), 4),
        "lift_ci_high": round(float(lift_ci_high), 4),
        "positive_rate_val": round(float(y_val.mean()), 4),
        "n_val_samples": int(len(y_val)),
        "threshold_top10": round(float(top10_threshold), 4),
        "cm_tn": int(tn), "cm_fp": int(fp), "cm_fn": int(fn), "cm_tp": int(tp),
        "best_iteration": int(getattr(model, "best_iteration", -1)),
        "n_features": len(FEATURE_COLUMNS),
        "xgboost_version": xgb.__version__,
        "eval_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    print("\n── Evaluation Results ──────────────────────────────")
    failures = []
    for metric, threshold in EVAL_THRESHOLDS.items():
        value = metrics[metric]
        status = "✓ PASS" if value >= threshold else "✗ FAIL"
        print(f"  {metric:25s}: {value:.4f}  (threshold: {threshold:.4f})  {status}")
        if value < threshold:
            failures.append(f"{metric}={value:.4f} < {threshold}")

    # The baseline gate: the interval must exclude zero.
    ci_status = "\u2713 PASS" if lift_ci_low > 0 else "\u2717 FAIL"
    print(f"  {'auc_lift 95% CI':25s}: [{lift_ci_low:.4f}, {lift_ci_high:.4f}]"
          f"  (must exclude 0)  {ci_status}")
    if lift_ci_low <= 0:
        failures.append(
            f"auc_lift 95% CI [{lift_ci_low:.4f}, {lift_ci_high:.4f}] includes "
            f"zero - no evidence the model beats the recency-only baseline")

    if failures:
        raise ValueError(
            f"Model failed evaluation thresholds: {'; '.join(failures)}. "
            f"Do not promote to Model Registry."
        )

    print("  All thresholds passed ✓")
    print(f"  Confusion matrix @ top-10% cutoff (score >= {top10_threshold:.4f}): "
          f"TP={tp} FP={fp} FN={fn} TN={tn}")

    return metrics


# ── Slice Evaluation ───────────────────────────────────────────────────────────

# A slice with fewer rows or positives than this cannot support a claim. The
# handout's cautionary tale: ~33 Platinum test rows with ~2 churners gave a
# Platinum AUC anywhere between 0.00 and 1.00 depending on the split.
SLICE_MIN_N = 100
SLICE_MIN_POSITIVES = 10


def evaluate_slices(model: xgb.Booster,
                    X_val: np.ndarray,
                    y_val: np.ndarray,
                    slice_column: pd.Series) -> list[dict]:
    """
    Evaluate the model per loyalty tier on the validation split.

    The top-10% cutoff is computed ONCE on the whole validation set and then
    applied inside each tier. That answers the operational question ("if we
    contact the riskiest 10% overall, how much of each tier's churn do we
    catch?"). Re-computing a top-10% per tier would answer a different question
    and hide exactly the kind of under-served tier this check exists to find.

    A tier is `flagged` when its recall sits more than 10pp below the aggregate
    AND the tier is large enough to conclude from; `too_small` tiers are
    reported with their n and positives but never flagged, because a flag on
    ~2 positives is noise dressed as a finding.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    proba = model.predict(dval)
    threshold = np.percentile(proba, 90)
    contacted = proba >= threshold
    aggregate_recall = float(contacted[y_val == 1].mean())

    rows = []
    for tier in sorted(slice_column.unique()):
        mask = (slice_column == tier).to_numpy()
        y_s, p_s, c_s = y_val[mask], proba[mask], contacted[mask]
        n, positives = int(mask.sum()), int(y_s.sum())
        auc_s = float(roc_auc_score(y_s, p_s)) if 0 < positives < n else float("nan")
        recall_s = float(c_s[y_s == 1].mean()) if positives else float("nan")
        precision_s = float(y_s[c_s].mean()) if c_s.sum() else float("nan")
        too_small = n < SLICE_MIN_N or positives < SLICE_MIN_POSITIVES
        flagged = (not too_small) and (recall_s < aggregate_recall - 0.10)
        rows.append({
            "tier": tier,
            "n": n,
            "positives": positives,
            "contacted": int(c_s.sum()),
            "auc": round(auc_s, 4),
            "recall": round(recall_s, 4),
            "precision": round(precision_s, 4),
            "recall_gap_vs_aggregate": round(recall_s - aggregate_recall, 4),
            "flagged": flagged,
            "too_small": too_small,
        })

    print(f"\n── Slice Evaluation by {SLICE_COLUMN} (cutoff from full val set, "
          f"aggregate recall {aggregate_recall:.4f}) ──")
    print(f"  {'tier':10s} {'n':>5s} {'pos':>5s} {'auc':>7s} {'recall':>7s} {'prec':>7s}  note")
    for r in rows:
        note = ("TOO SMALL to conclude" if r["too_small"]
                else "FLAGGED: recall >10pp below aggregate" if r["flagged"] else "")
        print(f"  {r['tier']:10s} {r['n']:5d} {r['positives']:5d} {r['auc']:7.4f} "
              f"{r['recall']:7.4f} {r['precision']:7.4f}  {note}")
    return rows


# ── Evaluation Artifacts (PNG evidence) ────────────────────────────────────────

def save_evaluation_artifacts(model: xgb.Booster, metrics: dict, out_dir: str) -> None:
    """Write confusion-matrix.png and feature-importance.png to out_dir.

    Both are rubric evidence; on the final run out_dir is docs/lab3-evidence/,
    so the committed pictures come from the registered model.
    """
    import matplotlib
    matplotlib.use("Agg")  # no display on a training host
    import matplotlib.pyplot as plt

    # Confusion matrix at the top-10% cutoff, from the counts already in metrics.
    cm = np.array([[metrics["cm_tn"], metrics["cm_fp"]],
                   [metrics["cm_fn"], metrics["cm_tp"]]])
    fig, ax = plt.subplots(figsize=(5, 4.3))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, f"{v:,}", ha="center", va="center",
                color="white" if v > cm.max() / 2 else "black", fontsize=13)
    ax.set_xticks([0, 1], ["not contacted", "contacted"])
    ax.set_yticks([0, 1], ["retained", "churned"])
    ax.set_xlabel("Model decision (top 10% of scores)")
    ax.set_ylabel("Actual")
    ax.set_title(f"Confusion matrix @ top-10% cutoff (score >= {metrics['threshold_top10']:.3f})\n"
                 f"P@10 {metrics['precision_top10']:.3f}  R@10 {metrics['recall_top10']:.3f}  "
                 f"n={metrics['n_val_samples']:,}", fontsize=10)
    fig.tight_layout()
    cm_path = os.path.join(out_dir, "confusion-matrix.png")
    fig.savefig(cm_path, dpi=150)
    plt.close(fig)

    # Feature importance by GAIN (average loss reduction per split), which is
    # what "carried real signal" means; split COUNT over-rewards high-cardinality
    # numeric features. Features never used in a split are shown at 0.
    gain = model.get_score(importance_type="gain")
    imp = pd.Series({f: gain.get(f, 0.0) for f in FEATURE_COLUMNS}).sort_values()
    fig, ax = plt.subplots(figsize=(7, 0.4 * len(imp) + 1.5))
    ax.barh(imp.index, imp.values, color="#2b6cb0")
    ax.set_xlabel("Importance (gain)")
    ax.set_title(f"XGBoost {xgb.__version__} feature importance, {len(imp)} features")
    fig.tight_layout()
    fi_path = os.path.join(out_dir, "feature-importance.png")
    fig.savefig(fi_path, dpi=150)
    plt.close(fig)
    print(f"\n✓ Wrote {cm_path} and {fi_path}")


# ── Experiment Tracking (MLflow App) ───────────────────────────────────────────

def track_run(args, metrics: dict, scale_pos_weight: float) -> None:
    """
    Log this training run to your SageMaker MLflow App.

    Worth 5 points in Task 1: >=3 runs, each with logged params AND metrics,
    retrievable via mlflow.search_runs.

    ###########################################################################
    #  THERE ARE TWO MLflow PRODUCTS ON SAGEMAKER. USE THE APP.               #
    #                                                                         #
    #    CreateMlflowApp            serverless   NO ADDITIONAL CHARGE   <-- ok #
    #    CreateMlflowTrackingServer $0.60/hour   until you delete it    <-- NO #
    #                                                                         #
    #  $0.60/hr breaches the entire $10 course budget in 16.7 hours and costs #
    #  about $43 over a weekend -- more per hour than any endpoint in this    #
    #  course. It is not an endpoint, so "did I delete my endpoints?" will    #
    #  not find it. Most tutorials describe the Tracking Server because it    #
    #  shipped first. If anything asks you to choose a size (Small/Medium),   #
    #  you are on the wrong product.                                          #
    ###########################################################################

    Two things that fail in ways the error does not explain:

      1. `create-mlflow-app` postdates many installed AWS CLIs. An old CLI
         says "Invalid choice: 'create-mlflow-app'", which reads like a typo
         rather than a version problem. Check `aws --version` and upgrade.

      2. You need BOTH `mlflow` and `sagemaker-mlflow` installed. The second
         is the SigV4 auth plugin for arn:aws:sagemaker:... tracking URIs.
         Without it you get a connection error that never mentions credentials.

    TODO: Implement this function.
    """
    if not args.mlflow_arn:
        print("\n(no --mlflow-arn given; skipping experiment tracking)")
        return

    import mlflow  # needs `mlflow` AND `sagemaker-mlflow` (SigV4 plugin) installed

    mlflow.set_tracking_uri(args.mlflow_arn)   # the App ARN, verbatim
    mlflow.set_experiment(args.mlflow_experiment)

    # Everything that could explain a metric difference between runs goes in as
    # a param, including the RESOLVED scale_pos_weight (not args.scale_pos_weight,
    # which is None whenever it was computed) and the XGBoost version: Lab 3
    # documents different numbers on 3.x vs the 1.7 SageMaker container for the
    # same data and split. feature_set is what makes the with/without
    # churn_risk_score runs comparable in search_runs.
    params = {
        "max_depth": args.max_depth,
        "eta": args.eta,
        "num_round": args.num_round,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": round(float(scale_pos_weight), 4),
        "early_stopping_rounds": 20,
        "feature_set": args.feature_set_name,
        "n_features": len(FEATURE_COLUMNS),
        "xgboost_version": xgb.__version__,
        "data_source": "local_csv" if args.local_data_path else "feature_store_athena",
        "test_size": 0.30,
        "random_state": 42,
    }
    # Metrics must be numeric. The dict carries two strings (version, timestamp)
    # and NaN is possible for a single-class tier AUC; both are left out.
    numeric = {k: float(v) for k, v in metrics.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v}

    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(numeric)
        mlflow.set_tags({
            "lab": "cs401r-lab3",
            "registered": str(bool(args.register)),
            "feature_group": args.feature_group_name,
        })
        print(f"\n✓ Logged to MLflow App: experiment '{args.mlflow_experiment}', "
              f"run '{args.run_name}' ({run.info.run_id}), "
              f"{len(params)} params, {len(numeric)} metrics")


# ── Model Saving & Registry ────────────────────────────────────────────────────

def save_and_register_model(model: xgb.Booster,
                             metrics: dict,
                             args) -> None:
    """
    Save the model artifact and register it in SageMaker Model Registry.
    Status is set to PendingManualApproval — human approval required before deployment.
    """
    os.makedirs(args.model_dir, exist_ok=True)

    # Save the model in XGBoost's portable JSON format. The legacy binary
    # format is deprecated in 3.x, and JSON is the format most likely to load
    # in the 1.7 inference container Lab 5 uses.
    model_path = os.path.join(args.model_dir, "model.json")
    model.save_model(model_path)
    print(f"\n✓ Model saved to {model_path}")

    # Save feature metadata alongside the model so a consumer can rebuild the
    # exact input vector (column order matters to a Booster).
    metadata = {
        "feature_columns": FEATURE_COLUMNS,
        "label_column": LABEL_COLUMN,
        "feature_set": args.feature_set_name,
        "hyperparameters": {
            "max_depth": args.max_depth,
            "eta": args.eta,
            "num_round": args.num_round,
            "min_child_weight": args.min_child_weight,
            "subsample": args.subsample,
            "colsample_bytree": args.colsample_bytree,
            "scale_pos_weight": round(float(args.resolved_scale_pos_weight), 4),
        },
        "evaluation_metrics": metrics,
        "xgboost_version": xgb.__version__,
        "training_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    metadata_path = os.path.join(args.model_dir, "model_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    if not args.register:
        print("  (--register not set: model kept local, nothing uploaded or registered)")
        return

    # ── Upload the artifact ────────────────────────────────────────────────
    # SageMaker model packages point at a model.tar.gz in S3. artifacts/ is
    # the only prefix the MLEngineer role can write, and the bucket is the Lab 1
    # bucket passed in from `terraform output` (Task 4: reused, not recreated).
    run_name = args.run_name or f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    tar_path = os.path.join(args.model_dir, "model.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(model_path, arcname="model.json")
        tar.add(metadata_path, arcname="model_metadata.json")
    prefix = f"artifacts/models/churn/{run_name}"
    s3 = boto3.client("s3")
    s3.upload_file(tar_path, args.artifacts_bucket, f"{prefix}/model.tar.gz")
    s3.put_object(Bucket=args.artifacts_bucket, Key=f"{prefix}/evaluation_metrics.json",
                  Body=json.dumps(metrics, indent=2).encode(), ContentType="application/json")
    model_data_url = f"s3://{args.artifacts_bucket}/{prefix}/model.tar.gz"
    metrics_url = f"s3://{args.artifacts_bucket}/{prefix}/evaluation_metrics.json"
    print(f"✓ Uploaded {model_data_url}")

    # ── Register ───────────────────────────────────────────────────────────
    sm = boto3.client("sagemaker")
    try:
        sm.create_model_package_group(
            ModelPackageGroupName=MODEL_PACKAGE_GROUP,
            ModelPackageGroupDescription="NorthStar customer churn models (CS 401R Lab 3+)")
        print(f"✓ Created model package group {MODEL_PACKAGE_GROUP}")
    except sm.exceptions.ClientError as e:  # idempotent: the group may already exist
        if "already exists" not in str(e):
            raise
        print(f"  Model package group {MODEL_PACKAGE_GROUP} already exists")

    # The evidence collector requires auc_roc / precision_top10 / recall_top10
    # here to equal evaluation_metrics.json. Same dict, same rounding, so they do.
    metadata_keys = ("auc_roc", "precision_top10", "recall_top10", "baseline_auc",
                     "auc_vs_baseline", "lift_ci_low", "lift_ci_high", "n_val_samples",
                     "threshold_top10", "best_iteration", "n_features", "xgboost_version")
    resp = sm.create_model_package(
        ModelPackageGroupName=MODEL_PACKAGE_GROUP,
        ModelPackageDescription=(
            f"NorthStar churn XGBoost {xgb.__version__}, run {run_name}, "
            f"AUC {metrics['auc_roc']} vs recency baseline {metrics['baseline_auc']} "
            f"(lift CI [{metrics['lift_ci_low']}, {metrics['lift_ci_high']}]), "
            f"P@10 {metrics['precision_top10']}, R@10 {metrics['recall_top10']}"),
        InferenceSpecification={
            "Containers": [{"Image": XGBOOST_INFERENCE_IMAGE, "ModelDataUrl": model_data_url}],
            "SupportedContentTypes": ["text/csv"],
            "SupportedResponseMIMETypes": ["text/csv"],
            "SupportedRealtimeInferenceInstanceTypes": ["ml.m5.large", "ml.t2.medium"],
            "SupportedTransformInstanceTypes": ["ml.m5.large"],
        },
        ModelMetrics={"ModelQuality": {"Statistics": {
            "ContentType": "application/json", "S3Uri": metrics_url}}},
        # Never "Approved" from a training script: a human promotes in Lab 5.
        ModelApprovalStatus="PendingManualApproval",
        CustomerMetadataProperties={k: str(metrics[k]) for k in metadata_keys if k in metrics},
    )
    print(f"✓ Registered {resp['ModelPackageArn']} as PendingManualApproval")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # The kit's progress lines use box-drawing and check-mark characters. On
    # Windows a piped stdout (e.g. `... 2>&1 | tee train-final.log`) defaults
    # to cp1252 and dies on them, so force UTF-8 for the log as well as the
    # console. No-op on Linux and in SageMaker containers.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    os.makedirs(args.model_dir, exist_ok=True)
    os.makedirs(args.output_data_dir, exist_ok=True)

    # Feature-set switch. FEATURE_COLUMNS is the module-level list every stage
    # reads (query, DMatrix names, baseline index, importance plot), so
    # extending it in place here is enough.
    if args.include_risk_score and BASELINE_COLUMN not in FEATURE_COLUMNS:
        FEATURE_COLUMNS.append(BASELINE_COLUMN)
    args.feature_set_name = (f"base_{len(FEATURE_COLUMNS) - 1}+{BASELINE_COLUMN}"
                             if args.include_risk_score else f"base_{len(FEATURE_COLUMNS)}")

    print("=" * 60)
    print("NorthStar Churn Prediction — Training Script")
    print("=" * 60)
    print(f"Run: {args.run_name or '(unnamed)'} | feature set: {args.feature_set_name} "
          f"| xgboost {xgb.__version__} | register: {args.register}")

    # 1. Load features
    if args.local_data_path:
        df = load_features_local(args.local_data_path)
    else:
        df = load_features_from_feature_store(
            args.feature_group_name,
            args.training_start_date,
            args.training_end_date,
            args.artifacts_bucket,
        )

    # 2. Preprocess
    X, y, tiers = preprocess_features(df)

    # 3. Train/validation split (temporal split is better — split by customer_id here for simplicity)
    # test_size=0.30 matches the reference run and every published figure in
    # Lab 3 (6,999 train / 3,000 test on the 10k dataset). The bootstrap lift CI
    # is calibrated on a test set that size; a 0.20 split leaves 2,000 rows and
    # widens the interval enough to matter.
    #
    # The tier rides along as a third array so it is split on the identical
    # indices; re-deriving it from df afterwards would not line up.
    X_train, X_val, y_train, y_val, tier_train, tier_val = train_test_split(
        X, y, tiers, test_size=0.30, random_state=42, stratify=y
    )
    tier_val = tier_val.reset_index(drop=True)
    print(f"\nTrain: {len(X_train):,} | Validation: {len(X_val):,}")

    # 4. Train
    model = train_model(X_train, y_train, X_val, y_val, args)

    # 5. Evaluate (raises if a gate fails, so nothing below runs on a bad model)
    baseline_proba = train_recency_baseline(X_train, y_train, X_val)
    metrics = evaluate_model(model, X_val, y_val, baseline_proba)

    # 5b. Slice evaluation per loyalty tier -> slice-evaluation.csv (evidence)
    #     and per-tier metrics folded into the tracked metrics.
    slice_rows = evaluate_slices(model, X_val, y_val, tier_val)
    slice_path = os.path.join(args.output_data_dir, "slice-evaluation.csv")
    pd.DataFrame(slice_rows).to_csv(slice_path, index=False)
    print(f"✓ Wrote {slice_path}")
    for r in slice_rows:
        t = r["tier"].lower()
        metrics[f"tier_{t}_n"] = r["n"]
        for k in ("auc", "recall"):
            if r[k] == r[k]:  # skip NaN (single-class tier)
                metrics[f"tier_{t}_{k}"] = r[k]

    # 5c. Confusion matrix + feature importance PNGs (evidence)
    save_evaluation_artifacts(model, metrics, args.output_data_dir)

    # 6. Save locally; upload + register only with --register (final run)
    save_and_register_model(model, metrics, args)

    # 7. Track this run in your MLflow App (Task 1, 5 pts)
    track_run(args, metrics, getattr(args, "resolved_scale_pos_weight", None))

    # 8. Write metrics to output. SageMaker also collects these as job output;
    #    that is job lineage, NOT experiment tracking. It records that a job
    #    ran, not what you varied or why. Step 7 is the graded one.
    metrics_path = os.path.join(args.output_data_dir, "evaluation_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"✓ Wrote {metrics_path}")

    print("\n" + "=" * 60)
    print(f"Training complete. AUC-ROC: {metrics['auc_roc']:.4f} "
          f"(recency baseline {metrics['baseline_auc']:.4f}, "
          f"lift CI [{metrics['lift_ci_low']:.4f}, {metrics['lift_ci_high']:.4f}])")
    print("=" * 60)


if __name__ == "__main__":
    main()
