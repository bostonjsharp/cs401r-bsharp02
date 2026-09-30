# NorthStar Retail AI Platform - CS 401R

Terraform-managed ML platform for NorthStar's customer churn model, built up one
lab at a time. Lab 1 laid the foundation (VPC, S3, IAM, SageMaker Studio); Lab 2
hardened it and added the data pipeline that turns raw transactions into
labeled feature vectors.

```
raw/customers/ (CSV)
   -> Glue crawler -> catalog table northstar_dev.customers
   -> Glue job northstar-dev-transform          -> processed/customers/ (Parquet, 1 row per transaction)
   -> Glue job northstar-dev-feature-engineer   -> features/customers/  (Parquet, 1 row per customer)
                                                -> Feature Group northstar-dev-customer-features
```

Lineage diagram: `docs/lab2-data-lineage.png`. Data contract for the processed
dataset: `docs/lab2-data-contract.md`.

## Repository layout

```
infrastructure/
  environments/dev/      real AWS (remote state in S3 + DynamoDB lock)
  environments/local/    LocalStack: vpc, storage, iam only
  modules/
    vpc/                 VPC, public + private subnets, IGW, NAT Gateway, S3 gateway endpoint, Studio SG
    storage/             one versioned encrypted data bucket, four prefixes, five lifecycle rules
    iam/                 MLEngineer, DataEngineer, ModelMonitor roles and policies
    sagemaker/           Studio Domain (VpcOnly, private subnet) and the MLEngineer profile
    glue/        (Lab 2) catalog DB, CSV classifier, crawler, NETWORK connection + SG,
                         script + sample uploads, two ETL jobs, log groups, workflow + triggers
    feature_store/(Lab 2) the customer-features Feature Group (16 definitions, online + offline)
glue-scripts/
  transform.py           raw -> processed: trim, cast, parse dates, drop null customer_id, impute, dedup
  feature_engineer.py    processed -> features: temporal split at 2026-04-01, RFM features, tier, churn label
  local_dry_run.py       runs both scripts in the Glue 4.0 Docker image against the sample CSV
scripts/
  bootstrap-state.sh     one-time remote state setup
  run-lab2-pipeline.sh   runs crawler -> transform -> feature-engineer using terraform outputs
  verify-lab1.sh / verify-lab2.sh   rubric checks
  teardown-lab2.sh       terraform destroy plus the six orphans destroy leaves behind
  check-secrets.sh       run before every commit
docs/                    diagrams, ADR, cost estimate, apply/verify/destroy evidence
northstar-raw-sample.csv 163,255 deliberately dirty transaction rows; uploaded to raw/ by Terraform
```

## Lab 2 additions

### Platform hardening (modules changed)

| Module | Change | Why |
|---|---|---|
| `vpc` | private subnet `10.0.1.0/24`, Elastic IP + NAT Gateway in the public subnet, private route table (`0.0.0.0/0 -> NAT`), S3 gateway endpoint | SageMaker and Glue now run with no inbound path from the internet; the NAT gives them outbound access, the endpoint keeps S3 traffic off the NAT |
| `storage` | `aws_s3_bucket_lifecycle_configuration` with five rules | raw data expires after 90 days; old object versions are pruned on every prefix; `datacapture/` retention exists before Lab 5 writes there |
| `iam` | `DataEngineer` (trusts Glue, Lambda, SageMaker) and `ModelMonitor` (trusts SageMaker) | DataEngineer writes `raw/ processed/ features/` and the Feature Group, reads `artifacts/glue/`, cannot write `artifacts/`. ModelMonitor writes CloudWatch metrics and cannot write S3 at all |
| `sagemaker` | `app_network_access_type = VpcOnly`, subnet moved to the private subnet | Studio egress now routes through the VPC and the NAT |

Both NAT resources and the lifecycle configuration sit behind `enable_*`
variables that `environments/local` turns off, since LocalStack Community
emulates neither.

### Data pipeline (modules added)

`modules/glue` builds everything from one `dataset` variable (`customers`):
the crawler targets `raw/customers/`, names the catalog table `customers` (no
table prefix), the transform job writes `processed/customers/`, and the
feature job writes `features/customers/`. Both jobs run as DataEngineer on two
G.1X workers inside the private subnet through a Glue NETWORK connection, with
`max_retries = 0` and a 30-minute timeout. A Glue workflow chains the three
stages so one command runs the whole pipeline.

`modules/feature_store` declares the Feature Group: record identifier
`customer_id`, event time `event_time` (Fractional, epoch seconds), online
store on, offline store at `features/offline-store/` (kept apart from the job's
own `features/customers/` output), execution role DataEngineer.

### Beyond the handout

- The sample CSV, both Glue scripts, and the CloudWatch log groups are
  Terraform-managed (`aws_s3_object` with `source_hash`, `aws_cloudwatch_log_group`
  with retention), so `terraform apply` is the only setup step and
  `terraform destroy` leaves nothing behind.
- `aws_glue_workflow` + three triggers: `aws glue start-workflow-run` runs
  crawler -> transform -> feature-engineer, each stage gated on the previous
  one succeeding.
- Every name the scripts need is a Terraform output; `scripts/run-lab2-pipeline.sh`
  reads them instead of hardcoding.
- `glue-scripts/local_dry_run.py` exercises the real transform and feature
  functions in the official Glue 4.0 image before any paid job run.

## Running it

### 0. Prerequisites

Terraform >= 1.5, AWS CLI authenticated against the lab account, Docker (for
LocalStack and the local dry run), Python 3 with `pandas` and `pyarrow` for
the verify script's data-quality block.

### 1. Validate without spending anything

```bash
cd infrastructure && terraform fmt -check -recursive && cd ..
terraform -chdir=infrastructure/environments/dev validate
terraform -chdir=infrastructure/environments/local validate

# LocalStack: vpc + storage + iam (3 roles, no NAT)
make local-validate LOCAL_OUT=docs/lab2-localstack-output.txt
make local-destroy

# Glue scripts against the real sample, in the Glue 4.0 image
docker run --rm -v "$PWD:/work" -w /work amazon/aws-glue-libs:glue_libs_4.0.0_image_01 \
  spark-submit glue-scripts/local_dry_run.py --csv northstar-raw-sample.csv --out /work/.dryrun
```

### 2. Deploy (bills: NAT Gateway ~$0.045/h, Studio Domain, Glue DPU-minutes)

```bash
cd infrastructure/environments/dev
terraform init
terraform plan
terraform apply 2>&1 | tee ../../../docs/lab2-extend-output.txt   # ~15 min, the Domain is the slow part
cd ../../..
```

The apply also uploads `northstar-raw-sample.csv` to `raw/customers/` and both
job scripts to `artifacts/glue/`.

### 3. Run the pipeline end to end

```bash
bash scripts/run-lab2-pipeline.sh            # workflow: crawler -> transform -> feature-engineer
bash scripts/run-lab2-pipeline.sh --steps    # or one stage at a time
```

Manually, the same thing is:

```bash
W=$(terraform -chdir=infrastructure/environments/dev output -raw glue_workflow_name)
aws glue start-workflow-run --name "$W"
# or per stage:
aws glue start-crawler --name northstar-dev-raw-crawler
aws glue start-job-run --job-name northstar-dev-transform
aws glue start-job-run --job-name northstar-dev-feature-engineer
```

Driver output (row counts at each step) streams to the CloudWatch log group
`/aws-glue/jobs/logs-v2`. Expected on the sample: 163,255 raw rows, 3,265
dropped for null `customer_id`, 157,627 processed rows, 9,999 feature rows,
churn rate 22.0%.

### 4. Verify, then tear down

```bash
bash scripts/verify-lab2.sh | tee docs/lab2-verify-output.txt   # expects 0 failed
bash scripts/teardown-lab2.sh                                    # destroy + orphan cleanup, writes docs/lab2-destroy-output.txt
bash scripts/check-secrets.sh
```

`terraform destroy` alone is not enough here: Glue ENIs, the Studio EFS, the
SageMaker NFS security groups, S3 object versions, the `sagemaker_featurestore`
catalog database, and Feature Store lineage entities all outlive it. The
teardown script removes each in the right order and then checks the live API
for anything still billing.

## Evidence

| File | What it shows |
|---|---|
| `docs/lab2-localstack-output.txt` | three roles, VPC, both subnets, no NAT on LocalStack |
| `docs/lab2-extend-output.txt` | `terraform apply` against AWS, ends `Apply complete!` |
| `docs/lab2-verify-output.txt` | `verify-lab2.sh`, 0 failed |
| `docs/lab2-destroy-output.txt` | `terraform destroy`, ends `Destroy complete!` |
| `docs/lab2-teardown-verification.txt` | live API check after teardown: nothing billable remains |
| `docs/lab2-data-contract.md` | contract for `processed/customers/` |
| `docs/lab2-data-lineage.png` (+ `.drawio`) | lineage with formats and roles on every edge |

Lab 1 evidence and the ADR live alongside them under `docs/lab1*`.
