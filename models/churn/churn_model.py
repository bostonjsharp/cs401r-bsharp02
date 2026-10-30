"""NorthStar churn model, packaged for the Lab 4 SageMaker Pipeline.

CS 401R Lab 4. train_sagemaker.py (beside this file; do not edit it) imports
this module inside the SageMaker XGBoost 1.7-1 training container and calls
train(df). Everything the job needs goes through three names:

    FEATURE_COLUMNS   list[str]   model input columns, in order
    LABEL             str         "churn_label"
    train(df, seed=42) -> (xgboost.Booster, dict)

This is the Lab 3 training path (churn_training_skeleton.py at the repo root)
with the data loading, MLflow tracking and registry calls removed: the
container stages the data and the pipeline's RegisterModel step does the
registering. The functions are COPIED, not imported, because only models/churn/
is uploaded to the training job.

Configuration is the Lab 3 winner, "xgb-shallow" (docs/lab3-evaluation-report.md):
depth 3, eta 0.05, 400 rounds with early stopping on validation AUC. It is
fixed in PARAMS below so the registered model's hyperparameters (read back out
of the booster by train_sagemaker.py) match what the stage-mapping doc quotes.

Container constraints honoured here: Python 3.8 syntax (no match, no X | Y,
no removeprefix), pandas 1.x API, no mlflow / sagemaker / boto3 imports at
module top, plain-ASCII prints. Keep it that way; the laptop runs 3.14 and
will not catch a 3.9+ slip, the container will.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split

# -- Contract ------------------------------------------------------------------

FEATURE_COLUMNS = [
    "days_since_last_purchase",
    "customer_tenure_days",
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    "category_diversity_score",
    "online_to_store_ratio",
]

LABEL = "churn_label"
SLICE_COLUMN = "loyalty_tier"
BASELINE_FEATURE = "days_since_last_purchase"

# -- Hyperparameters: Lab 3 "xgb-shallow", the registered configuration ----------
#
# One dict, quoted by docs/lab4-xops-assessment.md. scale_pos_weight is NOT
# here because it is computed from the training split (3.545 on the reference
# data); it is recorded in the metrics as `scale_pos_weight`.
PARAMS = {
    "objective": "binary:logistic",
    # ORDER MATTERS: XGBoost early-stops on the LAST metric. "auc" last, or
    # the reweighted logloss keeps improving long after ranking peaks and
    # validation AUC drops ~0.02 (defect 49).
    "eval_metric": ["logloss", "auc"],
    "max_depth": 3,
    "eta": 0.05,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "verbosity": 1,
}
NUM_ROUND = 400
EARLY_STOPPING_ROUNDS = 20
TEST_SIZE = 0.30

# Promotion gates. There is deliberately NO absolute AUC threshold (the
# reference model fell below 0.72 on 58% of random splits); AUC is reported.
EVAL_THRESHOLDS = {
    "precision_top10": 0.50,
    "recall_top10": 0.25,
}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42

# A slice with fewer rows or positives than this cannot support a claim.
SLICE_MIN_N = 100
SLICE_MIN_POSITIVES = 10


# -- Preprocessing -------------------------------------------------------------

def preprocess_features(df: pd.DataFrame):
    """Return (X, y, tiers) aligned row-for-row.

    Missing-value strategy: NaN passes through to XGBoost, which learns a
    default branch direction per split. A sentinel such as -1 in
    days_since_last_purchase would read as "bought yesterday", the opposite of
    unknown. The Lab 2 feature table has zero nulls anyway; they are reported,
    never silently filled. customer_id, event_time and churn_risk_score are
    simply not in FEATURE_COLUMNS, so they are dropped by selection.
    """
    print("Preprocessing %d samples..." % len(df))
    df = df.dropna(subset=[LABEL]).reset_index(drop=True)

    null_counts = df[FEATURE_COLUMNS].isna().sum()
    if null_counts.sum():
        print("  Nulls per feature (left as NaN for XGBoost's native handling):")
        for col, n in null_counts[null_counts > 0].items():
            print("    %s: %d" % (col, n))
    else:
        print("  No missing feature values")

    X = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = df[LABEL].to_numpy().astype(int)
    # A Series, not .values: under pandas 3 .values can be an Arrow array
    # that train_test_split cannot index.
    tiers = df[SLICE_COLUMN].astype(str).reset_index(drop=True)

    print("  Positive class rate: %.1f%%" % (100.0 * y.mean()))
    print("  Feature matrix shape: %s" % (X.shape,))
    return X, y, tiers


# -- Training ------------------------------------------------------------------

def train_model(X_train, y_train, X_val, y_val, seed: int = 42):
    """Train the XGBoost churn model with the fixed PARAMS. Returns (booster, spw)."""
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)

    # Class imbalance from the training split, not hardcoded (3.545 on reference data).
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))

    params = dict(PARAMS)
    params["scale_pos_weight"] = spw
    params["seed"] = seed

    print("\nTraining XGBoost with params: %s" % json.dumps(params, indent=2))
    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=NUM_ROUND,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=25,
    )
    return model, spw


def train_recency_baseline(X_train, y_train, X_val, seed: int = 42):
    """Score the validation split with a model over days_since_last_purchase ALONE.

    Not a constant predictor (AUC 0.5 by construction). The question is whether
    the feature set beats the rule the business already has for free.
    """
    i = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtr = xgb.DMatrix(X_train[:, [i]], label=y_train, feature_names=[BASELINE_FEATURE])
    dva = xgb.DMatrix(X_val[:, [i]], feature_names=[BASELINE_FEATURE])
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": "binary:logistic", "eval_metric": "auc",
        "max_depth": 4, "eta": 0.1, "subsample": 0.9,
        "colsample_bytree": 0.9, "scale_pos_weight": spw, "seed": seed,
    }
    return xgb.train(params, dtr, num_boost_round=200).predict(dva)


def bootstrap_lift_ci(y_true, proba, baseline_proba, n: int = BOOTSTRAP_N,
                      seed: int = BOOTSTRAP_SEED, alpha: float = 0.05):
    """Paired percentile CI for (model AUC - baseline AUC) over the validation set.

    Each replicate resamples row indices once and scores BOTH models on those
    rows, preserving their correlation. Single-class resamples are skipped.
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
            "The validation set is too small or too imbalanced to support this gate.")
    d = np.sort(diffs)
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


# -- Evaluation ----------------------------------------------------------------

def evaluate_model(model, X_val, y_val, baseline_proba) -> dict:
    """Metrics dict; raises ValueError if a gate fails.

    The raise is deliberate and load-bearing: it fails the SageMaker training
    step, so a model that misses a gate is never registered at all.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

    top10_threshold = np.percentile(y_pred_proba, 90)
    top10_mask = y_pred_proba >= top10_threshold
    precision_top10 = y_val[top10_mask].mean() if top10_mask.sum() > 0 else 0.0
    recall_top10 = y_val[top10_mask].sum() / y_val.sum() if y_val.sum() > 0 else 0.0

    baseline_auc = roc_auc_score(y_val, baseline_proba)
    auc_vs_baseline = auc_roc - baseline_auc
    lift_ci_low, lift_ci_high = bootstrap_lift_ci(y_val, y_pred_proba, baseline_proba)

    # Confusion matrix at the BUSINESS threshold (top-10% cutoff), not 0.5:
    # scale_pos_weight shifts the whole score scale.
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

    print("\n-- Evaluation Results --------------------------------")
    failures = []
    for metric, threshold in EVAL_THRESHOLDS.items():
        value = metrics[metric]
        status = "PASS" if value >= threshold else "FAIL"
        print("  %-25s: %.4f  (threshold: %.4f)  %s" % (metric, value, threshold, status))
        if value < threshold:
            failures.append("%s=%.4f < %s" % (metric, value, threshold))

    ci_status = "PASS" if lift_ci_low > 0 else "FAIL"
    print("  %-25s: [%.4f, %.4f]  (must exclude 0)  %s"
          % ("auc_lift 95% CI", lift_ci_low, lift_ci_high, ci_status))
    if lift_ci_low <= 0:
        failures.append(
            "auc_lift 95%% CI [%.4f, %.4f] includes zero - no evidence the model "
            "beats the recency-only baseline" % (lift_ci_low, lift_ci_high))

    if failures:
        raise ValueError(
            "Model failed evaluation thresholds: %s. Do not promote to Model Registry."
            % "; ".join(failures))

    print("  All thresholds passed")
    print("  Confusion matrix @ top-10%% cutoff (score >= %.4f): TP=%d FP=%d FN=%d TN=%d"
          % (top10_threshold, tp, fp, fn, tn))
    return metrics


def evaluate_slices(model, X_val, y_val, slice_column: pd.Series) -> list:
    """Per-tier results on the validation split, top-10% cutoff fixed on the whole set.

    A tier is `flagged` when its recall sits more than 10 pp below the aggregate
    AND it is large enough to conclude from; `too_small` tiers are reported, never
    flagged.
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

    print("\n-- Slice Evaluation by %s (cutoff from full val set, aggregate recall %.4f) --"
          % (SLICE_COLUMN, aggregate_recall))
    print("  %-10s %5s %5s %7s %7s %7s  note" % ("tier", "n", "pos", "auc", "recall", "prec"))
    for r in rows:
        note = ("TOO SMALL to conclude" if r["too_small"]
                else "FLAGGED: recall >10pp below aggregate" if r["flagged"] else "")
        print("  %-10s %5d %5d %7.4f %7.4f %7.4f  %s"
              % (r["tier"], r["n"], r["positives"], r["auc"], r["recall"], r["precision"], note))
    return rows


def _flatten_slices(rows: list) -> dict:
    """tier_<tier>_{n,positives,auc,recall,precision,flagged} keys, JSON-safe.

    NaN is not valid JSON and json.dump would write a bare NaN token, so a
    tier without an AUC/recall is emitted as null (None).
    """
    out = {}
    for r in rows:
        key = str(r["tier"]).lower()
        out["tier_%s_n" % key] = r["n"]
        out["tier_%s_positives" % key] = r["positives"]
        for m in ("auc", "recall", "precision"):
            v = r[m]
            out["tier_%s_%s" % (key, m)] = None if (v != v) else v
        out["tier_%s_flagged" % key] = bool(r["flagged"])
        out["tier_%s_too_small" % key] = bool(r["too_small"])
    return out


# -- Entry point for train_sagemaker.py ----------------------------------------

def train(df: pd.DataFrame, seed: int = 42):
    """Train on the full labeled feature table; return (booster, metrics)."""
    X, y, tiers = preprocess_features(df)

    X_train, X_val, y_train, y_val, _, tiers_val = train_test_split(
        X, y, tiers, test_size=TEST_SIZE, random_state=seed, stratify=y)
    tiers_val = tiers_val.reset_index(drop=True)
    print("Split: %d train / %d val (stratified, seed %d)" % (len(y_train), len(y_val), seed))

    model, spw = train_model(X_train, y_train, X_val, y_val, seed=seed)
    baseline_proba = train_recency_baseline(X_train, y_train, X_val, seed=seed)

    metrics = evaluate_model(model, X_val, y_val, baseline_proba)   # raises on a failed gate
    slice_rows = evaluate_slices(model, X_val, y_val, tiers_val)

    metrics.update(_flatten_slices(slice_rows))
    metrics["slice_results"] = slice_rows
    metrics["scale_pos_weight"] = round(spw, 4)
    metrics["hyperparameters"] = dict(PARAMS, num_round=NUM_ROUND,
                                      early_stopping_rounds=EARLY_STOPPING_ROUNDS,
                                      test_size=TEST_SIZE, seed=seed)
    metrics["feature_set"] = "base_11"
    metrics["n_train_samples"] = int(len(y_train))
    return model, metrics
