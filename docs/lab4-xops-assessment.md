# Lab 4: XOps Assessment, Pipeline Mapping and MLOps Configuration

NorthStar Retail churn model, CS 401R, repository `cs401r-bsharp02`. The five
section headings below are the ones the Lab 4 rubric names; the TA greps for them.

Evidence files referenced here (all under `docs/`): `lab4-pipeline-runs.txt`,
`lab4-build-green.log`, `lab4-build-failed.log`, `lab4-failure-demo.txt`,
`lab4-model-registry.json`, `lab4-model-package.json`, `lab4-mlflow-runs.txt`,
`lab4-alarm-email.png`.

---

## Pipeline Stage Mapping

**Implementation chosen: AWS CodePipeline + CodeBuild** (`pipeline.yaml`,
`buildspec.yml`), not GitHub Actions. Rationale: the training job, the Model
Registry and the data bucket are all in the AWS account, so the runner needs an
IAM role rather than long-lived keys in a GitHub secret; CodeBuild assumes
`northstar-codebuild-role` and the SageMaker Pipeline runs as the Lab 1
`northstar-dev-MLEngineer` role, both least-privilege and both defined as code.
CodePipeline also gives us the ManualApproval action and the SNS topic for free.

The five required phases map onto **three** CodePipeline stages. One CodeBuild
project performs four of the phases because a CodePipeline stage is a
deployment boundary (an artifact hand-off, a place to insert a human), while a
CI phase is a logical step. One stage per phase would mean five CodeBuild
projects passing the model artifact between them and buy nothing.

| # | Required phase | CodePipeline stage | Where it runs | Enforced by |
|---|---|---|---|---|
| 1 | **Source**: triggered by push to `main` | `Source` | CodeStar/CodeConnections action on `bostonjsharp/cs401r-bsharp02`, branch `main`, `DetectChanges: true` | Nothing runs without a push; every execution records the commit SHA (`docs/lab4-pipeline-runs.txt`) |
| 2 | **Test**: `pytest tests/`; fail and alert on any failure | `Build` | `buildspec.yml` `pre_build`: stages `raw/`, `processed/`, `features/` from the data bucket, runs `tests/test_data.py` then `tests/test_features.py` | A failed assertion ends the build at `PRE_BUILD`; no `BUILD` phase runs, nothing is trained or registered. CodeBuild emits `FailedBuilds`, the alarm `northstar-ci-build-failure` goes to ALARM and publishes to `northstar-model-approvals` (`docs/lab4-failure-demo.txt`, `docs/lab4-build-failed.log`) |
| 3 | **Build**: package training code, run a SageMaker Training Job with the new code | `Build` | `buildspec.yml` `build`: `python models/churn/pipeline_definition.py --upsert` packages `models/churn/` to S3 and defines `northstar-churn-pipeline`; `start-pipeline-execution` runs its `TrainChurnModel` step (spot `ml.m5.large`, XGBoost 1.7-1 container, `train_sagemaker.py` calling our `churn_model.train()`) | The build polls the execution; `Failed`/`Stopped` exits 1. A model that misses a gate inside `train()` raises `ValueError`, so the training step fails and the `RegisterChurnModel` step never runs |
| 4 | **Evaluate**: promotion gate on the new model's metrics | `Build` | `buildspec.yml` `build`, after the SageMaker Pipeline succeeds: downloads `artifacts/evaluations/<sha>/evaluation_metrics.json`, `model_metadata.json` and the real `model.tar.gz`, then runs `tests/test_model.py` + `tests/test_fairness.py` with `-rw` | Gates: `precision_top10 >= 0.50`, `recall_top10 >= 0.25`, lift 95% CI over the recency baseline excludes 0, candidate AUC >= champion AUC - 0.02 (`TestRegressionAgainstChampion`), plus artifact/contract tests. Fairness flags, does not block |
| 5 | **Register**: `PendingManualApproval` in the Model Registry if all gates pass | `Build` (registration) + `ManualApproval` (promotion) | The SageMaker Pipeline's `RegisterChurnModel` step registers into `northstar-churn-models` with `ModelApprovalStatus=PendingManualApproval`; the `ManualApproval` stage notifies `northstar-model-approvals` and waits for a human | See "where registration sits" below; `docs/lab4-model-registry.json` shows one new `PendingManualApproval` version per green run and none per red run |

**Where registration sits relative to the gate.** Registration happens
inside the SageMaker Pipeline, *before* CodeBuild runs `tests/test_model.py`.
Two mechanisms keep a red build from leaving an approvable package behind:

1. Gates that are computable inside training (precision@10, recall@10, lift
   CI) are checked in `churn_model.evaluate_model()`, which **raises** on a
   miss. The training step fails first, so the register step never executes.
2. Gates that need the registry or the packaged artifact (champion regression,
   model loading, monotonicity, metadata) run in CodeBuild after registration.
   If any fails, `buildspec.yml` `post_build` reads the package ARN the build
   recorded and sets it to **Rejected** with the build id in the description.
   `post_build` runs after a `BUILD` failure but not after a `PRE_BUILD`
   failure; in the latter case nothing was registered, which is exactly why
   the deliberate failure in the demo lives in `tests/test_data.py`.

Either way, the only packages that can be approved by a human are ones from
builds that went green end to end.

**Notification.** The alarm `northstar-ci-build-failure` watches
`AWS/CodeBuild FailedBuilds` (emitted by CodeBuild itself, so no phase failure
can skip it) with `Period 300, Threshold 1, TreatMissingData notBreaching`,
and publishes to `northstar-model-approvals`, whose topic policy allows
`cloudwatch.amazonaws.com`. A confirmed email subscription proves delivery
(`docs/lab4-alarm-email.png`).

**Edits we made to the kit's `pipeline.yaml`** (both marked `Lab 4 errata` in
the file):

- `sagemaker:ListModelPackages` granted with `Resource: "*"` on the CodeBuild
  role. The kit scoped it to `model-package*/*`, but `ListModelPackages` has
  no resource type in the SageMaker authorization reference, so the scoped
  grant never matches and the champion regression test, which lists the group
  to find the newest Approved package, would get `AccessDenied`.
- `ComputeType: BUILD_GENERAL1_SMALL` instead of `MEDIUM`: half the per-minute
  price and the only size CodeBuild's free tier covers; a run spends most of
  its wall clock polling the training job, not computing.

And one edit to our own Terraform (`infrastructure/modules/iam/main.tf`):
`northstar-dev-MLEngineer` may pass itself to SageMaker (`iam:PassRole`,
the handout's prerequisite 4), may `CreateModelPackage` /
`DescribeModelPackageGroup` on `northstar-churn-models` (the register step), and
may `AddTags`/`ListTags` on training jobs, model packages and pipelines (a
SageMaker Pipeline tags every job it creates). Two more were found by running:
`sagemaker:CreateModelPackageGroup`, which the SDK's RegisterModel step calls
even when the group already exists (green run 1 trained successfully and failed
only there, `docs/lab4-pipeline-runs.txt` build `3e2929b1`), and the training
job's documented CloudWatch Logs / `PutMetricData` / ECR layer-check actions.
The kit assumes `AmazonSageMakerFullAccess`; our Lab 1 policy is
least-privilege, so these were missing.

---

## Champion-Challenger

**Definitions.** The *champion* is the newest package in `northstar-churn-models`
with `ModelApprovalStatus == Approved`. A *challenger* is the package a green
pipeline run just registered as `PendingManualApproval`. Both carry an
`evaluation_metrics.json` (cited by `ModelMetrics.ModelQuality.Statistics.S3Uri`
and copied into `CustomerMetadataProperties` by the lineage step), computed on
a 30% stratified validation split with `random_state=42`. When the feature set
has not changed the two splits are identical, so the comparison is paired.

**Promotion rule (numeric, binary).** The challenger replaces the champion
**if and only if all four hold**:

| Condition | Threshold | Why this number |
|---|---|---|
| C1 ranking gain | `challenger.auc_roc - champion.auc_roc >= 0.010` | Lab 3 measured the four tracked configurations within 0.0048 AUC of each other on the same split (0.7787 to 0.7835, `docs/lab4-mlflow-runs.txt`); a 0.01 margin is above that spread, so a promotion reflects a real change, not seed noise |
| C2 beats the business baseline | `challenger.lift_ci_low > 0` | The bootstrap 95% CI on AUC lift over the recency-only model must exclude zero (the Lab 3 gate; a point-estimate threshold was retired because 0.03 is below the lift's run-to-run standard deviation) |
| C3 operating-point floor | `challenger.precision_top10 >= 0.50` and `challenger.recall_top10 >= max(0.25, champion.recall_top10 - 0.02)` | A higher AUC that catches fewer of the churners in the 10% we can actually contact is not an improvement for the retention team |
| C4 no tier left behind | `challenger.tier_<t>_recall >= champion.tier_<t>_recall - 0.05` for every tier with `n >= 100` and `positives >= 10` | `tests/test_fairness.py` flags a 0.34 recall gap between Bronze and Gold today; a challenger may not widen any tier's gap by more than 5 pp even if the aggregate improves |

Otherwise the challenger is set to **Rejected** and the champion stays.

**Relationship to the CI regression gate.** The CI gate in
`tests/test_model.py` allows a candidate down to `champion.auc_roc - 0.02`
before failing the build. The band between -0.02 and +0.01 is "registered,
reviewable, not promoted": a routine weekly retrain that lands there is a
valid rollback candidate and keeps its artifact, but it does not become the
champion. Below -0.02 the build is red, the package is Rejected by
`post_build`, and the alarm fires.

**Who applies it.** Today a human applies the rule at the `ManualApproval`
stage with `aws sagemaker update-model-package --model-approval-status
Approved|Rejected`. The rule is written so that a Lambda behind the Lab 5
`Deploy` stage can apply it mechanically from the two metrics files.

---

## Retraining Triggers

Both triggers start the same SageMaker Pipeline, `northstar-churn-pipeline`,
whose definition is upserted by every CI run (`models/churn/pipeline_definition.py`).
They produce a *challenger*; the Champion-Challenger rule above decides
promotion. Both are automatable with no human in the loop; the human is only at
the approval stage. Designed, not deployed.

One constraint shapes both: the pipeline's pre_build freshness test fails a
feature set older than 48 h, and the training step reads `features/customers/`
directly. So each trigger must refresh the features first, by starting the Lab 2
Glue workflow `northstar-dev-pipeline` (`infrastructure/modules/glue/main.tf`:
crawler, `northstar-dev-transform`, `northstar-dev-feature-engineer`, chained by
`CONDITIONAL` triggers), and start training only when the feature job succeeds.

### (a) Scheduled trigger: weekly

| Item | Value |
|---|---|
| Fires | **Amazon EventBridge Scheduler** schedule `northstar-churn-weekly-retrain` |
| Schedule expression | `cron(0 5 ? * MON *)` in `America/Denver` (Monday 05:00, after the weekend's transactions land in `raw/`) |
| Target 1 | `glue:StartWorkflowRun` on `northstar-dev-pipeline` (refreshes `processed/` and `features/`, writes a new `event_time`) |
| Hand-off | **EventBridge rule** `northstar-feature-job-succeeded`: event pattern `source: aws.glue, detail-type: "Glue Job State Change", detail.jobName: northstar-dev-feature-engineer, detail.state: SUCCEEDED` |
| Target 2 | `sagemaker:StartPipelineExecution` on `northstar-churn-pipeline` with `PipelineParameters: CommitSha=scheduled-<date>`, via a scheduler role allowed `sagemaker:StartPipelineExecution` and `iam:PassRole` on `northstar-dev-MLEngineer` |
| Why weekly | The label window is a quarter and the feature windows are 30/90/180 days; a weekly cadence moves the cutoff by ~1/13 of the shortest window, enough to track seasonality without retraining on near-identical data |

### (b) Performance-based trigger: production AUC decay

| Item | Value |
|---|---|
| Signal | Daily production AUC: the Lab 6 ground-truth job joins endpoint predictions captured by **SageMaker Model Monitor** data capture with the churn outcomes that mature 90 days later, and publishes `NorthStar/Model` metric `ProductionAUC` (dimension `Model=northstar-churn`) with `cloudwatch:PutMetricData` |
| Fires | **Amazon CloudWatch alarm** `northstar-churn-production-auc-low`: `ProductionAUC < 0.70`, `Period 86400`, `EvaluationPeriods 3`, `DatapointsToAlarm 2` (2 of 3 daily points below threshold; one bad day does not retrain), `TreatMissingData: missing` |
| Threshold rationale | The champion's validation AUC is 0.7835 and the recency-only baseline is 0.7225 (`docs/lab3-evidence/evaluation_metrics.json`). Below 0.70 the model is worse than the rule the business has for free, so continuing to serve it has negative value |
| Hand-off | **EventBridge rule** `northstar-churn-auc-alarm`: `source: aws.cloudwatch, detail-type: "CloudWatch Alarm State Change", detail.alarmName: northstar-churn-production-auc-low, detail.state.value: ALARM` |
| Target | `glue:StartWorkflowRun` on `northstar-dev-pipeline`, then the same `northstar-feature-job-succeeded` rule as (a) starts `northstar-churn-pipeline` |
| Second signal (same wiring) | Feature drift before outcomes mature: Model Monitor's hourly `DataQuality` schedule against the Lab 6 baseline; a `constraint_violations` report with `completeness`/`baseline_drift` on `days_since_last_purchase` or `total_spend_90d` with distance > 0.2 (PSI) sets alarm `northstar-churn-feature-drift`, which fires the same rule |

A retrain started by (b) runs through the same CI gates; if the data moved so
much that the new model cannot beat the recency baseline (C2), the build goes
red and the alarm `northstar-ci-build-failure` tells a human that automation
has run out of road.

---

## DataOps Maturity

Levels used (course chapter): 0 ad hoc; 1 scripted and repeatable; 2 automated
pipeline with tests and contracts; 3 continuous, monitored, quality-gated on
every change; 4 self-healing and governed at scale.

**Current level: 2**

**Evidence:** The pipeline is code: `infrastructure/modules/glue/main.tf`
defines the crawler, `northstar-dev-transform`, `northstar-dev-feature-engineer`
and the workflow `northstar-dev-pipeline` that chains them with `CONDITIONAL`
triggers, run end to end by `scripts/run-lab2-pipeline.sh` (157,627 processed
rows, every run since Lab 2). The data has a contract, `docs/lab2-data-contract.md`
(schema, grain, nine quality guarantees, SLA), and lineage with owners on every
edge, `docs/lab2-data-lineage.png`. The contract is enforced on every push:
`tests/test_data.py` holds 32 tests across schema, grain, leakage, distribution
and freshness, run in CodeBuild `pre_build` before anything trains;
`docs/lab4-build-green.log` line 356 shows `32 passed`, the leakage proof at
line 346 and the 48-hour freshness check at line 350, and
`docs/lab4-build-failed.log` lines 354-369 show one failing assertion ending
the build at `PRE_BUILD`. The feature logic itself is unit-testable:
`data/feature_engineering.py` is the pandas twin of the Glue job, pinned to it
by a parity test over all 9,999 customers.

**Gap to Level 3:** no continuous data-quality monitoring between pushes.
Quality is checked only when code changes or a human runs the workflow;
nothing watches `raw/` for schema or volume changes, no Glue Data Quality
ruleset runs inside the workflow, and a feature set that silently drifts (for
example `online_to_store_ratio` collapsing after a store closure) passes the
freshness test, which measures age, not content. The SLA in
`docs/lab2-data-contract.md` is asserted by nothing.

**Top priority investment:** an **AWS Glue Data Quality** ruleset (DQDL)
attached to `northstar-dev-transform`, generated from the contract
(`RowCount > 140000`, `IsComplete "customer_id"`, `IsUnique "transaction_id"`,
`ColumnValues "channel" in ["store","online","unknown"]`), failing the workflow
on a rule miss and publishing `glue.data.quality` metrics to a CloudWatch alarm.
That makes the contract a gate on every data refresh, not only on code pushes.

---

## MLOps Maturity

Levels used: 0 manual notebook training; 1 scripted, tracked, reproducible
training; 2 CI/CD-automated training with gated registration; 3 automated
retraining and production monitoring; 4 governed and self-optimising.

**Current level: 2**

**Evidence:** Training is reproducible code: `models/churn/churn_model.py`
fixes the hyperparameters in one `PARAMS` dict and the split seed; the local run
reproduces Lab 3's AUC 0.7835 exactly (`docs/lab3-evidence/evaluation_metrics.json`)
and the CI run on the container's XGBoost 1.7.4 lands at 0.7848
(`docs/lab4-build-green.log` line 549). Experiments are tracked: four runs with
differing `max_depth`, `eta` and `feature_set` in the MLflow App
(`docs/lab4-mlflow-runs.txt`). Every push trains on spot in a SageMaker
Pipeline (`models/churn/pipeline_definition.py`, driven by `buildspec.yml` and
`pipeline.yaml`), registers `PendingManualApproval`, and must pass the gates in
`tests/test_model.py` against the real artifact: `docs/lab4-build-green.log`
line 744 names the champion it beat (`champion v2 auc=0.7848; candidate
auc=0.7848`) and line 748 carries the fairness flag from `tests/test_fairness.py`.
A red build registers nothing and notifies: `docs/lab4-failure-demo.txt` shows
the same newest version before and after the failed run, the alarm going
OK -> ALARM, and a confirmed subscriber (`docs/lab4-alarm-email.png`). The
registry carries lineage: `docs/lab4-model-package.json` has the data URI and
timestamp, the full commit SHA and the metrics on the final package.

**Gap to Level 3:** nothing is in production, so there is no production
monitoring and no automatic retraining: no endpoint (Lab 5), no Model Monitor
data capture or drift baseline (Lab 6), the `ProductionAUC` metric that trigger
(b) alarms on is published by nothing, and the weekly EventBridge schedule in
trigger (a) is designed, not deployed. Promotion is a human running
`update-model-package`; the Champion-Challenger rule is applied by eye.

**Top priority investment:** deploy the champion to a SageMaker real-time
endpoint with **Model Monitor data capture** and a `DataQuality` baseline built
from `features/customers/` (Labs 5-6), and put a Lambda behind the `Deploy`
stage of `pipeline.yaml` that applies the Champion-Challenger rule from the two
`evaluation_metrics.json` files. That single change makes both retraining
triggers live and removes the last manual step between a green build and a
served model.
