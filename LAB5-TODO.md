# Lab 5 To-Do: Deployment & Scaling + Security

Due **Sat Nov 14, midnight**. Tag `lab5-submit`, push the tag, paste the repo URL in Canvas.
Points: Task 1 Production deployment (30) + Task 2 Deployment plan (20) + Task 3 Security
assessment (25) + Task 4 Privacy & compliance (15) + Task 5 Repository quality (10) = 100.
**Teardown is a gate**: without `docs/lab5-teardown-output.txt` Task 1 is capped at half.
An endpoint still running after the deadline is a further **-10**. The TA grades the repo at
the tag, never the AWS account: CLI output committed under `docs/` is the evidence, and most
of Task 1's evidence can only be captured **while the endpoint is live**.

Handout: `C:\Users\bosto\Downloads\Lab_5--Deployment & Security.html` (includes the Nov 5 and
Nov 7 changes and the "Submitting Lab 5" section with the capture blocks).
Starter kit: `C:\Users\bosto\Downloads\Lab5-Starter-Kit.zip` = one file,
`scripts/teardown-lab5.sh` (byte-identical to the GitHub template). Not copied in yet.

Suggested pacing: Phases 0-2 (free) Tue-Wed, Phase 3 (the paid sitting, ~3 h) Thu or Fri,
Phase 4 right after. Do not leave Phase 3 for Saturday.

### Errata received 2026-11-10 (read first)

1. **Teardown script now ships in a kit.** Put it at `scripts/teardown-lab5.sh`. It writes
   `docs/lab5-teardown-output.txt`, the gate file. (We have not deployed, so the "tore down
   by hand" alternative does not concern us.)
2. **Do not uncomment the bandit block in `buildspec.yml`** (`if highs:` reads as a YAML
   mapping key; CodeBuild rejects the file). Use the three lines in `pre_build` instead:
   `echo "Running security scan"`, `pip install --quiet bandit`, `bandit -r models data -lll`.
3. **Prerequisite 5**: MLEngineer needs `s3:PutObject` + `s3:AbortMultipartUpload` on
   `datacapture/*`, or data capture fails silently (endpoint healthy, nothing written).
   Not graded in Lab 5; Lab 6 needs it.
4. **Prerequisite 6**: the `expire-datacapture` lifecycle rule needs
   `noncurrent_version_expiration { noncurrent_days = 1 }` **inside the existing rule**
   (versioned bucket: expiry alone only hides objects; `verify-lab2.sh` wants exactly five
   rules). Not graded; our Task 4 retention claim depends on it.
5. **Replace `scripts/teardown-lab2.sh`** with the current Lab 2 kit version before running
   it again (the old one deletes every lineage record in the account and reports leftover
   lineage as a billing failure). Also wait ~15 min after the last Glue job before running it.

The handout also added an **Evidence** column to every rubric table and a "Submitting Lab 5"
section. No criteria or points changed.

---

## What we are building, in one paragraph

One real-time SageMaker endpoint, `northstar-churn-prod`, on `ml.m5.large`, with two
production variants: `champion` (registry v4, weight 9) and `canary` (registry v5, weight 1),
both built from Approved model packages in `northstar-churn-models`, executing as
`northstar-dev-MLEngineer`, with `DataCaptureConfig` writing inputs and outputs to
`s3://northstar-dev-data-829485866627/datacapture/` for Lab 6. Around it, all as code in
`deployment/configs/`: a target-tracking auto-scaling policy
(`SageMakerVariantInvocationsPerInstance` = 1000, cooldowns 60 s out / 600 s in), a
CloudWatch rollback alarm `northstar-churn-rollback-latency` (canary `ModelLatency` p95 >
**200000 microseconds**), and `rollback-action.sh`
(`update-endpoint-weights-and-capacities`, canary weight 0). We observe the 9:1 split for
60 minutes (standing in for the 48-hour production window the plan documents), prove the
split by counting `InvokedProductionVariant` over 200 calls, roll back and prove 0 canary
calls over 100, then delete everything and capture the teardown. Tasks 2-4 are two documents:
a 600-900 word deployment plan a stranger could execute, and a security + privacy assessment
(STRIDE, seven-asset classification, GDPR assessment with an erasure workflow). Task 5 is the
bandit step in `buildspec.yml`, clean secrets, and the config-as-code directory.

---

## Possible errata and our deviations (found 2026-11-10, before any implementation)

| # | What | Why it matters | What we do |
|---|---|---|---|
| **D1** | **No deployable model exists.** Registry: v1 Pending (Lab 3), v2 Rejected, v3 Pending. v3's `ModelDataUrl` is `s3://northstar-dev-data-829485866627/artifacts/training/...`, in the bucket the Lab 4 teardown destroyed. The handout assumes "the trained model already exists". | `create-model` from v3 would fail at endpoint creation (artifact 404), after billing started. | Phase 3 steps 1-5 re-apply the Lab 2 stack, re-run the feature pipeline and register **v4 and v5** through the Lab 4 CI stack. Never approve v1 or v3. |
| **D2** | **Quota wall 2 does not apply to us.** `ml.m5.large for endpoint usage` (L-614B09FD) applied value is **30** (checked 2026-11-10); no request history. | The handout's "file the request on day one" path and the t2.medium fallback are unnecessary. Rubric gives full credit for (a) a live scaling policy on a non-burstable instance. | Deploy straight on `ml.m5.large`. Re-check the quota at Phase 3 step 6. Wall 1 (burstable rejection) is optional, see step 8. The plan's section 4 still explains the burstable constraint in one sentence. |
| **D3** | Prereq 5's snippet uses `${local.bucket_arn}`. Our IAM module's local is `local.data_bucket_arn` (`infrastructure/modules/iam/main.tf:26`, wildcard on the account suffix). | Pasting verbatim fails `terraform validate`. | Phase 0: write `"${local.data_bucket_arn}/datacapture/*"`. |
| **D4** | Prereq 6 shows a literal `rule { ... }` block. Our rules are a map driving dynamic blocks (`infrastructure/modules/storage/main.tf:85-93`). | Pasting a block would add a sixth rule or a syntax error. | Phase 0: change the `expire-datacapture` map entry to `noncurrent_days = 1`. The existing dynamic `noncurrent_version_expiration` block does the rest. Still five rules. |
| **D5** | The handout's helpers call `python3` and use `/tmp/channel`. In Git Bash, `python3` is 3.13 **without boto3/pandas**, and a native Windows Python does not see Git Bash's `/tmp`. | `traffic` and the `sample.csv` snippet both fail on this machine. | Use `.venv/Scripts/python` in `traffic`; build `sample.csv` from `.dryrun/features` (free, same 9,999 customers) or from a repo-relative download dir. |
| **D6** | Two variants need two models. The handout never says where the second comes from. | A canary of the same package against itself is legal but hollow. | Two CI runs: v4 = champion (approved first), v5 = canary (its regression test then runs against champion v4 for real). Same code and data, so scores match; that is what a clean canary looks like. |
| **D7** | "Before you tag" says a push with the CI stack still deployed costs "$0.02". On our account a run is ~5 min CodeBuild SMALL + spot training, ~$0.10-0.15 (Lab 4 E5). | Budget only. | Delete the CI stack before the final docs push (Phase 3 step 15). |
| **D8** | `teardown-lab5.sh` deletes **every** endpoint, endpoint config and SageMaker model in the account, and alarms prefixed `northstar-churn-rollback`. | Fine for us (nothing else lives there), but the alarm must use that prefix or it survives teardown and is missed by capture section 3. | Alarm name `northstar-churn-rollback-latency`. |
| D9 | The capture block's section 2 re-runs `register-scalable-target` from `scalable-target.json`. | On m5.large this is an idempotent update; it must be the same file we registered with. | One scalable target file, for the `champion` variant. |
| D10 | Lab 4 rule: no Approved package may point at a deleted artifact. Lab 5 needs v4/v5 Approved in capture section 0. | If the data bucket is destroyed at the end, v4/v5 become Approved-but-undeployable. | Capture section 0 first (window 1). At final teardown, if the Lab 2 stack is destroyed, set v4 and v5 to Rejected and say so in the plan's cleanup section. |
| D11 | Lab 6 "redeploys the endpoint and analyzes the captured data". A full Lab 2 teardown deletes the artifact and `datacapture/`. | Lab 6 will need a model again. | Before teardown, copy `model.tar.gz` for v4/v5 into local `model/` (gitignored). Decide at step 16 whether to keep the Lab 2 stack up (NAT ~$1.20/day) based on when Lab 6 starts. |

Things that **check out** (so nobody re-verifies them): the Lab 5 kit script equals the
GitHub template; the new Lab 2 teardown script (fetched 2026-11-10) keeps our per-page
`awk` sum fix and adds a credentials guard; `.gitignore` already excludes `.env`, `*.tfvars`,
`*.tfstate`; the registered package lists `ml.m5.large` and `ml.t2.medium` as supported
real-time instances and `text/csv` as content type
(`models/churn/pipeline_definition.py:155-157`); `FEATURE_COLUMNS` has 11 names
(`models/churn/churn_model.py:41`); `model_dir` holds only `xgboost-model`, which is what the
1.7-1 serving container wants; the Feature Group has the **online store enabled**
(`infrastructure/modules/feature_store/main.tf:37`), which the Task 4 erasure answer needs;
MLEngineer already has S3 read on `artifacts/*`, ECR pull, `/aws/sagemaker/*` logs and
`PutMetricData`, i.e. everything an endpoint execution role needs except prereq 5; the
GitHub connection `northstar-github` and the MLflow App survived Lab 4.

---

## COST MAP (read before any AWS step)

Budget alarm for the course: **$10/month**. Lab 4's sitting cost about $2.

| Thing | Cost | Needed? | Rule |
|---|---|---|---|
| **Endpoint, two `ml.m5.large` variants** | $0.115/hr each = **$0.23/hr**, from InService until **deleted** | YES | Up ~1.5-2 h (7 min create + captures + 60 min window + rollback). Weight 0 does not stop the bill; only `delete-endpoint` does. Never leave it overnight |
| Lab 2 stack: NAT Gateway + public IPv4 | ~$0.05/hr | YES (data bucket for the artifact and `datacapture/`, MLEngineer role, Glue feature job) | Up for the sitting only |
| Lab 2 full pipeline | ~$0.25 | YES, once | `bash scripts/run-lab2-pipeline.sh` |
| CI runs (CodeBuild SMALL + spot training) | ~$0.10-0.15 each | YES, two (v4, v5) | Budget 4 in case bandit or freshness bites |
| Auto-scaling, CloudWatch alarm (first 10 free), data capture storage | ~$0 | YES | Deleted by `teardown-lab5.sh` / expired by lifecycle |
| Optional Wall 1 demo on `ml.t2.medium` | $0.056/hr, ~10 min | NO | Skip unless we want the rejection text in the evidence |
| Batch Transform | $0.115/hr, quota 0 | NOT taken | Real-time path only |
| Studio apps, MLflow Tracking Server | $0.05-0.60/hr | NOT needed | Do not open Studio; the MLflow App is free |

Working estimate: **$1.50-3** for one sitting.

---

## Where things stand (2026-11-10)

- AWS account `829485866627`, `us-east-1`: nothing billable is up. No CloudFormation stacks,
  no endpoints, no SageMaker models, no NAT. Buckets: `northstar-tfstate-829485866627` and
  two SageMaker-default buckets. Free leftovers: MLflow App, model package group (v1-v3),
  GitHub connection.
- Endpoint quota `ml.m5.large` = 30 (D2). Spot training quota 10 (Lab 4).
- Repo: `main` clean at `53bec72`. No `deployment/` directory, no `scripts/teardown-lab5.sh`,
  no `docs/lab5-*`. `buildspec.yml:111-121` still has the commented bandit block.
  `bandit` is not installed in `.venv`.
- Local data for free work: `.dryrun/features/*.parquet` (9,999 rows, 16 columns).
- `terraform apply` / `terraform destroy` / bulk deletes: **Boston runs them** with `!`
  (classifier-blocked for Claude). AWS reads from Python go through `.venv` boto3 (the
  Windows AWS CLI chokes on non-ASCII output).

---

## Phase 0 - Setup (FREE)

- [x] Copy the kit: `scripts/teardown-lab5.sh` from the zip. Add, do not replace anything.
      Read it once: five delete steps, then six `check` lines that must all read `OK`.
- [x] Replace `scripts/teardown-lab2.sh` with the current Lab 2 template version
      (`https://raw.githubusercontent.com/byu-cs401r4-f26/cs401r-lab2-template/main/scripts/teardown-lab2.sh`).
      `diff` against ours first; the only differences should be the credentials guard, the
      feature-group-only lineage step, `check` failing on a failed AWS call, and lineage
      reported with `info` instead of `check`.
- [x] **Prereq 5** (D3), `infrastructure/modules/iam/main.tf`, MLEngineer policy, next to
      `S3ArtifactsAndFeatures`: statement `S3DataCaptureWrite`, actions `s3:PutObject` and
      `s3:AbortMultipartUpload`, resource `"${local.data_bucket_arn}/datacapture/*"`. Comment
      it as Lab 5 and say why (silent capture failure). Update the header comment's Lab list.
- [x] **Prereq 6** (D4), `infrastructure/modules/storage/main.tf:92`:
      `expire-datacapture = { prefix = "datacapture/", current_days = 7, noncurrent_days = 1 }`
      and fix the comment above it (Lab 5 is now the writer).
- [x] `terraform fmt -check`, `terraform validate`, `terraform plan` in
      `infrastructure/environments/dev` (free; stack is down so expect ~53 adds). Not applied.
- [x] **Task 5 security step.** In `buildspec.yml` `pre_build`, replace the commented bandit
      block (lines 111-121) with the three errata lines, after the feature tests and before
      the final echo. No colon inside any `echo` string (trap 9). Then locally:
      `pip install bandit` in `.venv`, `bandit -r models data -lll` must exit 0 (HIGH only).
      If it reports a HIGH, fix the code; do not add `# nosec` without a reason in the comment.
      Validate the YAML still parses:
      `python -c "import yaml,sys; yaml.safe_load(open('buildspec.yml', encoding='utf-8'))"`.
- [x] `mkdir -p deployment/configs` and build `deployment/configs/sample.csv` (D5): one
      header-less row, the 11 `FEATURE_COLUMNS` in order, as floats, from `.dryrun/features`
      with `.venv/Scripts/python`. Check: exactly 11 comma-separated numbers, no
      `customer_id`, no label.
- [x] `.gitignore`: confirm `.env`, `*.tfvars`, `*.tfstate` are still there (graded) and that
      `deployment/` is not ignored. `bash scripts/check-secrets.sh` prints CLEAN.

## Phase 1 - Deployment config as code (FREE; Task 1 + Task 5)

Every file is written so the matching CLI call is `--cli-input-json file://...`. Keep all
descriptions ASCII. Model names are fixed now and created in Phase 3:
`northstar-churn-v4` (champion), `northstar-churn-v5` (canary). If CI registers different
version numbers, rename in the JSON before creating anything.

- [x] `deployment/configs/endpoint-config-canary.json` (`create-endpoint-config`):
      `EndpointConfigName` `northstar-churn-canary`; `ProductionVariants`:
      `champion` -> `northstar-churn-v4`, `canary` -> `northstar-churn-v5`, each
      `InstanceType` `ml.m5.large`, `InitialInstanceCount` 1, `InitialVariantWeight` 9 and 1;
      `DataCaptureConfig`: `EnableCapture` true, `InitialSamplingPercentage` 100,
      `DestinationS3Uri` `s3://northstar-dev-data-829485866627/datacapture/`,
      `CaptureOptions` Input and Output, `CaptureContentTypeHeader.CsvContentTypes`
      `["text/csv"]`. Endpoint configs are immutable: capture cannot be added later.
- [x] `deployment/configs/rollback-alarm.json` (`put-metric-alarm`): `AlarmName`
      `northstar-churn-rollback-latency` (D8); `Namespace` `AWS/SageMaker`; `MetricName`
      `ModelLatency`; `Dimensions` `EndpointName=northstar-churn-prod`, `VariantName=canary`;
      `ExtendedStatistic` `p95` (the capture block queries `ExtendedStatistic`); `Period` 60;
      `EvaluationPeriods` 3; `Threshold` **200000**; `ComparisonOperator`
      `GreaterThanThreshold`; `TreatMissingData` `notBreaching`. The number, statistic and
      period here are the ones the plan's section 3 must quote exactly.
- [x] `deployment/configs/scalable-target.json` (`register-scalable-target`):
      `ServiceNamespace` `sagemaker`; `ResourceId`
      `endpoint/northstar-churn-prod/variant/champion`; `ScalableDimension`
      `sagemaker:variant:DesiredInstanceCount`; `MinCapacity` 1; `MaxCapacity` 2.
- [x] `deployment/configs/scaling-policy.json` (`put-scaling-policy`): `PolicyName`
      `northstar-churn-invocations-tt`; same namespace / resource / dimension; `PolicyType`
      `TargetTrackingScaling`; `TargetValue` 1000.0; `PredefinedMetricType`
      `SageMakerVariantInvocationsPerInstance`; `ScaleOutCooldown` 60; `ScaleInCooldown` 600.
- [x] `deployment/configs/rollback-action.sh`: `aws sagemaker
      update-endpoint-weights-and-capacities --endpoint-name northstar-churn-prod
      --desired-weights-and-capacities VariantName=champion,DesiredWeight=1
      VariantName=canary,DesiredWeight=0`, then `aws sagemaker wait endpoint-in-service`.
      Header comment: does not stop billing; takes ~90 s; endpoint keeps serving.
- [x] `deployment/deploy-canary.sh` (ours; makes the plan executable by a stranger and keeps
      the plan under 900 words): resolves `ROLE` from `terraform output -raw
      ml_engineer_role_arn`, takes the two package ARNs as arguments, runs `create-model`
      twice (`--containers ModelPackageName=<arn>`), `create-endpoint-config` from the JSON,
      `create-endpoint`, `wait endpoint-in-service`, then registers the scalable target,
      puts the scaling policy and puts the alarm, each from its JSON. `set -euo pipefail`;
      refuses to run if either package is not `Approved`.
- [x] Shape check without AWS cost: `python -m json.tool` on each JSON; compare keys against
      `aws sagemaker create-endpoint-config --generate-cli-skeleton` (and the three others).
      `grep '"Threshold"' deployment/configs/rollback-alarm.json` shows 200000.

## Phase 2 - Documents that need no AWS (FREE; Tasks 2, 3, 4)

Status 2026-11-12: Phases 1 and 2 written and checked offline (JSON validated against the
botocore API models, `bash -n` on both scripts, plan at 859 words including the empty
execution-record table). Measured values are still `TBD` in the plan's last table; filling
them adds roughly 20 words, so keep the plan under 900. Not committed yet.

Draft now, fill measured numbers (latency, observed split, timings, version numbers) after
Phase 3.

### 2a. `docs/lab5-deployment-plan.md` (Task 2, plus Task 1 justification; 600-900 words)
`wc -w` counts code blocks, so reference `deployment/deploy-canary.sh`,
`deployment/configs/rollback-action.sh` and `scripts/teardown-lab5.sh` instead of inlining
long commands. Seven numbered sections, in the handout's order (the rubric cites them by number):
- [x] **1. Deployment strategy and rationale.** Real-time canary. Justify from NorthStar's
      scoring-frequency requirement (read `northstar-scenario-overview.md` for what the
      business actually asks for; cite it), not generic latency/throughput talk. Say why not
      batch and why canary over blue/green for this system.
- [x] **2. Pre-deployment checklist.** Includes the approval step: which **role** approves
      (e.g. ML lead), against which metrics (`auc_roc`, `precision_top10`, `recall_top10`,
      lift CI, fairness flag, from the package's `evaluation_metrics.json`), the exact
      `update-model-package` command, quota check, features fresh, prereqs 5-6 applied,
      alarm in OK.
- [x] **3. Rollback criteria.** Metric `ModelLatency` p95 on the canary variant, threshold
      200000 microseconds (200 ms), 3 consecutive 1-minute periods; identical to
      `rollback-alarm.json`. Who makes the call (a role). The action: `rollback-action.sh`.
- [x] **4. Monitoring window.** Production: 48 h at 9:1 before promotion. Lab: 60 min. State
      the compression explicitly: what 48 h catches that 60 min cannot (daily and weekly
      traffic cycles, the nightly feature refresh landing, slow drift, memory growth, a
      full business cycle of retention outcomes) and what we would additionally monitor
      (5XX rate, score distribution per variant from data capture, downstream campaign
      metrics). Auto-scaling: the policy, and one sentence on why burstable `ml.t2.*`
      cannot be a scalable target (CPU credits, not sustained capacity). Define "clean".
- [x] **5. Stakeholder notifications.** Roles, not people; what each receives at deployment
      start, promotion, rollback. A small table.
- [x] **6. Post-deployment review.** When, who attends, what artifact it produces.
- [x] **7. Resource cleanup.** Order and verification: `teardown-lab5.sh`, the six `OK`
      lines, the three live-API counts; D10 (approvals reset if the artifact bucket goes).
- [x] Stranger test: every name defined, every command copy-pasteable from the repo root.

### 2b. `docs/lab5-security-assessment.md` (Tasks 3 and 4)
- [x] **STRIDE table** (15 pts): at least 5 threats across at least 3 categories; columns
      Threat, STRIDE Category, Likelihood, Impact, Mitigation, AWS Control; every cell
      filled; every mitigation names an AWS service **and** the control (not "use
      encryption"). At least one row names which Lab 2 role contains the blast radius
      (candidates: a compromised training job runs as MLEngineer and cannot read `raw/` or
      `processed/`; ModelMonitor cannot write S3 or invoke). At least one row names a
      **gap the roles do not close** (candidates, all true of our repo: `SageMakerCore`
      is `Resource "*"`, so MLEngineer can delete any endpoint; no `sagemaker:InvokeEndpoint`
      boundary or VPC endpoint policy on the endpoint; SSE-S3 means no key-level audit or
      separation; data capture stores raw inputs; no CloudTrail data events on the bucket).
      Aim for 6-7 rows over 4+ categories for margin.
- [x] **Data classification table** (10 pts): all seven assets (customer PII, transaction
      history, behavioral clickstream, product catalog, model weights, inference logs,
      Feature Store records); for each: tier (Public / Internal / Confidential /
      Restricted), encryption (SSE-S3, SSE-KMS or CSE) **with a stated reason for the
      choice**, IAM access policy (name our roles and prefixes), third-party sharing
      yes/no/conditions. Be honest that today everything is SSE-S3
      (`modules/storage/main.tf:54`) and say what should move to SSE-KMS and why.
      Clickstream and catalog are not in our pipeline; classify them as NorthStar assets anyway.
- [x] **Privacy impact assessment** (15 pts), four sections:
      1. Personal data inventory by pipeline stage (raw CSV, processed, features, training
         set, endpoint payloads, data capture).
      2. Lawful basis: choose **one** and justify for NorthStar's retention use case.
      3. Data minimization: what was considered and excluded (check
         `northstar-data-schema.md`, `docs/lab2-data-contract.md`, `preprocess_features`
         dropping `customer_id`).
      4. Right to erasure for `customer_id = C00123456`: concrete step for (a) raw S3
         (versioned: delete all versions, not just the current object), (b) Feature Store,
         **online** (`DeleteRecord`) vs **offline** (append-only Parquet history; rewrite
         or hard-delete objects), (c) model training data and the trained model, (d)
         inference logs (capture rows carry no `customer_id`, so they cannot be located
         per customer; the 7-day + 1-day lifecycle from prereq 6 is the control). Name the
         hardest step and why it is not a delete operation (the trained model, or the
         offline store's history).
- [x] README: a "Lab 5: deployment and security" section (layout of `deployment/`, how to
      deploy, capture and tear down, what `docs/lab5-*` contains).
- [ ] Commit Phases 0-2. Pushing is free while no CI stack exists.

## Phase 3 - AWS session (**COSTS MONEY**: one sitting, same-day teardown)

Ask before step 1. Expected wall clock ~3 h, expected spend $1.50-3.
Tags: **[$]** bills, **[free]** API calls only, **[Boston]** run by hand with `!`.

1. **[$] [Boston] Re-deploy the Lab 2 stack** with prereqs 5 and 6:
   `terraform plan -out=tfplan` then `terraform apply tfplan` in
   `infrastructure/environments/dev`. If it fails on `aws_s3_object` refresh, run it again
   (trap 7). NAT meter starts. Wait 30 s after apply (IAM lag).
2. **[$] Populate the bucket**: `bash scripts/run-lab2-pipeline.sh` (~6 min). Note the time
   the last Glue job ends (15-minute rule for teardown, errata 5).
3. **[free] Confirm prereq 6**: `aws s3api get-bucket-lifecycle-configuration --bucket
   northstar-dev-data-829485866627 --query "Rules[?ID=='expire-datacapture'].[Expiration.Days,NoncurrentVersionExpiration.NoncurrentDays]" --output text`
   prints `7  1`, and `length(Rules)` is 5.
4. **[$] CI stack, run 1 -> v4.** Push `main` first (it must contain the bandit step).
   Recreate the artifacts bucket (`northstar-dev-cicd-artifacts-829485866627`, versioning
   on), confirm the connection is `AVAILABLE`, then the Lab 4 `aws cloudformation deploy`
   command (LAB4-TODO Phase 4 step 5). Stack creation starts run 1. In its log confirm
   `Running security scan` and bandit's summary, `Pipeline succeeded`, `Registered: arn:...`
   (v4). Review v4's metrics, then **approve v4** (`update-model-package
   --model-approval-status Approved`). Record who/what/when for the plan's section 2.
5. **[$] Run 2 -> v5.** `aws codepipeline start-pipeline-execution --name northstar-pipeline`
   (no push needed). Its regression test now compares against champion v4. On green,
   review and **approve v5**. Optional: save this build log as
   `docs/lab5-build-security.log` (not graded; shows the security step ran in CI).
6. **[free] Pre-flight**: quota L-614B09FD >= 2; both packages `Approved`; both
   `ModelDataUrl` objects exist (`aws s3 ls`); model names in `endpoint-config-canary.json`
   match the versions; alarm JSON threshold 200000.
7. **[$] Deploy**: `bash deployment/deploy-canary.sh <v4-arn> <v5-arn>`. ~7 min to
   InService. **$0.23/hr from here.** Note the InService time.
8. **[free] Smoke test** each variant directly:
   `aws sagemaker-runtime invoke-endpoint --endpoint-name northstar-churn-prod --content-type text/csv --target-variant canary --body fileb://deployment/configs/sample.csv out.txt`
   (and `champion`). Each returns one probability in [0, 1]. Check the latency unit once:
   `get-metric-statistics` for `ModelLatency` shows `Unit: Microseconds`, values in the
   low thousands. Optional Wall 1 (skip by default): not needed, D2.
9. **[free] Confirm scaling and alarm are live**: `describe-scalable-targets`,
   `describe-scaling-policies` (policy plus the two `TargetTracking-*` alarms),
   `describe-alarms --alarm-name-prefix northstar-churn-rollback` reads `OK` or
   `INSUFFICIENT_DATA`, **not** `ALARM`.
10. **[free] WINDOW 1 capture** (before any weight shift). Paste the handout's helpers with
    D5's change (`.venv/Scripts/python` in `traffic`), then the window-1 block. It creates
    `docs/lab5-deployment-output.txt` with sections 0-4. **Read the file**: section 0 shows
    v4 and v5 Approved; section 1 both variants, weights 9 and 1, `ml.m5.large`; section 2
    the target, the policy and (empty) quota history; section 3 the JSON and the alarm
    state; section 4 roughly 180 / 20 over 200 (reference run: 175 / 25). Any
    `AccessDenied` / `Could not connect` line means that section captured nothing: re-run now.
11. **[$] 60-minute window.** Send traffic every few minutes
    (`for i in $(seq 12); do traffic 50; sleep 300; done`) so the metrics have data. While
    it runs: `aws s3 ls s3://northstar-dev-data-829485866627/datacapture/ --recursive | head`
    lists `.jsonl` files (prereq 5 proof; if empty after 5 min of traffic, the IAM
    statement is wrong, fix it now, because Lab 6 depends on it); note p95 latency per
    variant, 4XX/5XX counts, alarm state. These numbers go into the plan and the review.
12. **[free] Rollback (required even though the canary is healthy)**:
    `bash deployment/configs/rollback-action.sh`, wait for InService (~90 s), then
    **WINDOW 2** block: appends section 5, weights `champion 1 / canary 0`, and
    `traffic 100` must show **only** champion. Optional 5b: promote the canary (weights
    0 / 1), capture, label it `5b`.
13. **[free] Before deleting anything**: copy both `model.tar.gz` artifacts and their
    `evaluation_metrics.json` to local `model/` (D11). Decide D11's stack question.
14. **[stops the endpoint meter] `bash scripts/teardown-lab5.sh`.** Writes
    `docs/lab5-teardown-output.txt`; all six lines `OK`. Then the **WINDOW 3** block:
    appends section 6, three counts all 0.
15. **[free] CI teardown** (same as LAB4-TODO step 11.1-11.3): delete stack `northstar-cicd`
    and wait, empty and delete the artifacts bucket, delete log group
    `/aws/codebuild/northstar-ci` (`MSYS_NO_PATHCONV=1`). After this, pushes run nothing.
16. **[Boston] Lab 2 teardown** (unless keeping the stack for Lab 6): at least 15 min after
    the last Glue job, with the **new** script, `bash scripts/teardown-lab2.sh`. If destroy
    hangs on the subnet, run the handout's Glue-ENI loop in a second terminal. Then set v4
    and v5 to `Rejected` (D10). Lineage lines now read "left (metadata, not billed)".
17. **[free] Billing sweep** (read-only): no endpoints, endpoint configs, models, scalable
    targets, NAT gateways, Elastic IPs, CloudFormation stacks, CodeBuild projects.
18. Fill the measured numbers into both documents; re-check the plan's word count.

## Phase 4 - Submit

- [ ] The handout's "Before you tag" checks, all from the repo root:
      `grep -c '^===== ' docs/lab5-deployment-output.txt` = 7 (8 with 5b);
      `grep '"Threshold"' deployment/configs/rollback-alarm.json` = 200000;
      `wc -w docs/lab5-deployment-plan.md` in 600-900;
      `grep -c ' OK$' docs/lab5-teardown-output.txt` = 6;
      `bash scripts/check-secrets.sh` = CLEAN.
- [ ] Every file in the rubric table below exists at its exact name.
- [ ] CI stack is gone (or accept one paid run on push).
- [ ] `git add docs/ deployment/ buildspec.yml .gitignore scripts/ infrastructure/ README.md LAB5-TODO.md`,
      commit, `git tag lab5-submit && git push origin main lab5-submit`.
- [ ] Paste the repo URL in Canvas. `aws sagemaker list-endpoints` is empty one last time.

---

## Rubric cross-reference

| Points | Item | Closed by | Evidence (exact file names) |
|---|---|---|---|
| 5 | Approach justified from NorthStar's scoring frequency | Phase 2a section 1 | `docs/lab5-deployment-plan.md` section 1 |
| 12 | Canary configured, observed split from >= 100 invocations | Phase 1, Phase 3 steps 7, 10, 12 | `docs/lab5-deployment-output.txt` sections 1, 4, 5; `deployment/configs/endpoint-config-canary.json` |
| 8 | Rollback trigger numeric, as code, correct units | Phase 1, Phase 3 steps 9-10, 12 | `deployment/configs/rollback-alarm.json`; output section 3; `deployment/configs/rollback-action.sh` |
| 5 | Auto-scaling live on non-burstable; 48 h -> 60 min compression | Phase 1, Phase 3 steps 9-10, Phase 2a section 4 | output section 2; `deployment/configs/scalable-target.json`, `scaling-policy.json`; plan section 4 |
| 10 | Plan executable by a stranger | Phase 2a | `docs/lab5-deployment-plan.md` |
| 6 | Rollback criteria numeric, consistent with the alarm | Phase 2a section 3 | plan section 3 vs `rollback-alarm.json` |
| 4 | Stakeholder notifications: roles, per stage | Phase 2a section 5 | plan section 5 |
| 10 | >= 5 STRIDE threats, >= 3 categories, one role-mapped, one gap | Phase 2b | `docs/lab5-security-assessment.md` STRIDE table |
| 5 | Mitigations name AWS service + control | Phase 2b | same |
| 10 | All 7 assets classified, SSE-KMS vs SSE-S3 reasoned | Phase 2b | classification table |
| 5 | Lawful basis justified | Phase 2b | privacy impact assessment |
| 6 | Deletion workflow covers 4 stores, online vs offline | Phase 2b | privacy impact assessment |
| 4 | Hardest step identified with reasoning | Phase 2b | privacy impact assessment |
| 4 | No credentials in code | Phase 0, Phase 4 | all history via `scripts/check-secrets.sh`; `.gitignore` |
| 3 | Deployment config is code | Phase 1 | `deployment/configs/endpoint-config-canary.json`, `scalable-target.json`, `scaling-policy.json`, `rollback-alarm.json` |
| 3 | CI/CD extended with a security check | Phase 0, Phase 3 step 4 | `buildspec.yml` |
| req | `sample.csv` payload | Phase 0 | `deployment/configs/sample.csv` |
| gate | Teardown evidence (Task 1 capped at half without it) | Phase 3 step 14 | `docs/lab5-teardown-output.txt`, six `OK` lines; output section 6 |
| -10 | Endpoint running after the deadline | Phase 3 step 14 | teardown file timestamp before the deadline |
| req | Never commit secrets (automatic zero) | Phase 4 | `scripts/check-secrets.sh` |

---

## Gotchas collected from the handout, the kit and our own checks

- Endpoints bill from InService until **deleted**. Rolling back to weight 0 changes nothing
  on the bill.
- `ModelLatency` is in **microseconds**. 200 ms = 200000. A threshold of 200 alarms
  instantly on a healthy endpoint (normal is ~4,100) and fails the 8-point rubric item.
- Endpoint configs are immutable. Data capture, instance type and variants are decided in
  the JSON before `create-endpoint`.
- Burstable `ml.t2.*` / `ml.t3.*` cannot be scalable targets. We avoid it on `ml.m5.large`.
- Data capture fails **silently** without prereq 5. Check for `.jsonl` during the window.
- On a versioned bucket, expiration only adds a delete marker; prereq 6 is what makes the
  7-day retention claim in Task 4 true.
- The alarm name must start with `northstar-churn-rollback`: capture section 3 and
  `teardown-lab5.sh` both look for that prefix. A bare `northstar` prefix would have matched
  the Lab 4 CI alarm; do not widen it.
- `teardown-lab5.sh` deletes every endpoint, endpoint config and model in the account.
  Use it, not `teardown-lab3.sh` (wrong evidence file, misses models and alarms).
- Deleting the endpoint deregisters the scalable target and removes its policies and
  `TargetTracking-*` alarms on its own; the script still does it explicitly.
- Capture windows 1 and 2 cannot be regenerated after teardown. Read the file before step 14.
- The console lags the API by hours. Verify with the CLI only.
- IAM changes take ~30 s to propagate; an immediate retry shows the old error.
- First `terraform apply` after a destroy can fail on `aws_s3_object` refresh. Re-run.
- Keep every AWS-facing description ASCII (EC2 rejects em dashes, IAM accepts them).
- `buildspec.yml`: a colon inside an `echo` string, or the old multi-line bandit block,
  makes CodeBuild reject the whole file before running anything.
- While the CI stack exists, every push to `main` is a paid run and registers a package.
  `start-pipeline-execution` gives a second run without a push.
- The XGBoost "Saving model in the UBJSON format" warning is benign.
- Wait ~15 min after the last Glue job before `teardown-lab2.sh`, and use the new script.
- Git Bash on this machine: `python3` has no boto3 (use `.venv/Scripts/python`); native
  Python does not see `/tmp`; `MSYS_NO_PATHCONV=1` for `aws logs` calls with `/aws/...` names.
- `wc -w` on the plan counts code blocks. Point at scripts instead of inlining commands.
- Never approve v1 or v3: their artifacts no longer exist.
