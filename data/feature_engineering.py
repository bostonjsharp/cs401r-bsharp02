"""
NorthStar Retail - pandas twin of the Lab 2 Glue feature job.

Why this module exists
----------------------
``glue-scripts/feature_engineer.py`` computes the 11 churn features in PySpark
inside an AWS Glue job. That code cannot be imported on a laptop or in
CodeBuild without a Spark runtime, so it cannot be unit-tested in isolation
and a CI pipeline cannot gate on it. This module is the *testable twin*: the
same feature definitions, written against pandas DataFrames, with one
function per feature family so each can be exercised on a hand-built frame.

The Glue job stays PySpark and is NOT changed to import this file (Glue would
need ``--extra-py-files`` and the point of the refactor is to gate on the
logic, not to redeploy the job). The two implementations are kept honest by
the parity test in ``tests/test_data.py``, which recomputes every feature from
``processed/`` rows on or before the cutoff with the functions below and
asserts equality with what the Glue job wrote to ``features/``. If the Glue
job ever changes, that test is what fails.

Semantics copied from the Glue job (read these before editing anything)
----------------------------------------------------------------------
* Temporal split: features use purchases with ``purchase_date <= T`` only
  (``T`` = ``FEATURE_CUTOFF`` = 2026-04-01). The label comes from the holdout
  ``(T, SNAPSHOT]`` and nowhere else.
* ``age = datediff(T, purchase_date)`` on *dates*, not timestamps. "In the N
  days before T" is ``age < N`` (so a purchase exactly N days old is excluded).
* ``avg_basket_size_6m`` is items per order over the last 180 days
  (``sum(num_items) / orders``), ``0.0`` when there are no orders in window.
* ``category_diversity_score`` counts distinct categories EXCLUDING
  ``'unknown'`` (the transform's imputation marker) divided by 8.
* ``online_to_store_ratio`` is online orders / all orders over all history.
* ``avg_order_value`` and ``total_lifetime_value`` are over all history.
* Six columns are rounded to 4 dp at the end (``ROUNDED_COLUMNS``), HALF_UP
  like Spark's ``F.round`` (see ``round_half_up``).
* Tier thresholds on lifetime value: 500 / 2000 / 5000.
* Every feature is a double (Feature Store declares them Fractional).

Customers with no transactions
------------------------------
The Glue job groups ``history`` by ``customer_id`` and so never emits a row
for a customer with no purchases. The functions here accept an optional
roster (``customer_ids`` / ``customers``) and, for roster customers absent
from the transactions, emit the "no activity" values: recency
``NO_PURCHASE_SENTINEL`` (999), tenure 0, every count / sum / ratio 0.0.
That is the same guard the Glue job applies to ``avg_basket_size_6m``,
extended to the whole row, so the output never contains NaN.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Union

import numpy as np
import pandas as pd

# Timeline anchors. MUST match glue-scripts/feature_engineer.py.
FEATURE_CUTOFF = "2026-04-01"
SNAPSHOT = "2026-06-30"

# Loyalty tier thresholds on total lifetime value (USD).
TIER_BRONZE_MAX = 500.0
TIER_SILVER_MAX = 2000.0
TIER_GOLD_MAX = 5000.0

# Churn-proxy bands (rule-based score, not the label).
RISK_HIGH_LO, RISK_HIGH_HI = 0.7, 1.0
RISK_MED_LO, RISK_MED_HI = 0.4, 0.7
RISK_LOW_LO, RISK_LOW_HI = 0.0, 0.4

N_CATEGORIES = 8.0
UNKNOWN_CATEGORY = "unknown"

# Recency for a customer with no purchases at all.
NO_PURCHASE_SENTINEL = 999.0

FEATURE_COLUMNS = [
    "days_since_last_purchase", "customer_tenure_days",
    "purchase_frequency_30d", "purchase_frequency_90d", "purchase_frequency_180d",
    "avg_order_value", "total_spend_90d", "total_lifetime_value",
    "avg_basket_size_6m", "category_diversity_score", "online_to_store_ratio",
]

ROUNDED_COLUMNS = [
    "avg_order_value", "total_lifetime_value", "total_spend_90d",
    "avg_basket_size_6m", "category_diversity_score", "online_to_store_ratio",
]

DateLike = Union[str, pd.Timestamp]


# -- Helpers -------------------------------------------------------------------

def _as_date(value) -> pd.Timestamp:
    """Normalise any date-like to a midnight Timestamp (Spark ``to_date``)."""
    return pd.Timestamp(value).normalize()


def _prepare(txns: pd.DataFrame, snapshot_date) -> pd.DataFrame:
    """Return the observation window with an integer ``_age`` column.

    ``_age`` is ``datediff(T, purchase_date)`` on calendar dates, exactly as the
    Glue job computes it, so ``age < N`` means "in the N days before T".
    Rows after T are dropped here so each feature function is leakage-safe on
    its own, not only when called through ``build_churn_feature_set``.
    """
    cutoff = _as_date(snapshot_date)
    df = txns.copy()
    if len(df) == 0:
        df["_age"] = pd.Series(dtype="int64")
        return df
    dates = pd.to_datetime(df["purchase_date"]).dt.normalize()
    df["purchase_date"] = dates
    df = df[dates <= cutoff].copy()
    df["_age"] = (cutoff - df["purchase_date"]).dt.days.astype("int64")
    return df


def _roster(customer_ids) -> Optional[pd.Index]:
    """Optional explicit customer roster; ``None`` means 'customers seen in txns'."""
    if customer_ids is None:
        return None
    if isinstance(customer_ids, pd.DataFrame):
        customer_ids = customer_ids["customer_id"]
    return pd.Index(pd.unique(pd.Series(list(customer_ids))))


def _finish(agg: pd.DataFrame, roster: Optional[pd.Index], fill: dict) -> pd.DataFrame:
    """Reindex onto the roster (if any), fill no-activity values, cast to double."""
    if roster is not None:
        agg = agg.reindex(roster)
        for col, value in fill.items():
            agg[col] = agg[col].fillna(value)
    agg = agg.astype("float64")
    agg.index.name = "customer_id"
    return agg.reset_index()


# -- Feature families ----------------------------------------------------------

def compute_recency_features(txns: pd.DataFrame, snapshot_date,
                             customer_ids=None) -> pd.DataFrame:
    """``days_since_last_purchase`` and ``customer_tenure_days``, one row per customer.

    * days_since_last_purchase = datediff(T, max purchase_date)
    * customer_tenure_days     = datediff(T, min purchase_date)

    ``customer_ids`` (Series, list or a DataFrame with a ``customer_id`` column)
    is an optional roster; roster customers with no purchases get
    ``NO_PURCHASE_SENTINEL`` recency and 0 tenure.
    """
    df = _prepare(txns, snapshot_date)
    agg = df.groupby("customer_id")["_age"].agg(
        days_since_last_purchase="min",   # newest purchase = smallest age
        customer_tenure_days="max",       # oldest purchase = largest age
    )
    return _finish(agg, _roster(customer_ids),
                   {"days_since_last_purchase": NO_PURCHASE_SENTINEL,
                    "customer_tenure_days": 0.0})


def compute_frequency_features(txns: pd.DataFrame, snapshot_date,
                               customer_ids=None) -> pd.DataFrame:
    """Order counts in the 30 / 90 / 180 days before T (``age < N``)."""
    df = _prepare(txns, snapshot_date)
    out = pd.DataFrame(index=df.groupby("customer_id").size().index)
    for days in (30, 90, 180):
        in_window = (df["_age"] < days).astype("int64")
        out["purchase_frequency_%dd" % days] = in_window.groupby(df["customer_id"]).sum()
    return _finish(out, _roster(customer_ids), {c: 0.0 for c in out.columns})


def compute_monetary_features(txns: pd.DataFrame, snapshot_date,
                              customer_ids=None) -> pd.DataFrame:
    """``avg_order_value``, ``total_spend_90d``, ``total_lifetime_value``, ``avg_basket_size_6m``.

    * avg_order_value      = mean(order_value) over all history
    * total_spend_90d      = sum(order_value) where age < 90
    * total_lifetime_value = sum(order_value) over all history
    * avg_basket_size_6m   = sum(num_items where age < 180) / orders where age < 180,
                             0.0 when there are no orders in the window (guarded
                             denominator, as in the Glue job)
    """
    df = _prepare(txns, snapshot_date)
    g = df.groupby("customer_id")
    out = pd.DataFrame(index=g.size().index)
    out["avg_order_value"] = g["order_value"].mean()
    out["total_lifetime_value"] = g["order_value"].sum()

    in_90 = df["_age"] < 90
    out["total_spend_90d"] = (df["order_value"].where(in_90, 0.0)
                              .groupby(df["customer_id"]).sum())

    in_180 = df["_age"] < 180
    orders_6m = in_180.astype("int64").groupby(df["customer_id"]).sum()
    items_6m = df["num_items"].where(in_180, 0).groupby(df["customer_id"]).sum()
    basket = items_6m / orders_6m.replace(0, np.nan)
    out["avg_basket_size_6m"] = basket.fillna(0.0)
    return _finish(out, _roster(customer_ids), {c: 0.0 for c in out.columns})


def compute_behavioral_features(txns: pd.DataFrame, snapshot_date=FEATURE_CUTOFF,
                                customer_ids=None) -> pd.DataFrame:
    """``category_diversity_score`` and ``online_to_store_ratio`` over all history.

    * category_diversity_score = distinct product_category (excluding 'unknown') / 8
    * online_to_store_ratio    = orders with channel == 'online' / all orders

    ``snapshot_date`` defaults to the cutoff because the kit calls this with
    the transactions only; it still drops post-cutoff rows for leakage safety.
    """
    df = _prepare(txns, snapshot_date)
    g = df.groupby("customer_id")
    out = pd.DataFrame(index=g.size().index)

    real_cat = df["product_category"].where(df["product_category"] != UNKNOWN_CATEGORY)
    distinct = real_cat.groupby(df["customer_id"]).nunique(dropna=True)
    out["category_diversity_score"] = distinct / N_CATEGORIES

    online = (df["channel"] == "online").astype("int64")
    out["online_to_store_ratio"] = online.groupby(df["customer_id"]).sum() / g.size()
    return _finish(out, _roster(customer_ids), {c: 0.0 for c in out.columns})


def round_half_up(values: pd.Series, places: int = 4) -> pd.Series:
    """Spark-compatible rounding.

    ``F.round`` rounds the *decimal string* of the double HALF_UP (Java
    ``BigDecimal.valueOf``), while pandas ``.round`` is banker's rounding on
    the binary value. They disagree on exact halves such as 123.45675, which
    is enough to break a bit-for-bit parity test on 200 of 9,999 customers.

    Even with matching rounding mode, about 100 customers still differ by
    exactly 1e-4 on ``avg_order_value``: a customer with 8 orders at 2-dp
    prices has a mean ending in ...5 at the 5th decimal, an exact tie, and
    Spark's partition-wise summation order moves the double by one ULP either
    side of it. That is why the parity test in tests/test_data.py allows
    ``atol=1e-4`` on the six rounded columns and demands exact equality on
    the other five.
    """
    quantum = Decimal(1).scaleb(-places)
    return pd.Series(
        [float(Decimal(repr(float(v))).quantize(quantum, rounding=ROUND_HALF_UP))
         for v in values],
        index=values.index, dtype="float64",
    )


# -- Derived columns (same rules as the Glue job) ------------------------------

def assign_loyalty_tier(ltv: pd.Series) -> pd.Series:
    """Bronze < 500 <= Silver < 2000 <= Gold < 5000 <= Platinum."""
    return pd.Series(
        np.select(
            [ltv < TIER_BRONZE_MAX, ltv < TIER_SILVER_MAX, ltv < TIER_GOLD_MAX],
            ["Bronze", "Silver", "Gold"],
            default="Platinum",
        ),
        index=ltv.index,
    )


def compute_churn_proxy(days: pd.Series, freq_30d: pd.Series) -> pd.Series:
    """Rule-based ``churn_risk_score`` in [0, 1]; the Lab 3 baseline, not the label."""
    def ramp(lo, hi, start, span):
        return lo + (hi - lo) * np.minimum(1.0, (days - start) / span)

    score = np.select(
        [(days > 60) & (freq_30d == 0), days > 30],
        [ramp(RISK_HIGH_LO, RISK_HIGH_HI, 60, 300), ramp(RISK_MED_LO, RISK_MED_HI, 30, 30)],
        default=ramp(RISK_LOW_LO, RISK_LOW_HI, 0, 30),
    )
    return pd.Series(np.clip(score, 0.0, 1.0), index=days.index).astype("float64")


# -- Full feature set ----------------------------------------------------------

def build_churn_feature_set(customers: pd.DataFrame, transactions: pd.DataFrame,
                            snapshot_date=FEATURE_CUTOFF,
                            holdout_end=SNAPSHOT) -> pd.DataFrame:
    """One row per roster customer with the 11 features, tier, proxy score and label.

    Mirrors ``main()`` of the Glue job: split windows, aggregate the four
    feature families over the observation window, derive ``loyalty_tier`` and
    ``churn_risk_score`` from the features, derive ``churn_label`` from the
    holdout (1 = no purchase in ``(T, holdout_end]``), round the six
    ``ROUNDED_COLUMNS`` to 4 dp.

    ``customers`` supplies the roster (``customer_id`` only; any
    ``loyalty_tier`` / ``churn_label`` columns on it are ignored because the
    Glue job derives both). Roster customers with no transactions at or before
    T get the no-activity row described in the module docstring, so the
    output has no NaN anywhere. ``event_time`` is not added: that is the
    job's run timestamp, not a feature.
    """
    roster = _roster(customers["customer_id"])
    cutoff = _as_date(snapshot_date)
    snapshot = _as_date(holdout_end)

    txns = transactions.copy()
    if len(txns):
        txns["purchase_date"] = pd.to_datetime(txns["purchase_date"]).dt.normalize()
        history = txns[txns["purchase_date"] <= cutoff]
        holdout = txns[(txns["purchase_date"] > cutoff) & (txns["purchase_date"] <= snapshot)]
    else:
        history, holdout = txns, txns

    parts = [
        compute_recency_features(history, cutoff, roster),
        compute_frequency_features(history, cutoff, roster),
        compute_monetary_features(history, cutoff, roster),
        compute_behavioral_features(history, cutoff, roster),
    ]
    features = parts[0]
    for part in parts[1:]:
        features = features.merge(part, on="customer_id", how="left")

    features["loyalty_tier"] = assign_loyalty_tier(features["total_lifetime_value"])
    features["churn_risk_score"] = compute_churn_proxy(
        features["days_since_last_purchase"], features["purchase_frequency_30d"]
    )
    bought_after = set(holdout["customer_id"].unique()) if len(holdout) else set()
    features["churn_label"] = (~features["customer_id"].isin(bought_after)).astype("int64")

    for col in ROUNDED_COLUMNS:
        features[col] = round_half_up(features[col], 4)

    return features[["customer_id"] + FEATURE_COLUMNS
                    + ["loyalty_tier", "churn_risk_score", "churn_label"]]
