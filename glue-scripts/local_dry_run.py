"""
Local dry run of both Glue scripts against the sample CSV. Costs nothing.

Runs inside the official Glue 4.0 image so the Spark version and awsglue
libraries match the real jobs, but skips the pieces that need AWS: the
catalog read, the S3 writes, and the Feature Store PutRecord calls. Every
transform and feature function is imported from the real scripts, so what
passes here is what runs in Glue.

    docker run --rm -v "$PWD:/work" -w /work \
      amazon/aws-glue-libs:glue_libs_4.0.0_image_01 \
      spark-submit glue-scripts/local_dry_run.py \
        --csv northstar-raw-sample.csv --out /work/.dryrun

Expected values come from profiling the sample with pandas (LAB2-TODO.md).
"""

import argparse
import os
import sys
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transform as T             # noqa: E402
import feature_engineer as FE     # noqa: E402

EXPECTED = {
    "raw_rows": 163255,
    "null_customer_rows": 3265,
    "processed_rows": 157627,
    "customers": 9999,
    "churn_rate": (0.15, 0.30),
    "sample_customer": "CUST-10000776",   # verify-lab2.sh GetRecord target
}

FEATURE_COLS = [
    "days_since_last_purchase", "customer_tenure_days", "purchase_frequency_30d",
    "purchase_frequency_90d", "purchase_frequency_180d", "avg_order_value",
    "total_spend_90d", "total_lifetime_value", "avg_basket_size_6m",
    "category_diversity_score", "online_to_store_ratio", "loyalty_tier",
    "churn_risk_score", "churn_label",
]

results = {"pass": 0, "fail": 0}


def check(label, ok, detail=""):
    results["pass" if ok else "fail"] += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spark = SparkSession.builder.appName("lab2-dry-run").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")

    # Every column as a string, which is what the crawler-backed catalog read
    # hands the real job.
    df = spark.read.csv(args.csv, header=True, inferSchema=False)
    raw = df.count()
    print("\n== transform ==")
    check("raw rows", raw == EXPECTED["raw_rows"], f"{raw}")

    df = T.cast_types(df)
    after_cast = df.count()
    check("rows dropped for null customer_id",
          raw - after_cast == EXPECTED["null_customer_rows"], f"{raw - after_cast}")
    check("all purchase_date parsed",
          df.filter(F.col("purchase_date").isNull()).count() == 0)
    check("no whitespace left in customer_id",
          df.filter(F.col("customer_id") != F.trim(F.col("customer_id"))).count() == 0)

    df = T.impute_nulls(df)
    nulls = {c: df.filter(F.col(c).isNull()).count() for c in T.SCHEMA}
    check("no nulls after impute", sum(nulls.values()) == 0, str({k: v for k, v in nulls.items() if v}))
    unknown = df.filter(F.col("product_category") == "unknown").count()
    check("'unknown' imputed for product_category", unknown > 0, f"{unknown} rows")

    df = T.deduplicate(df)
    processed = df.count()
    check("processed rows", processed == EXPECTED["processed_rows"], f"{processed}")
    check("no duplicate transaction_id",
          df.select("transaction_id").distinct().count() == processed)
    n_cust = df.select("customer_id").distinct().count()
    check("processed customers", n_cust == EXPECTED["customers"], f"{n_cust}")
    check("transaction-level grain preserved", processed > n_cust, f"{processed} rows / {n_cust} customers")
    print("  schema:", ", ".join(f"{f.name}:{f.dataType.simpleString()}" for f in df.schema.fields))

    df.coalesce(4).write.mode("overwrite").parquet(f"{args.out}/processed")

    # ── feature engineering, mirroring feature_engineer.main() ────────────
    print("\n== feature_engineer ==")
    df = spark.read.parquet(f"{args.out}/processed")
    history, holdout = FE.split_windows(df)
    check("history rows on or before T",
          history.filter(F.col("purchase_date") > FE.FEATURE_CUTOFF).count() == 0,
          f"{history.count()} rows")
    check("holdout rows strictly after T",
          holdout.filter(F.col("purchase_date") <= FE.FEATURE_CUTOFF).count() == 0,
          f"{holdout.count()} rows")

    features = FE.compute_rfm_features(history)
    features = FE.assign_loyalty_tier(features)
    features = FE.compute_churn_proxy(features)
    features = FE.attach_churn_label(features, holdout)
    for col in ["avg_order_value", "total_lifetime_value", "total_spend_90d",
                "avg_basket_size_6m", "category_diversity_score", "online_to_store_ratio"]:
        features = features.withColumn(col, F.round(F.col(col), 4))
    features = features.withColumn("event_time", F.lit(float(int(time.time()))))
    features.cache()

    n = features.count()
    check("one row per customer", n == features.select("customer_id").distinct().count(), f"{n} rows")
    check("feature rows == history customers",
          n == history.select("customer_id").distinct().count(), f"{n}")
    missing = [c for c in FEATURE_COLS if c not in features.columns]
    check("all 14 feature columns present", not missing, f"missing {missing}" if missing else "")
    total_nulls = sum(features.filter(F.col(c).isNull()).count() for c in features.columns)
    check("no null values", total_nulls == 0, f"{total_nulls}")

    types = dict(features.dtypes)
    check("churn_label is int", types.get("churn_label") == "int", types.get("churn_label"))
    check("event_time is double", types.get("event_time") == "double", types.get("event_time"))
    non_double = [c for c in FEATURE_COLS
                  if c not in ("loyalty_tier", "churn_label") and types[c] != "double"]
    check("13 numeric features are double", not non_double, str(non_double))

    rate = features.agg(F.avg("churn_label")).first()[0]
    lo, hi = EXPECTED["churn_rate"]
    check("churn_label rate plausible (15-30%)", lo <= rate <= hi, f"{rate:.1%}")

    tiers = {r[0] for r in features.select("loyalty_tier").distinct().collect()}
    check("all 4 loyalty tiers present", tiers == {"Bronze", "Silver", "Gold", "Platinum"}, ", ".join(sorted(tiers)))
    tier_counts = {r[0]: r[1] for r in features.groupBy("loyalty_tier").count().collect()}
    print("  tiers:", tier_counts)

    smin, smax = features.agg(F.min("churn_risk_score"), F.max("churn_risk_score")).first()
    check("churn_risk_score in [0,1]", smin >= 0 and smax <= 1, f"{smin:.3f} - {smax:.3f}")
    distinct_scores = features.select("churn_risk_score").distinct().count()
    check("churn score non-degenerate", distinct_scores > 3, f"{distinct_scores} distinct")

    churn_min_rec = features.filter("churn_label = 1").agg(F.min("days_since_last_purchase")).first()[0]
    active_max_rec = features.filter("churn_label = 0").agg(F.max("days_since_last_purchase")).first()[0]
    check("label not trivially separable by recency", churn_min_rec < active_max_rec,
          f"churner min recency {churn_min_rec:.0f} vs active max {active_max_rec:.0f}")

    bounds = features.agg(
        F.min("days_since_last_purchase"), F.max("customer_tenure_days"),
        F.min("category_diversity_score"), F.max("category_diversity_score"),
        F.min("online_to_store_ratio"), F.max("online_to_store_ratio"),
        F.min("avg_basket_size_6m"), F.max("avg_basket_size_6m"),
    ).first()
    check("recency >= 0", bounds[0] >= 0, f"min {bounds[0]}")
    check("category_diversity_score in [0,1]", 0 <= bounds[2] and bounds[3] <= 1, f"{bounds[2]} - {bounds[3]}")
    check("online_to_store_ratio in [0,1]", 0 <= bounds[4] and bounds[5] <= 1, f"{bounds[4]} - {bounds[5]}")
    check("avg_basket_size_6m >= 0", bounds[6] >= 0, f"{bounds[6]} - {bounds[7]}")

    sample = features.filter(F.col("customer_id") == EXPECTED["sample_customer"]).collect()
    check(f"{EXPECTED['sample_customer']} present", len(sample) == 1)
    if sample:
        print("  sample record:", {k: sample[0][k] for k in ["days_since_last_purchase", "purchase_frequency_90d",
                                                          "total_lifetime_value", "loyalty_tier",
                                                          "churn_risk_score", "churn_label"]})

    # What PutRecord would send for the sample customer: every value goes
    # through str(), so this is where "1.0" vs "1" would show up.
    if sample:
        record = {name: str(sample[0][name]) for name in ["churn_label", "event_time", "loyalty_tier"]}
        check("churn_label serializes as an integer string", record["churn_label"] in ("0", "1"), str(record))

    features.coalesce(2).write.mode("overwrite").parquet(f"{args.out}/features")

    print(f"\nSummary: {results['pass']} passed, {results['fail']} failed")
    spark.stop()
    sys.exit(1 if results["fail"] else 0)


if __name__ == "__main__":
    main()
