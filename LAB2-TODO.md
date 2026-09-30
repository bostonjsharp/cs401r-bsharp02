# Lab 2 To-Do: Data & Feature Engineering

Due **Sat Oct 3, midnight**. Tag `lab2-submit`, submit repo URL in Canvas.
Points: Task 1 (25) + Task 2 (25) + Task 3 (20) + Task 4 (15) + Task 5 (15) = 100.

All three instructor corrections are already folded in below (FEATURE_CUTOFF anchor,
dedup on `transaction_id`, Grain section in the contract, "outcome window" wording).

Strategy: **Phases 1-4 cost nothing** (code, LocalStack, local dry run, docs).
**Phase 5 is the only part that bills** - do it in one sitting and tear down the same day.
The NAT Gateway is ~$0.045/hr; a 3-4 hour session including Glue runs is about $1-2.

---

## Phase 0 - Setup (done 2026-09-28)

- [x] Copy starter kit into repo: `glue-scripts/`, `scripts/verify-lab2.sh`,
      `scripts/teardown-lab2.sh`, `northstar-raw-sample.csv`
- [x] Replace `Makefile` with the Lab 2 version (has `LOCAL_OUT ?=`)
- [x] Fix `python3` for the verify script: in Git Bash `python3` is Python 3.13 with
      **no pandas/pyarrow** (`python` 3.14 has them). Run
      `python3 -m pip install pandas pyarrow` or the data-quality checks all FAIL.

## Phase 1 - Task 1 Terraform: harden the platform (no cost)

Status 2026-09-29: all four modules edited and `terraform validate` passes in both
environments. Walked through all four with Boston. LocalStack evidence captured (27 resources,
3 roles, no NAT). Nothing applied to real AWS yet.

### modules/vpc
- [x] `aws_subnet.private` - `var.private_subnet_cidr` (10.0.1.0/24), us-east-1a,
      no public IP, Name tag `${prefix}-private-1` (verify script looks up this tag)
- [x] `aws_eip` (`${prefix}-eip`) and `aws_nat_gateway` (`${prefix}-nat`) in the
      **public** subnet, both behind `count = var.enable_nat_gateway ? 1 : 0`
- [x] `aws_route_table.private` (`${prefix}-private-rt`), 0.0.0.0/0 -> NAT, + association
- [x] Variables `enable_nat_gateway` (bool, default true), `private_subnet_cidr`
- [x] Output `private_subnet_id`
- [x] ASCII only in every `description` (no em dashes / arrows) - real AWS rejects them

### modules/storage
- [x] `aws_s3_bucket_lifecycle_configuration` with **exactly 5** rules (verify counts them):

      | Rule id                   | Prefix       | Action                       |
      |---------------------------|--------------|------------------------------|
      | expire-raw-data           | raw/         | current versions, 90 days    |
      | expire-raw-versions       | raw/         | noncurrent versions, 30 days |
      | expire-processed-versions | processed/   | noncurrent versions, 30 days |
      | expire-feature-versions   | features/    | noncurrent versions, 60 days |
      | expire-datacapture        | datacapture/ | current versions, 7 days     |

- [x] Variable `enable_lifecycle_rules` (bool, default true)

### modules/iam
- [x] `DataEngineer` role + policy + attachment
  - Trust: `glue`, `lambda`, **and** `sagemaker` (.amazonaws.com)
  - Glue full access + `glue:GetConnection`
  - EC2 ENI lifecycle + `ec2:CreateTags`/`DeleteTags` on `network-interface/*`
  - S3 read/write on `raw/`, `processed/`, `features/`; **read-only** on `artifacts/glue/`
  - `s3:GetBucketAcl` on the bucket and `s3:PutObjectAcl` on `features/*` (Feature Store traps)
  - Feature Store: `PutRecord`, `CreateFeatureGroup`, `DescribeFeatureGroup`
  - CloudWatch Logs write
  - Must NOT be able to write `artifacts/`
- [x] `ModelMonitor` role + policy + attachment
  - Trust: `sagemaker`
  - CloudWatch `PutMetricData`, `GetMetricStatistics`, `PutMetricAlarm`, `DescribeAlarms`
  - SageMaker `ListProcessingJobs`, `DescribeProcessingJob`
  - S3 read-only on `artifacts/`; CloudWatch Logs write
  - Must NOT be able to write S3 at all
- [x] Outputs for both role ARNs

### modules/sagemaker
- [x] `app_network_access_type = "VpcOnly"` (make it a variable)
- [x] Domain `subnet_ids` fed from the private subnet (change is in `environments/dev/main.tf`)

### environments
- [x] `dev/variables.tf`: add `private_subnet_cidr` = "10.0.1.0/24"
- [x] `dev/main.tf`: pass new vars; sagemaker gets `module.vpc.private_subnet_id`
- [x] `dev/outputs.tf`: private subnet id, new role ARNs
- [x] `local/main.tf`: `enable_nat_gateway = false`, `enable_lifecycle_rules = false`
- [x] `local/outputs.tf`: add the new role ARNs
- [x] `make local-validate LOCAL_OUT=docs/lab2-localstack-output.txt`
      -> must show 3 IAM roles, the VPC, and an empty NAT list (3 pts)
- [x] Confirm `docs/lab1b-localstack-output.txt` was NOT modified (`git status`)

## Phase 2 - Tasks 2 and 3 Terraform: pipeline modules (no cost)

Status 2026-09-29: both modules written, wired into environments/dev, validate + fmt
clean, hardcode grep empty. First real test is the Phase 5 apply.

### modules/glue (new)
- [x] `aws_glue_catalog_database` - `northstar_dev` (underscores: derive from project/env)
- [x] `aws_glue_crawler` - `${prefix}-raw-crawler`, target `raw/customers/`,
      role = DataEngineer, **no table prefix** (table must be named `customers`)
- [x] `aws_security_group` for Glue with a **self-referencing** all-ports ingress
      (`self = true`; a VPC-CIDR rule does not satisfy Glue)
- [x] `aws_glue_connection` type NETWORK - private subnet, Glue SG, AZ
- [x] `aws_s3_object` x2 - upload `transform.py` and `feature_engineer.py` to
      `artifacts/glue/` (use `source_hash` so edits re-upload)
- [x] `aws_glue_job` `${prefix}-transform` - Glue 4.0, Python 3, args
      `--database_name`, `--table_name`, `--output_path`
- [x] `aws_glue_job` `${prefix}-feature-engineer` - args `--input_path`,
      `--output_path`, `--feature_group_name`, `--region`
- [x] Both jobs: G.1X, 2 workers, `max_retries = 0`, a timeout, the connection attached,
      and `--TempDir` under a prefix DataEngineer can write (not `artifacts/`)
- [x] Zero hardcoded names - everything from variables (3 pts)

### modules/feature_store (new)
- [x] `aws_sagemaker_feature_group` `${prefix}-customer-features`
  - record identifier `customer_id`, event time `event_time`
  - online store enabled
  - offline store `s3://<bucket>/features/offline-store/` (NOT `features/customers/`)
  - role = DataEngineer
  - 16 feature definitions: `customer_id` String, `event_time` **Fractional**,
    `loyalty_tier` String, `churn_label` **Integral**, the other 12 Fractional

### Extra Terraform (your "do more with Terraform" goal)
- [x] Upload `northstar-raw-sample.csv` to `raw/customers/` with `aws_s3_object`
      instead of `aws s3 cp` (replaces a manual CLI step)
- [x] `aws_glue_workflow` + 3 `aws_glue_trigger`s chaining crawler -> transform ->
      feature-engineer, so the whole pipeline is one `aws glue start-workflow-run`
- [x] `aws_cloudwatch_log_group` for the `/aws-glue/*` groups with a retention period,
      so Terraform owns and destroys them
- [x] Outputs for job names, crawler, workflow (glue done), feature group -> scripts read
      `terraform output` instead of hardcoding names
- [x] Optional: S3 gateway VPC endpoint (free) so S3 traffic skips the NAT
- [x] `terraform fmt -recursive` in `infrastructure/`, then `terraform validate` in
      both `environments/dev` and `environments/local`

## Phase 3 - Glue scripts (no cost)

Status 2026-09-29: all 8 functions implemented. `glue-scripts/local_dry_run.py` runs
them in the Glue 4.0 Docker image: 30/30 checks, every target below hit exactly, and
verify-lab2.sh's own pandas block passes 12/12 on the output.

### glue-scripts/transform.py
- [x] `cast_types` - trim all columns -> empty string to null -> cast per SCHEMA ->
      parse dates in BOTH formats (`yyyy-MM-dd` and `MM/dd/yyyy`) with coalesce ->
      drop null `customer_id`
- [x] `impute_nulls` - numeric -> **median** (round for `num_items`); strings -> `'unknown'`
- [x] `deduplicate` - `row_number()` over `transaction_id`, ordered by
      `purchase_date` desc, `order_value` desc. **Never dedup on `customer_id`.**

### glue-scripts/feature_engineer.py
- [x] `split_windows` - history: `purchase_date <= T`; holdout: `T < date <= SNAPSHOT`
- [x] `compute_rfm_features` - 11 columns, every window measured back from
      **FEATURE_CUTOFF**, never `max(purchase_date)` or `current_date()`
  - guard the divide-by-zero in `avg_basket_size_6m`
  - exclude `'unknown'` from `category_diversity_score`
  - cast to double
- [x] `assign_loyalty_tier` - Bronze <500, Silver <2000, Gold <5000, Platinum >=5000
- [x] `compute_churn_proxy` - scaled within each band, clamped to [0, 1]
- [x] `attach_churn_label` - left join on holdout customers; cast to **int**
      (Feature Store type is Integral, so it must serialize as `1`, not `1.0`)
- [x] Keep the DataFrame name `holdout` as the kit has it

### Local dry run before paying for Glue
- [x] Reproduce the logic locally (pandas or local PySpark) and hit these targets,
      measured from the real sample file:

      | Check                         | Target            |
      |-------------------------------|-------------------|
      | Raw rows                      | 163,255           |
      | Rows dropped, null customer_id| 3,265             |
      | Processed rows                | 157,627           |
      | Processed customers           | 9,999             |
      | Feature rows                  | 9,999 (1/customer)|
      | Churn rate                    | 22.0%             |
      | Loyalty tiers present         | all 4             |
      | `CUST-10000776` in features   | yes (verify uses it) |

## Phase 4 - Docs (no cost)

Status 2026-09-29: contract (9 measured guarantees), lineage .drawio + .png (rendered via
headless Chrome + diagrams.net viewer; draw.io desktop is no longer installed), README
rewritten, scripts/run-lab2-pipeline.sh added. Ready to commit.

- [x] `docs/lab2-data-contract.md` - sections Producer, Consumers, **Grain**,
      **Schema** (heading must start with `Schema`), **Quality Guarantees**
      (3+ measurable assertions, with numeric bounds), **SLA**, **Versioning**
- [x] `docs/lab2-data-lineage.png` (+ `.drawio` source) - source -> raw/ -> crawler ->
      catalog -> transform -> processed/ -> feature-engineer -> features/ + Feature Store.
      Every arrow labeled with format; every write arrow labeled with the IAM role.
- [x] README: new modules + how to run the pipeline end to end (2 pts)
- [ ] Commit everything so far

## Phase 5 - AWS session (COSTS MONEY - one sitting)

Status 2026-09-29 20:33: applied 20:16-20:19 (53 added, Domain took 1m55s), pipeline ran
via workflow 20:20-20:31 (transform 115 s, features 187 s, counts identical to the dry run),
verify 47/0. Fix applied live: ON_DEMAND start trigger enabled=false (it auto-fired the
crawler during apply). Windows CRLF fix in verify-lab2.sh's tally file. NAT still up.

- [x] `terraform plan` in `environments/dev` and read it
- [x] `terraform apply 2>&1 | tee ../../../docs/lab2-extend-output.txt`
      (~15 min; file must contain `aws_sagemaker_domain` and end `Apply complete!`)
- [x] Do not overwrite `lab2-extend-output.txt` on later applies
- [x] Run crawler -> confirm table `customers` exists in `northstar_dev`
- [x] Run transform job -> SUCCEEDED -> Parquet in `processed/customers/`
- [x] Run feature-engineer job -> SUCCEEDED -> Parquet in `features/customers/`
      (the ~10k PutRecord calls take several minutes)
- [x] If a job fails on permissions, wait ~30 s after an IAM fix before re-running
- [x] `bash scripts/verify-lab2.sh | tee docs/lab2-verify-output.txt` -> 0 failed
- [ ] Look at it in the console (learning, not graded): NAT Gateway, private route
      table, Domain network settings, Glue job run, Feature Group
- [ ] Commit and push

## Phase 6 - Teardown and submit

- [ ] `bash scripts/teardown-lab2.sh` -> "No billable Lab 2 resources remain"
- [ ] `docs/lab2-destroy-output.txt` ends with `Destroy complete!`
      (missing evidence caps Task 1 at half credit)
- [ ] Commit and push the destroy output
- [ ] `bash scripts/check-secrets.sh`
- [ ] `git tag lab2-submit && git push origin lab2-submit`
- [ ] Paste the repo URL into Canvas
