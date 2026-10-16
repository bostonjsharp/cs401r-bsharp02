"""Feature ablation for the churn model: does dropping a feature cost AUC?

Why this exists: gain importance (docs/lab3-evidence/feature-importance.png)
puts category_diversity_score and online_to_store_ratio near the bottom, but
gain cannot tell "weak" from "redundant with a stronger feature the trees
reached first". Retraining without a feature can. Each row below trains the
final (xgb-shallow) configuration on a feature subset and reports validation
AUC next to the full model's.

Free: runs on .dryrun/features.csv in a few seconds, no AWS.

    python experiments/ablation.py

train_subset() is the XGBoost part; its docstring walks through each call.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split

# Make `import churn_training_skeleton` work when run from the repo root or
# from inside experiments/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import churn_training_skeleton as ct  # noqa: E402

# The final configuration (run name xgb-shallow in LAB3-TODO.md Phase 1 results).
CONFIG = {"max_depth": 3, "eta": 0.05, "num_round": 400,
          "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8}

# Each experiment: a label and the features to REMOVE from the full set.
ABLATIONS = [
    ("full (11 features)", []),
    ("- behavioural pair", ["category_diversity_score", "online_to_store_ratio"]),
    ("- purchase_frequency_180d", ["purchase_frequency_180d"]),
    ("- customer_tenure_days", ["customer_tenure_days"]),
]


def train_subset(X_train: np.ndarray, y_train: np.ndarray,
                 X_val: np.ndarray, y_val: np.ndarray,
                 feature_names: list[str]) -> tuple[int, float]:
    """Train the xgb-shallow configuration on these columns and return
    (best_iteration, validation AUC at that iteration).

    X_train / X_val already contain ONLY the columns in feature_names, in
    that order. This mirrors train_model() in churn_training_skeleton.py;
    read that function alongside this docstring.

    1. Wrap the arrays. XGBoost trains on its own matrix type, which carries
       the labels and the column names (names are what make the importance
       chart readable):

           dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
           dval   = xgb.DMatrix(X_val,   label=y_val,   feature_names=feature_names)

    2. Class weight. 22% of customers churn, so an unweighted model is rewarded
       for leaning "no churn". scale_pos_weight multiplies the gradient of every
       positive example; negatives/positives (about 3.55 here) makes the two
       classes pull equally hard:

           spw = float((y_train == 0).sum() / (y_train == 1).sum())

    3. Parameters. Same dict as train_model(), with CONFIG supplying the
       numbers. Two that matter for this experiment:
         - max_depth 3: each tree asks at most three yes/no questions, so one
           tree can only express a 3-way interaction; boosting stacks hundreds
           of them to get further.
         - eta 0.05: each tree's contribution is shrunk to 5%, so the model
           needs many rounds but overfits slowly.
       Keep eval_metric as ["logloss", "auc"]: XGBoost early-stops on the LAST
       metric in the list, and we want AUC to be the stopping criterion.

           params = {"objective": "binary:logistic",
                     "eval_metric": ["logloss", "auc"],
                     "max_depth": CONFIG["max_depth"], "eta": CONFIG["eta"],
                     "min_child_weight": CONFIG["min_child_weight"],
                     "subsample": CONFIG["subsample"],
                     "colsample_bytree": CONFIG["colsample_bytree"],
                     "scale_pos_weight": spw, "seed": 42, "verbosity": 0}

    4. Train with early stopping. Up to num_round trees; after every tree the
       validation AUC is checked, and if it has not improved for 20 rounds in a
       row training stops and the booster remembers the best round:

           booster = xgb.train(params, dtrain, num_boost_round=CONFIG["num_round"],
                               evals=[(dval, "validation")],
                               early_stopping_rounds=20, verbose_eval=False)

    5. Read the result off the booster. best_iteration is the 0-based round
       with the best validation AUC; best_score is that AUC (the last metric
       in eval_metric, which is why the order mattered):

           return int(booster.best_iteration), float(booster.best_score)
    """
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_names)
    spw = float((y_train == 0).sum() / (y_train == 1).sum())
    params = {"objective": "binary:logistic",
              "eval_metric": ["logloss", "auc"],        # early stopping watches the LAST one
              "max_depth": CONFIG["max_depth"], "eta": CONFIG["eta"],
              "min_child_weight": CONFIG["min_child_weight"],
              "subsample": CONFIG["subsample"],
              "colsample_bytree": CONFIG["colsample_bytree"],
              "scale_pos_weight": spw, "seed": 42, "verbosity": 0}
    booster = xgb.train(params, dtrain, num_boost_round=CONFIG["num_round"],
                        evals=[(dval, "validation")],
                        early_stopping_rounds=20, verbose_eval=False)
    return int(booster.best_iteration), float(booster.best_score)


def main() -> None:
    df = ct.load_features_local(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "..", ".dryrun", "features.csv"))
    X, y, _tiers = ct.preprocess_features(df)
    # Same split as the training script (stratified 70/30, seed 42), so the
    # "full" row here reproduces the xgb-shallow AUC of 0.7835.
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.30,
                                                      random_state=42, stratify=y)
    all_features = list(ct.FEATURE_COLUMNS)

    rows = []
    for label, dropped in ABLATIONS:
        keep = [f for f in all_features if f not in dropped]
        idx = [all_features.index(f) for f in keep]
        best_iter, auc = train_subset(X_train[:, idx], y_train, X_val[:, idx], y_val, keep)
        rows.append({"feature set": label, "n_features": len(keep),
                     "best_iteration": best_iter, "val_auc": round(auc, 4)})
        print(f"  {label:<28} n={len(keep):>2}  best_iter={best_iter:>3}  auc={auc:.4f}")

    table = pd.DataFrame(rows)
    table["auc_vs_full"] = (table["val_auc"] - table.loc[0, "val_auc"]).round(4)
    print("\n" + table.to_string(index=False))


if __name__ == "__main__":
    main()
