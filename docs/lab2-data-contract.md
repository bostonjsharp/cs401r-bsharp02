# Data Contract: processed/customers

| | |
|---|---|
| Dataset | `s3://northstar-dev-data-829485866627/processed/customers/` |
| Format | Apache Parquet, Snappy compression, unpartitioned (up to 4 files per run) |
| Version | 1.0 (2026-09-29) |
| Owner | Data Engineering (the `northstar-dev-DataEngineer` role) |
| Status | Active. Produced by every run of the Lab 2 pipeline |

This contract is the agreement between the team that produces the cleaned
customer transaction dataset and the teams that consume it. The producer
enforces every guarantee below inside the job itself (assertions in
`glue-scripts/transform.py` fail the run rather than write a bad dataset),
and `scripts/verify-lab2.sh` re-checks them against the written Parquet.

### Producer

Team / process: Glue ETL job `northstar-dev-transform` (`glue-scripts/transform.py`),
running as `northstar-dev-DataEngineer` inside `northstar-dev-private-1`.

Input: the Glue Data Catalog table `northstar_dev.customers`, registered by
`northstar-dev-raw-crawler` over `raw/customers/*.csv`.

The job applies, in order: whitespace trim, empty-string-to-null, type casting
(dates parsed as `yyyy-MM-dd` or `MM/dd/yyyy`), removal of rows with no
`customer_id`, median imputation of numeric nulls, `'unknown'` imputation of
string nulls, and deduplication on `transaction_id`.

### Consumers

- Feature engineering job `northstar-dev-feature-engineer` (`glue-scripts/feature_engineer.py`),
  which aggregates this dataset to one row per customer in `features/customers/`
  and the `northstar-dev-customer-features` Feature Group.
- (Future) Direct model training and evaluation in Lab 3, reading as `northstar-dev-MLEngineer`.
- (Future) Ad-hoc analysis through Athena over the catalog.

### Grain

One row per transaction. A customer appears on many rows.

`transaction_id` is the natural key and is unique. `customer_id` repeats across
rows by design; that repetition is the purchase history the feature job
aggregates. A consumer that finds only one row per customer should treat the
dataset as broken, not as deduplicated.

### Schema

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `transaction_id` | string | no | Natural key, `TXN-` + 12 uppercase alphanumerics. Unique. |
| `customer_id` | string | no | `CUST-` + 8 digits. Join key to every downstream dataset. Repeats across rows. |
| `purchase_date` | date | no | Calendar date of the purchase, normalised to ISO 8601 from either raw layout. |
| `order_value` | double | no | Gross order value in USD. Raw nulls (~4%) are imputed with the run's column median. |
| `num_items` | int | no | Line items in the order. Raw nulls are imputed with the rounded column median. |
| `payment_method` | string | no | One of `credit_card`, `debit_card`, `gift_card`, `cash`, or `unknown`. |
| `channel` | string | no | `store` or `online`, or `unknown`. |
| `store_id` | string | no | `STORE-` + 3 digits for in-store orders, `ONLINE` for online orders, or `unknown`. |
| `product_category` | string | no | Primary category of the order. Eight categories, or `unknown` where the raw value was blank (~2% of rows). |

Column order is as listed. No column is added, removed, renamed, or retyped
without a version change (see Versioning).

### Quality Guarantees

Each assertion is measurable with a single query over the dataset. Bounds are
inclusive. The reference values were measured on the 2026-09 sample
(163,255 raw rows to 157,627 processed rows across 9,999 customers).

1. `customer_id` is never null. Rows without one are dropped at the producer.
2. No duplicate `transaction_id` rows: `count(*) = count(distinct transaction_id)`.
   A `customer_id` repeating across rows is expected, not a defect.
3. Every `transaction_id` matches `^TXN-[A-Z0-9]{12}$` and every `customer_id` matches `^CUST-[0-9]{8}$`.
4. `purchase_date` is never null and is a valid ISO 8601 date within the
   dataset's collection window, `2025-04-01` to `2026-06-30` inclusive. No date
   is in the future relative to the run.
5. `order_value` is never null and lies in `[15.00, 620.00]` USD. Values outside
   `[0, 10000]` are treated as a producer defect in any version of this contract.
6. `num_items` is never null and is an integer in `[1, 9]`. Any run producing a
   value outside `[1, 100]` is rejected.
7. `payment_method`, `channel`, `store_id`, and `product_category` are never null;
   the placeholder `'unknown'` marks an imputed value and must not exceed 5% of
   rows for any single column (measured: 2.0% for `product_category`, 0% for the others).
8. `channel = 'online'` if and only if `store_id = 'ONLINE'` (measured: holds on 100% of rows).
9. Row count per run is within 10% of the previous run for the same source
   window. The transaction-level grain is preserved: `count(*) > count(distinct customer_id)`.

Guarantees 1, 2, and 4 are enforced by assertions in the transform job; a
violation aborts the run before any Parquet is written. Guarantees 1, 2, 4, and
9 are re-verified by `scripts/verify-lab2.sh`.

### SLA

- Data is available in `processed/customers/` within 2 hours of landing in `raw/customers/`.
  The pipeline currently runs on demand (`aws glue start-workflow-run --name northstar-dev-pipeline`);
  the crawler plus transform step takes about 10 minutes on two G.1X workers.
- The job overwrites `processed/customers/` atomically per run (`mode("overwrite")`).
  Consumers should read the prefix, not individual files, since file names change every run.
- A failed run leaves the previous output in place. `max_retries = 0`: failures are
  surfaced to the operator, not retried silently.
- Previous versions of every object are retained for 30 days by the bucket's
  `expire-processed-versions` lifecycle rule, so a bad run can be rolled back.
- Producer responds to a reported contract violation within 1 business day.

### Versioning

- This is version 1.0. The version is recorded in this document and in the
  Terraform that builds the pipeline (`infrastructure/modules/glue`).
- Additive, backwards-compatible changes (a new nullable column appended at the
  end, a widened numeric bound) increment the minor version and are announced
  to consumers at least 5 business days before they take effect.
- Breaking changes (removing or renaming a column, changing a type, tightening
  a guarantee, changing the grain) require a new S3 prefix (for example
  `processed/customers/v2/`) and a new catalog table, so that existing consumers
  keep reading the old shape until they migrate. Breaking changes require consumer
  notification 5 business days in advance, and the previous prefix is kept for at
  least 30 days after the new one is live.
- Schema is discoverable at any time from the catalog:
  `aws glue get-table --database-name northstar_dev --name customers` for the raw
  input, and from the Parquet footer for this dataset.
