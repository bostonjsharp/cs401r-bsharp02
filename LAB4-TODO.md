# Lab 4 To-Do: XOps + CI/CD Pipeline + Testing

Due **Sat Oct 31, midnight**. Tag `lab4-submit`, push the tag, paste the repo URL in Canvas.
Points: Task 1 Test suite (30) + Task 2 CI/CD pipeline (30) + Task 3 MLOps configuration (20)
+ Task 4 XOps maturity assessment (20) = 100. The TA cannot see our AWS account: Task 2 is
graded **only** from evidence files committed under `docs/` (exact names, listed in the
rubric table at the bottom). If a file is not committed, the task was not done.

Handout: `C:\Users\bosto\Downloads\Lab_4--XOps & CICD.html` (includes the "Clarified Fri Oct 23"
evidence section).
Starter kit: `C:\Users\bosto\Downloads\Lab4-Starter-Kit.zip` (the corrected kit; 11 files).
Nothing from the kit has been copied into the repo yet (Phase 0 says where it goes).

### Errata received 2026-10-26 (read first)

The instructor ran the lab end to end and corrected the kit. The zip above is the corrected
one; the changes it already contains:

1. New `requirements.txt` (CodeBuild's install phase needs it) and `pytest.ini`
   (`pythonpath = .` so plain `pytest tests/` can import `data/feature_engineering.py`).
2. `tests/test_features.py`: `avg_basket_size_6m` is checked as **items per order** (5.0),
   matching our Lab 2 definition. The old assertion expected dollars.
3. `tests/test_model.py`: the recency monotonicity test allows ties and dips up to 0.02.
4. `tests/test_data.py`: the hint for the leakage TODO was corrected (test the features
   against a recomputation from `processed/` rows <= T; processed itself contains holdout rows).
5. `buildspec.yml` / `pipeline.yaml`: `tests/test_fairness.py` runs in the build phase next to
   `test_model.py`; a package registered by a build that later fails is set to **Rejected**
   in post_build; `GitHubRepo` must be our repo, `cs401r-bsharp02`.
6. **One change to our own Terraform**: MLEngineer must be allowed to pass itself to
   SageMaker (`iam:PassRole`), statement under prerequisite 4. See errata item E1 below:
   for *our* least-privilege policy that statement alone is not enough.
7. Task 2 is graded from `docs/` evidence files: pipeline runs, the deliberate-failure
   demonstration with alarm history. Point values unchanged.
8. Re-run the Lab 2 feature job (~90 s) at the start of any session in which you push: a
   feature set older than 48 hours fails the build by design. Every push to `main` runs a
   training job, so **delete the CI stack before the final docs-only push**.

The handout's Oct 23 clarification added four evidence files after the lab was assigned:
`docs/lab4-build-green.log`, `docs/lab4-build-failed.log`, `docs/lab4-model-registry.json`,
`docs/lab4-alarm-email.png`, and a BEFORE snapshot in the failure demo.

---

## What we are building, in one paragraph

A push to `main` is picked up by **CodePipeline** (Source stage, CodeStar/CodeConnections
link to GitHub). Its Build stage runs one **CodeBuild** project with the kit's
`buildspec.yml`: `pre_build` copies raw/processed/features out of the Lab 2 data bucket and
runs `tests/test_data.py` + `tests/test_features.py` (the **Test** phase); `build` upserts and
starts the **SageMaker Pipeline** `northstar-churn-pipeline` (`models/churn/pipeline_definition.py`:
one spot `ml.m5.large` training step running `train_sagemaker.py`, which calls *our*
`churn_model.train(df)`, then a RegisterModel step that lands the model in
`northstar-churn-models` as `PendingManualApproval`), waits for it, downloads the artifact
and `evaluation_metrics.json`, and runs `tests/test_model.py` + `tests/test_fairness.py` as
the **Evaluate** gate (precision@10 >= 0.50, recall@10 >= 0.25, lift CI excludes 0, AUC >=
champion - 0.02, fairness flag). A third stage, **ManualApproval**, is the human promotion
decision (SNS topic `northstar-model-approvals`). A CloudWatch alarm
`northstar-ci-build-failure` on CodeBuild's `FailedBuilds` metric notifies the same topic.
We prove it all with one green run (approved as champion), one deliberate pre_build failure
(halts, no new version, alarm fires, email arrives), and the revert (final green run whose
regression test compares against the champion). Everything is captured into `docs/` before
teardown. Task 3 documents champion/challenger, retraining triggers, lineage metadata on the
final package, and MLflow runs from the Lab 3 App. Task 4 is a ~400-word maturity assessment.

---

## Possible errata (found 2026-10-26, before any implementation)

Found by reading the handout against the kit and against our repo. E1 and E2 are
"very likely" but only confirmed by `aws iam simulate-principal-policy` / the first run;
the rest are confirmed by reading. Worth sending E1-E6 to the instructor once E1/E2 are
confirmed.

| # | What | Why it matters | What we do |
|---|---|---|---|
| **E1** | The handout's prerequisite 4 adds only `iam:PassRole` to MLEngineer. The SageMaker Pipeline runs as MLEngineer and its RegisterModel step calls `sagemaker:CreateModelPackage`; our policy (`infrastructure/modules/iam/main.tf`) grants the non-existent `sagemaker:RegisterModel` plus Describe/List, never Create. The handout also says "confirm the policy has AddTags/ListTags: pipelines tag every job they create"; ours has them **scoped to Studio ARNs only** (domain/user-profile/space/app), not `training-job/*`, `model-package/*`, `pipeline/*`. | Reference accounts have AmazonSageMakerFullAccess masking this. Ours will fail the training step (AddTags on training-job) or the register step (CreateModelPackage) with AccessDenied. | Phase 0: extend the policy in Terraform in one edit (PassRole-to-self, CreateModelPackage + DescribeModelPackageGroup on model-package ARNs, AddTags/ListTags widened). Phase 4 step 3: `simulate-principal-policy` before the first run. |
| **E2** | `pipeline.yaml` grants the CodeBuild role `sagemaker:ListModelPackages` on `arn:...:model-package*/*`. ListModelPackages has no resource type in the SageMaker authorization reference, so it needs `Resource: "*"`. | The regression test the handout requires runs inside CodeBuild and must list the group to find the newest Approved package. Likely AccessDenied on the first green run. | Phase 4 step 5: simulate for the CodeBuild role; if denied, add a `Resource: "*"` statement to `pipeline.yaml` (ours to edit) and redeploy the stack. |
| **E3** | "Add a deliberately failing assertion to tests/" is underspecified. The rubric requires PRE_BUILD FAILED, no BUILD phase, registry unchanged. Only `test_data.py` and `test_features.py` run in pre_build. | A failure in `test_model.py`/`test_fairness.py` runs *after* the SageMaker Pipeline registered a package; post_build sets it Rejected, so the registry **does** gain a version and the rubric's "same newest version BEFORE and AFTER FAILED RUN" fails. | Phase 4 step 8: the failing assertion goes in `tests/test_data.py`. |
| **E4** | Kit is internally inconsistent on `avg_basket_size_6m`: the errata fixed `test_features.py` to items per order (ours ranges 0-9), but `test_model.py`'s synthetic batch uses 20-60 / 120-400 and the monotonicity fixture 85.0, dollar-like values. | Out-of-distribution inputs for a tree model. Trees bin "very large" as "large basket" (retained), so it should still pass. | Phase 2 local run proves it. If `test_auc_roc_on_synthetic_data`, `test_churners_score_higher_than_retainers` or the spread test fails, this is the first suspect; report, do not weaken the test. |
| **E5** | Handout cost: "a pipeline run costs about $0.02". CodeBuild BUILD_GENERAL1_MEDIUM bills $0.01/min and a run sits polling the training job for 10-15 min. CodePipeline V1 is $1/month per active pipeline after the free one. | Budgeting, not correctness: ~$0.10-0.15 per push. | COST MAP below. Optional: `ComputeType: BUILD_GENERAL1_SMALL` in `pipeline.yaml` (half price, 100 free min/month). |
| **E6** | Timeout mismatch: estimator `max_wait=3600` (spot may wait up to 60 min), buildspec polls 45 min (`MAX_WAIT=2700`), CodeBuild times out at 90. | If spot capacity is slow the build fails on the poll timeout, post_build cannot reject (the ARN file is never written), and the SageMaker pipeline may still register a package later: a stray PendingManualApproval version. | Phase 4 step 6: on "Pipeline timed out", `aws sagemaker stop-pipeline-execution` immediately, then reject any package it registered. |
| E7 | MLflow snippet uses `experiment_names=["northstar-churn-lab4"]` as "your experiment name". Ours is `northstar-churn` (Lab 3, 4 runs with differing params). | None; substitute. The Lab 3 runs already satisfy ">= 3 runs with differing params". | Phase 3. |
| E8 | Windows/Git Bash: the handout's `save_log` and the teardown line `aws logs ... --log-group-name /aws/codebuild/northstar-ci` get MSYS path-mangled. | Known Lab 2 gotcha; `python3` in `save_log` only needs stdlib so Git Bash's 3.13 is fine. | Prefix those commands with `MSYS_NO_PATHCONV=1`. |
| E9 | `update-model-package --model-approval-status Approved` is the registry; CodePipeline's ManualApproval *action* is separate and stays "waiting" until a newer execution supersedes it. | Not graded; just do not chase a phantom. | Note only. |
| E10 | `northstar-churn-models` already holds **v1** from Lab 3 (PendingManualApproval; its artifact bucket was destroyed). | The CI's first green run registers v2. The champion must be a CI-registered version. | Never approve v1. |
| E11 | Prerequisite 3's local test downloads `features/customers/` from the data bucket (stack must be up). `.dryrun/features/*.parquet` has the same 9,999 customers and all 16 columns (incl. `event_time`, `loyalty_tier`); `.dryrun/processed/*.parquet` + `northstar-raw-sample.csv` cover the data tests. | The whole of Task 1 and `churn_model.py` can be built and verified **for free**. The freshness test fails on the stale dry run (event_time 2026-10-11) unless the Docker dry run is re-run (free). | Phases 1-2 run locally; Phase 4 only confirms in CI. |

Things that **check out** (so nobody re-verifies them): our Glue feature job writes
`event_time` = wall clock and overwrites `features/customers/`, so the 48-h freshness rule
and `test_one_row_per_customer` both hold after re-runs; the kit's `FEATURE_COLUMNS` order
equals the skeleton's; Lab 3's metrics already carry `precision_top10`, `recall_top10`,
`lift_ci_low/high`, `baseline_auc`, `positive_rate_val`, `tier_<tier>_n/auc/recall`; the kit's
data tests pass on dry-run ranges (max recency 365 = window length, order_value max 620,
channels online/store, survival 96.5%, diversity 0.125-1.0, churn 22%); the PassRole statement
creates no Terraform cycle (policy is a separate `aws_iam_policy`, attached by
`aws_iam_role_policy_attachment`); `terraform output ml_engineer_role_arn` exists; MLflow App
`app-ARLX53NAWKLU` survived the Lab 3 teardown; the raw sample lands at
`raw/customers/northstar-raw-sample.csv` as the buildspec expects; the Glue temp dir is
`processed/_glue_temp/`, which the buildspec excludes.

---

## COST MAP (read before any AWS step)

Budget alarm for the course: **$10/month**. Lab 3's full session cost about $2.

| Thing | Cost | Needed? | Rule |
|---|---|---|---|
| Lab 2 stack re-apply: NAT Gateway + public IPv4 | ~$0.05/hr while up | YES: the pipeline reads `features/customers/` from the data bucket and the Glue feature job needs the NAT | Up only during AWS sessions. Re-apply + pipeline costs ~$0.25 and 10 min, so tear down if the gap to the next session is more than ~5 hours; otherwise leave it up overnight (~$1.20/day) |
| Lab 2 full pipeline (crawler, transform, feature-engineer) | ~$0.25 per run | YES, once per apply | Run once via `bash scripts/run-lab2-pipeline.sh` |
| Feature job alone, `aws glue start-job-run --job-name northstar-dev-feature-engineer` | ~$0.03-0.10 per run (Glue bills per DPU-minute, 1-min minimum) | YES at the start of every session in which we push (48-h freshness) | Also re-ingests 9,999 rows into the Feature Group (append-only offline store; harmless, both loaders dedup) |
| **CodeBuild** `northstar-ci`, BUILD_GENERAL1_MEDIUM | $0.01/min, ~10-15 min per run = **~$0.10-0.15 per push** | YES, every push to main | Minimum 3 runs (green, failure, revert); budget 6. Optional: SMALL compute in `pipeline.yaml` halves it |
| SageMaker **spot** training `ml.m5.large` | < $0.01 per run (spot ~$0.035/hr, bills ~2-3 min) | YES, every push | Quota L-29688C85 applied **10** (on-demand L-611FA074 applied 15) per `docs/training-quota-verification.txt`; re-check before the first run. Keep `use_spot_instances=True` |
| CodePipeline `northstar-pipeline` (V1) | $1/month per active pipeline; first active pipeline free under Free Tier | YES | Deleted with the stack before the final push |
| CodeConnections (GitHub app), SNS email, CloudWatch alarm (first 10 free), S3 artifacts bucket, CloudWatch Logs | ~$0 | YES | Delete the log group and empty the versioned bucket at teardown |
| **MLflow App** `northstar-mlflow` | free | YES (Task 3 evidence) | Already exists; never create a Tracking Server ($0.60/hr) |
| Model Registry, SageMaker Pipeline definition, auto-created SageMaker Experiments | free | YES | Survive teardown; harmless |
| SageMaker Studio apps / kernels | ~$0.05/hr | NOT needed | Do not open Studio |
| SageMaker endpoint | $0.05-0.10/hr | NOT needed (Lab 5) | `teardown-lab3.sh` style check at the end |

Working estimate for the whole lab: **$2-5**, dominated by NAT hours between steps.
Keep Phase 4 to one or two sittings.

---

## Where things stand (2026-10-26)

- AWS account `829485866627`: Lab 2 and Lab 3 stacks torn down. Still present (all free):
  `northstar-tfstate-829485866627`, the MLflow App `arn:...:mlflow-app/app-ARLX53NAWKLU`
  (experiment `northstar-churn`, 4 runs), model package group `northstar-churn-models`
  with v1 PendingManualApproval. Phase 4 starts with a full `terraform apply` of the Lab 2
  stack, which recreates the data bucket under the same name.
- Toolchain: AWS CLI 2.36.40 (has `codeconnections`), Terraform, Docker (Glue image for
  the dry run), `.venv` on Python 3.14.2 with xgboost 3.4.1, pandas, scikit-learn, boto3,
  mlflow 3.16 + sagemaker-mlflow. **No `sagemaker` SDK** in the venv. The kit pins
  `sagemaker>=2.200,<3` for `pipeline_definition.py`, which only CodeBuild (Python 3.11)
  runs; do not install it into the 3.14 venv (wheel/compat risk, and we never need it
  locally). `pytest` is the only new local dependency.
- Local data for free CI simulation: `.dryrun/processed/*.parquet` (157,627 rows),
  `.dryrun/features/*.parquet` (9,999 rows, 16 columns), `northstar-raw-sample.csv`
  (163,255 rows) at the repo root.
- Git: remote `https://github.com/bostonjsharp/cs401r-bsharp02.git`, branch `main`, clean.
  `tests/`, `data/`, `models/` do not exist yet. No GitHub connection in AWS yet.
- Training container: SageMaker XGBoost **1.7-1** (Python 3.8, pandas 1.x, xgboost 1.7).
  Everything in `models/churn/churn_model.py` must run there.

---

### Phase 0 done (2026-10-28, nothing committed yet)

- Kit copied in (11 files, nothing overwritten). `.gitignore` ignores `data/processed/`,
  `data/features/`, `data/northstar-raw-sample.csv`, `reports/`. `pytest` 9.2.0 in the venv.
- E1 written in `infrastructure/modules/iam/main.tf`: `PassSelfToSageMaker` (handout verbatim),
  `ModelRegistryWrite` (CreateModelPackage + DescribeModelPackageGroup on model-package(-group)
  ARNs), `PipelineJobTags` (AddTags/ListTags on training-job, model-package(-group), pipeline).
  `terraform validate` OK; plan = 53 add / 0 change / 0 destroy. Not applied.
- `pipeline.yaml` decided and edited: E2 statement (`sagemaker:ListModelPackages`, `Resource "*"`)
  added to the CodeBuild role; E5 `ComputeType: BUILD_GENERAL1_SMALL` (free tier's 100
  build-min/month applies to SMALL only). `aws cloudformation validate-template` OK.
- `pytest --collect-only tests/`: 44 tests collected; `test_features.py` errors at import on
  the missing `data/feature_engineering.py`, which is Phase 1a.
- `buildspec.yml` contract the tests see: pre_build sets `NORTHSTAR_RAW=./data/northstar-raw-sample.csv`,
  `NORTHSTAR_PROCESSED=./data/processed`, `NORTHSTAR_FEATURES=./data/features` and runs
  `test_data.py` then `test_features.py` (`--junitxml=reports/*.xml`); build runs
  `test_model.py` + `test_fairness.py` (only if the file exists) with `-v -rw`,
  `--eval-metrics-path /tmp/eval_metrics.json --model-path /tmp/model/xgboost-model`;
  `conftest.py` maps `--eval-metrics-path` onto `EVAL_METRICS_PATH`. Metrics live at
  `artifacts/evaluations/<8-char sha>/evaluation_metrics.json` with `model_metadata.json` beside it.

### Phases 1-3 done (2026-10-30, committed; not pushed)

- `data/feature_engineering.py` (330 lines): pandas twin of the Glue job. Parity on the
  9,999-customer dry run is **exact** for all integer-valued features, tier, proxy score
  and label; `avg_order_value` differs by exactly 1e-4 on 103 customers (8-order means
  land on an exact half at the 5th decimal and Spark's partition summation order
  decides the tie). Rounding is Spark-style HALF_UP (`round_half_up`); the parity
  test allows `atol=1e-4` on the six rounded columns, exact elsewhere.
- Tests: `test_data.py` 32 (parity x3, label-from-holdout, freshness x2 added),
  `test_features.py` 25 (both TODOs filled, 8 named edge cases), `test_model.py` 19
  (champion regression added; `eval_metrics` + `candidate_metrics` are module-scoped
  fixtures to avoid pytest 9's class-scoped-method deprecation in the graded log),
  `test_fairness.py` 4 (new). Local: **80 passed, 1 failed** = `test_features_are_fresh`
  on the 18-day-old dry run (expected, E11).
- `models/churn/churn_model.py` (361 lines): Lab 3 functions copied, `PARAMS` = xgb-shallow.
  Local `train_sagemaker.py` run on `.dryrun/features` reproduces Lab 3 exactly
  (AUC 0.7835, P@10 0.7167, R@10 0.3258, CI [0.0425, 0.0797]); `model_dir` holds only
  `xgboost-model`. **Container check done** in `python:3.8-slim` + xgboost 1.7.6 /
  pandas 1.5.3 / sklearn 1.3.2: runs clean, AUC 0.7848, P@10 0.7300, R@10 0.3318,
  CI [0.0452, 0.0838] (all gates pass); the 1.7 artifact loads under xgboost 3.4.1 and
  passes all 23 model + fairness tests. `-rw` summary shows the fairness flag
  (Bronze 0.45 vs Gold 0.12) and "no Approved champion ... vacuous".
- E4 did not bite: the dollar-scaled synthetic batch passes all discrimination tests.
- `docs/lab4-xops-assessment.md` written: mapping, champion-challenger (C1-C4),
  both triggers, maturity drafts. Three `<!-- TODO Phase 4 -->` markers want line
  numbers from `docs/lab4-build-green.log` (Phase 4 step 12).
- README "Lab 4: CI/CD and testing" section added. `requirements.txt`: nothing to add.
- **`docs/lab4-mlflow-runs.txt` moved to Phase 4 step 3**: the App's execution role is
  `northstar-dev-MLEngineer`, destroyed with the Lab 2 stack, so `search_runs` returns
  `PERMISSION_DENIED` until `terraform apply` recreates it (same name = same ARN).
  Script is in the Phase 3 bullet; run it right after the apply.
- Windows note: Git Bash heredocs containing certain non-ASCII text failed to parse;
  edit scripts were run from files instead. Keep new test/doc text ASCII.

## Phase 0 - Setup (FREE)

Starter kit placement (mirror the kit's own layout; nothing existing is overwritten):

| Kit file | Repo location | Edit? |
|---|---|---|
| `buildspec.yml` | repo root | ours to document; edit only if E2/E5 need it |
| `pipeline.yaml` | repo root | ours; E2 statement and optional SMALL compute |
| `requirements.txt` | repo root (new; we have `requirements-lab3.txt`, keep both) | add anything our tests import |
| `pytest.ini` | repo root | keep as is |
| `tests/conftest.py` | `tests/` | keep as is |
| `tests/test_data.py`, `tests/test_features.py`, `tests/test_model.py` | `tests/` | fill TODOs, add tests |
| `models/churn/pipeline_definition.py`, `models/churn/train_sagemaker.py` | `models/churn/` | **do not edit** |
| `models/churn/churn_model.py` | `models/churn/` | we write `train()` |
| (ours) | `tests/test_fairness.py`, `data/feature_engineering.py`, `docs/lab4-*` | new |

- [x] Copy the kit in (above). `git status` should show only new files.
- [x] `.gitignore`: add `data/processed/`, `data/features/`, `data/northstar-raw-sample.csv`
      (the buildspec stages them there in CI; locally we point `NORTHSTAR_*` at `.dryrun/`),
      `reports/` (junit xml). **Do not** ignore `data/` wholesale: `data/feature_engineering.py`
      must be committed or every CI run fails at import. Keep the `*.tfvars` / `.env` lines.
- [x] `pip install pytest` in the venv. Nothing else is missing; verify with
      `python -c "import pytest, xgboost, pandas, sklearn, boto3, pyarrow"`.
- [x] Terraform (E1), written now, applied in Phase 4 step 1. In
      `infrastructure/modules/iam/main.tf`, MLEngineer policy:
      1. the handout's `PassSelfToSageMaker` statement (`iam:PassRole` on
         `aws_iam_role.ml_engineer.arn`, condition `iam:PassedToService = sagemaker.amazonaws.com`);
      2. `sagemaker:CreateModelPackage`, `sagemaker:DescribeModelPackageGroup` on
         `arn:aws:sagemaker:*:*:model-package/*` and `model-package-group/*` (RegisterModel step);
      3. a second `AddTags`/`ListTags` statement for `training-job/*`, `model-package/*`,
         `model-package-group/*`, `pipeline/*` (keep the Studio one as is);
      4. leave out anything simulate-principal-policy does not ask for.
      `terraform validate` and `terraform plan` against remote state are free.
      Done 2026-10-28: plan shows **53 to add, 0 to change** (the stack is torn down, so
      the edited policy is one of the adds; its JSON reads "known after apply" because it
      references the role ARN). The statements are verified by reading `main.tf` now and
      by `simulate-principal-policy` in Phase 4 step 3.
- [x] Decide `pipeline.yaml` edits (free; redeploying later costs nothing extra):
      - E2: add a statement `sagemaker:ListModelPackages` with `Resource: "*"` to the
        CodeBuild role. Decide now, confirm with simulate in Phase 4.
      - E5 (optional): `ComputeType: BUILD_GENERAL1_SMALL`. 3 GB RAM is enough for pip +
        pytest on 157k rows. Document the change in the stage-mapping section.
- [x] Read `buildspec.yml` end to end once (the handout says so, and it is where every
      environment variable and path the tests see is defined: `NORTHSTAR_RAW`,
      `NORTHSTAR_PROCESSED`, `NORTHSTAR_FEATURES`, `EVAL_METRICS_PATH`, `--model-path`).

## Phase 1 - Task 1: the test suite (FREE)

Everything here runs locally against `.dryrun/` with:
```bash
export NORTHSTAR_RAW=./northstar-raw-sample.csv
export NORTHSTAR_PROCESSED=./.dryrun/processed
export NORTHSTAR_FEATURES=./.dryrun/features
pytest tests/test_data.py tests/test_features.py -v
```
Never set `ALLOW_MISSING_DATA` or `ALLOW_MISSING_FEATURES_MODULE` anywhere CI can see them.

### 1a. `data/feature_engineering.py` (the refactor the kit calls "the assignment here")
Pure-pandas port of `glue-scripts/feature_engineer.py`. The kit imports exactly five names:
`compute_recency_features(txns, snapshot_date[, customer_ids])`,
`compute_frequency_features(txns, snapshot_date)`, `compute_monetary_features(txns, snapshot_date)`,
`compute_behavioral_features(txns)`, `build_churn_feature_set(customers=, transactions=, snapshot_date=)`.
- [x] Reproduce the Glue semantics **exactly** (the parity test in 1b compares against the
      Glue output on 9,999 customers): window membership is `age < N` where
      `age = (T - purchase_date).days` on **dates** (Spark `datediff`), not timestamps;
      `avg_basket_size_6m` = sum(num_items in 180d) / orders in 180d, 0.0 when no orders;
      `category_diversity_score` = distinct categories **excluding 'unknown'** / 8.0;
      `online_to_store_ratio` = online orders / all orders; `avg_order_value` and
      `total_lifetime_value` over all history; six columns rounded to 4 dp
      (`avg_order_value`, `total_lifetime_value`, `total_spend_90d`, `avg_basket_size_6m`,
      `category_diversity_score`, `online_to_store_ratio`); tier thresholds 500/2000/5000.
- [x] Parse `purchase_date` with `pd.to_datetime` (the kit fixture passes ISO strings;
      Glue output is a date type).
- [x] Boundary: the kit's sentinel TODO passes an empty transaction frame (3 columns only)
      plus a `customer_ids` frame and expects `days_since_last_purchase == 999`. Add an
      optional `customer_ids` argument and a sentinel constant.
- [x] Document in the module docstring that the Glue job stays PySpark and this module is
      the **testable twin**, kept honest by the parity test (not by import: Glue cannot
      import it without `--extra-py-files`, and the point is to gate on it).
- [x] No `data/__init__.py` needed (namespace package + `pythonpath = .`), but adding one
      is harmless; pick one and note it in the README.

### 1b. `tests/test_data.py` (rubric: schema, grain, leakage, distribution, freshness)
Kit already covers schema (raw/processed/features), grain, nulls, tiers, bounded features,
monotonic windows, trivial-separability leakage smoke test, observation window, consistency.
- [x] **Leakage/parity TODO**: from `processed_df` rows with `purchase_date <= 2026-04-01`,
      recompute recency, tenure and the 30/90/180-day frequencies with 1a's functions and
      assert equality with `features_df` for every customer (merge on `customer_id`; exact
      for the integer-valued ones, `atol=1e-4` for the rounded ones if included). This is
      the test that forces 1a to be exact. Runs on 157k rows in CI; keep it vectorised.
- [x] **Freshness TODO**: `features_df["event_time"]` is Fractional epoch seconds (float);
      assert `now - max(event_time) < 48 h` and that `event_time` is **not** the cutoff.
      Locally this fails on the stale dry run unless re-run (E11); that is expected, note it
      in the test's failure message.
- [x] Both must FAIL loudly on missing data (inherit the `_require` behaviour; no new skips).
- [x] Count: the rubric wants >= 2 PASSED per file; the kit's file has ~25. Fine.

### 1c. `tests/test_features.py` (rubric: >= 5 unit tests; normal, boundary, edge)
Kit already has 17 tests across recency/frequency/monetary/behavioural/full-set.
- [x] Fill the two TODOs: `test_customer_with_no_transactions_returns_sentinel` (boundary,
      0 purchases, currently a vacuous `pass`) and `test_no_null_features_for_known_customers`
      (assert `null_counts.sum() == 0`, document the null strategy in the docstring).
- [x] Add explicit edge cases the handout names, with readable names (the TA reads the
      names): single transaction (recency = tenure, frequency 1, basket = that order's
      items, diversity 1/8), a customer whose only purchase is older than 180 days (basket
      0.0, frequencies 0), an 'unknown' category excluded from diversity, boundary at
      exactly `age == 90` (excluded by `age < 90`).
- [x] Everything must pass without touching the Glue image.

### 1d. `tests/test_model.py` additions (rubric: regression vs champion, 8 pts)
Kit already covers loading, 11-feature count, metadata, probability range, NaN handling,
monotonicity, determinism, the stored-metrics gate (P@10, R@10, lift CI), positive rate.
- [x] **Regression test**: champion = newest package in `northstar-churn-models` with
      `ModelApprovalStatus == "Approved"` (boto3 `list_model_packages`, sort by creation
      time desc; E2 for permissions). Champion AUC from the package's
      `ModelMetrics.ModelQuality.Statistics.S3Uri` (`evaluation_metrics.json`, key `auc_roc`)
      via boto3 S3 (CodeBuild has S3 full access); fall back to `CustomerMetadataProperties`
      if present. Candidate AUC = `auc_roc` from `EVAL_METRICS_PATH`.
      `warnings.warn(f"champion v{version} auc={champ_auc:.4f}; candidate auc={new_auc:.4f}")`
      **before** `assert new_auc >= champ_auc - 0.02`. The buildspec runs with `-rw`, so the
      warning lands in the build log's warnings summary (graded evidence).
- [x] No Approved package yet (first green run only): warn
      `"no Approved champion in northstar-churn-models; regression gate vacuous"` and pass.
      The **final** green run must show this test PASSED with the champion line, never
      SKIPPED. Do not `pytest.skip` here.
- [x] Region/account: `boto3.client("sagemaker", region_name=os.environ.get("AWS_REGION", "us-east-1"))`.
- [x] Locally: runs against the real registry with our admin credentials (free API calls);
      with the stack down it sees v1 (not Approved) and takes the vacuous path. Fine.

### 1e. `tests/test_fairness.py` (new; rubric: flag, don't block; >= 2 tests)
Manual slice evaluation (no SageMaker Clarify: it would need a processing job, i.e. money).
Reads the same `EVAL_METRICS_PATH`; `train()` (Phase 2) puts per-tier results there as
`tier_<tier>_n`, `tier_<tier>_positives`, `tier_<tier>_auc`, `tier_<tier>_recall`.
- [x] Test 1: all four tiers present with `n > 0` and a numeric recall (a model whose
      metrics lost the slices is not reviewable).
- [x] Test 2: recall gap between highest and lowest tier; if > 10 pp,
      `warnings.warn("FAIRNESS FLAG: recall gap ...")` and **still pass**. Expect it to fire:
      Lab 3 measured Bronze 0.4515 vs Gold 0.1154. The flag must be visible in
      `docs/lab4-build-green.log`'s warnings summary.
- [x] Test 3 (cheap, useful): too-small tiers (n < 100 or positives < 10) are reported, not
      judged, mirroring `evaluate_slices`.
- [x] Missing metrics file = FAIL, not skip (same principle as test_data).

### 1f. Local run of the whole suite
- [x] `pytest tests/ -v -rw` with the env vars above: everything green except the freshness
      test on the stale dry run and the model tests (no model yet; they skip until Phase 2
      provides `--model-path`). Re-run the Docker dry run (free, ~5 min) if a fully green
      local log is wanted:
      `docker run --rm -v "$PWD:/work" -w /work amazon/aws-glue-libs:glue_libs_4.0.0_image_01 spark-submit glue-scripts/local_dry_run.py --csv northstar-raw-sample.csv --out /work/.dryrun`

## Phase 2 - Task 2 code: `models/churn/churn_model.py` (FREE)

Contract (checked by `train_sagemaker.py` in the first seconds of the job):
`FEATURE_COLUMNS` (kit order, identical to the skeleton's), `LABEL = "churn_label"`,
`train(df, seed=42) -> (xgboost.Booster, dict)` with `precision_top10`, `recall_top10`,
`lift_ci_low`, `lift_ci_high` in the dict.
- [x] Copy (not import) from `churn_training_skeleton.py`, in call order:
      `preprocess_features` (drop `customer_id`, `event_time`, `churn_risk_score`; NaN
      passthrough; label to int; tiers as a Series), the split
      `train_test_split(X, y, tiers, test_size=0.30, random_state=seed, stratify=y)`,
      `train_model` with the **xgb-shallow** configuration fixed as module constants
      (depth 3, eta 0.05, 400 rounds, early stopping 20, `scale_pos_weight` computed from
      the training split, `eval_metric ["logloss","auc"]` with auc last), `train_recency_baseline`,
      `bootstrap_lift_ci` (2,000 replicates, seeded), `evaluate_model` **keeping the raise**
      (a failed gate fails the training step, so nothing is registered), `evaluate_slices`
      flattened into `tier_<tier>_*` keys (plus the per-tier list under `slice_results` if
      wanted; JSON-serialisable only).
- [x] Container constraints: Python 3.8 syntax only (no `match`, no `X | Y` outside
      annotations, no `str.removeprefix`, no `functools.cache`), keep
      `from __future__ import annotations`; pandas 1.x API only; pass
      `df["loyalty_tier"]` as a Series (kit note: `.values` is an Arrow array under pandas 3);
      no `mlflow`, `sagemaker`, `argparse`, `boto3` imports at module top; plain-ASCII
      prints only (no box-drawing characters in a container whose locale is unknown).
- [x] Train with a **named** DMatrix (`feature_names=FEATURE_COLUMNS`) so `xgboost-model`
      carries feature names; the tests build `DMatrix(..., feature_names=FEATURE_COLUMNS)`
      and XGBoost validates them.
- [x] Hyperparameters in one `PARAMS` dict at the top of the file, not literals inside
      `train()`, so the stage-mapping doc can quote it and the metadata's `hyperparameters`
      (read back from the booster by `train_sagemaker.py`) matches.
- [x] Local end-to-end (the handout's prerequisite-3 test, but on the dry run):
      ```bash
      python models/churn/train_sagemaker.py --train-channel .dryrun/features \
        --model-dir /tmp/model --output-data-dir /tmp/out
      cp /tmp/out/model_metadata.json /tmp/model/
      pytest tests/test_model.py tests/test_fairness.py -v -rw \
        --model-path /tmp/model/xgboost-model --eval-metrics-path /tmp/out/evaluation_metrics.json
      ```
      Expect AUC ~0.7835, P@10 ~0.72, R@10 ~0.33, CI [~0.04, ~0.08] on xgboost 3.4.1 (the
      container's 1.7 will differ slightly; both pass every gate per the handout). Expect the
      fairness warning and the "no Approved champion" warning in the `-rw` summary.
- [x] Confirm `/tmp/model` holds **only** `xgboost-model` (Lab 5 serving container
      enumerates that directory) and `/tmp/out` holds `model.ubj`, `evaluation_metrics.json`,
      `model_metadata.json`.
- [x] Optional, free, strongly suggested: run the same local command inside
      `python:3.8` in Docker with `pip install xgboost==1.7.6 pandas==1.5.3 scikit-learn pyarrow`
      to see exactly what the container will see (syntax and API), before paying for a run.

## Phase 3 - Docs that need no AWS (FREE)

- [x] `docs/lab4-xops-assessment.md` with the five exact headings the TA greps for:
      `## Pipeline Stage Mapping` (five phases -> three CodePipeline stages, table lifted
      from the kit plus where each phase runs in `buildspec.yml`, why one CodeBuild project
      and not five stages, where registration sits relative to the gate and how post_build's
      Rejected keeps a red build from leaving an approvable package, any `pipeline.yaml`
      edits we made and why),
      `## Champion-Challenger` (one numeric, binary criterion, e.g. "promote the candidate
      iff `auc_roc >= champion.auc_roc + 0.005` AND `lift_ci_low > 0` AND
      `recall_top10 >= champion.recall_top10 - 0.02`; otherwise keep the champion"; say why
      the margin sits above the run-to-run noise the handout measured),
      `## Retraining Triggers` ((a) scheduled: EventBridge Scheduler
      `cron(0 6 ? * MON *)` -> `sagemaker:StartPipelineExecution` on `northstar-churn-pipeline`;
      (b) performance: a CloudWatch alarm on a production-AUC / drift metric published by
      Model Monitor (Lab 6), e.g. `AUC < 0.70 for 2 of 3 daily periods` -> EventBridge rule on
      the alarm state change -> same StartPipelineExecution target; both name the service
      and the threshold/expression; "designed, not deployed" is fine),
      `## DataOps Maturity` and `## MLOps Maturity` (~400 words total, the four-line
      structure from the handout, every cited path must exist at the tag: e.g.
      `glue-scripts/transform.py`, `docs/lab2-data-contract.md`, `tests/test_data.py`, a line
      number in `docs/lab4-build-green.log`, `models/churn/pipeline_definition.py`,
      `docs/lab3-evidence/mlflow-runs.csv`; gap lines must be specific, e.g. "no automated
      drift detection on served predictions (Lab 6)").
      Write the mapping, criterion and triggers now; finish the maturity text after Phase 4
      so it can cite measured lines.
- [ ] (DEFERRED to Phase 4 step 3: App role is destroyed with the stack) `docs/lab4-mlflow-runs.txt` from the existing App (E7):
      ```python
      mlflow.set_tracking_uri("arn:aws:sagemaker:us-east-1:829485866627:mlflow-app/app-ARLX53NAWKLU")
      runs = mlflow.search_runs(experiment_names=["northstar-churn"])
      ```
      then the handout's column filter and `to_string()` (never the truncated print). Check
      the file shows the ARN as `tracking_uri`, >= 3 rows, `params.max_depth`/`params.eta`/
      `params.feature_set` differing, `metrics.*` filled. Optional screenshot
      `docs/lab4-mlflow-compare.png` (extra support only).
- [x] README: a "Lab 4: CI/CD" section (layout entries for `tests/`, `data/`, `models/churn/`,
      `buildspec.yml`, `pipeline.yaml`; how to run the suite locally with the `NORTHSTAR_*`
      vars; the local training check; what `docs/lab4-*` contains; the prerequisites list
      in our words with our names filled in).
- [x] `requirements.txt`: add anything the new tests import that the kit's list lacks
      (expected: nothing; `boto3` is there).
- [x] Commit everything so far (done 2026-10-30; push is Phase 4 step 4). Pushing is free and harmless until the CI stack exists.

## Phase 4 - AWS session (**COSTS MONEY**: one or two sittings, same-day teardown)

Ask before step 1. Expected wall clock: 3-4 hours spread over the runs (each pipeline run
10-15 min, each failure-demo wait ~5 min more for the alarm). Expected spend $2-5.
Tags: **[$]** bills, **[free]** API calls only, **[by hand]** Boston does it in a browser.

1. **[$] Re-deploy the Lab 2 stack with the E1 IAM change.** `terraform plan -out=tfplan`
   in `infrastructure/environments/dev`, read it (expect ~53 adds incl. the changed policy),
   `terraform apply tfplan`. NAT meter starts. Wait for apply to finish before any Glue job
   ("Could not find S3 endpoint or NAT gateway" otherwise). Tee to `docs/lab4-apply-output.txt`
   (not graded; our record).
2. **[$] Populate the data bucket.** `bash scripts/run-lab2-pipeline.sh` (crawler ->
   transform -> feature-engineer, ~6 min, ~$0.25). Confirm `features/customers/` has two
   Parquet parts and `event_time` is now; 9,999 rows. This also satisfies the 48-h rule
   for the next ~2 days.
3. **[free] Prerequisites 1, 2, 4 and the E1 check.**
   - Artifacts bucket: `AB=northstar-dev-cicd-artifacts-829485866627`, `create-bucket`,
     `put-bucket-versioning Status=Enabled` (us-east-1: no LocationConstraint).
   - GitHub connection: `aws codeconnections create-connection --provider-type GitHub
     --connection-name northstar-github`; then **[by hand]** console: Developer Tools ->
     Settings -> Connections -> `northstar-github` -> Update pending connection -> install
     the "AWS Connector for GitHub" app on `bostonjsharp`, grant it `cs401r-bsharp02` ->
     Connect. `list-connections` must say `AVAILABLE`. Keep the ARN.
   - Spot quota: `aws service-quotas get-service-quota --service-code sagemaker --quota-code L-29688C85` >= 1.
   - **MLflow export (moved from Phase 3)**: now that `northstar-dev-MLEngineer` exists again,
     run the Phase 3 snippet (experiment `northstar-churn`, App ARN in `docs/lab3-mlflow-app-arn.txt`)
     to write `docs/lab4-mlflow-runs.txt`; check >= 3 rows, params differ, tracking_uri is the App ARN.
   - `ROLE=$(terraform -chdir=infrastructure/environments/dev output -raw ml_engineer_role_arn)`;
     `aws iam simulate-principal-policy --policy-source-arn $ROLE` for `iam:PassRole` (on the
     role ARN), `sagemaker:CreateTrainingJob` + `sagemaker:AddTags` (on
     `arn:aws:sagemaker:us-east-1:829485866627:training-job/x`), `sagemaker:CreateModelPackage`
     (on `.../model-package/northstar-churn-models/1`). Every row must be `allowed`. Fix
     Terraform and re-apply (free, seconds) if not.
4. **[free] Push the complete work to `main`.** No pipeline exists yet, so nothing runs.
   This is the commit the stack's first run will build, so it must contain the finished
   `churn_model.py`, the full test suite, `data/feature_engineering.py`, `requirements.txt`,
   `pytest.ini`, both kit YAMLs.
5. **[$] Deploy the CI stack** (prerequisite 6): `aws cloudformation deploy --template-file
   pipeline.yaml --stack-name northstar-cicd --parameter-overrides ProjectName=northstar
   Environment=dev GitHubOwner=bostonjsharp GitHubRepo=cs401r-bsharp02 GitHubBranch=main
   ArtifactsBucket=$AB GitHubConnectionArn=<arn> SageMakerRoleArn=$ROLE --capabilities
   CAPABILITY_NAMED_IAM`. Creating the stack starts **green run 1** immediately (~$0.15).
   While it runs: `aws sns subscribe --topic-arn <ModelApprovalTopicArn> --protocol email
   --notification-endpoint <address>` and **[by hand]** click the confirmation link
   (until then the alarm "succeeds" and nobody is notified). Then the E2 check:
   `aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::829485866627:role/northstar-codebuild-role
   --action-names sagemaker:ListModelPackages --resource-arns "*"`; if `implicitDeny`, add
   the `Resource: "*"` statement to `pipeline.yaml` and `cloudformation deploy` again
   (a stack update does not start a run).
6. **Debug loop (each retry is a push = a run, ~$0.15).** Watch
   `aws codepipeline get-pipeline-state --name northstar-pipeline` and
   `aws codebuild batch-get-builds`. On a red build read the CodeBuild log first; if it says
   `AlgorithmError: ExecuteUserScriptError`, the real reason is in CloudWatch
   `/aws/sagemaker/TrainingJobs` (`MSYS_NO_PATHCONV=1 aws logs tail ...`). Known first-run
   suspects, in order: E1 (AccessDenied in the SageMaker pipeline step), E2 (regression
   test AccessDenied), E4 (synthetic-batch tests), a 3.9+ syntax slip in `churn_model.py`,
   `event_time` staleness (> 48 h since step 2). On "Pipeline timed out" (E6):
   `aws sagemaker stop-pipeline-execution` at once. Fix locally, verify with the Phase 2
   local commands, commit, push.
7. **[free] Approve the champion.** From the green run's log line `Registered: arn:...`,
   `aws sagemaker update-model-package --model-package-arn <arn> --model-approval-status
   Approved`. This is the version the final run's regression test will name. (E9: the
   CodePipeline ManualApproval action is separate; approve it in the console if you like,
   or let the next execution supersede it. E10: never v1.)
8. **[$] Failure demonstration (Task 2, 20 of the 30 points).** Feature job must be < 48 h
   old (step 2 was today; otherwise `aws glue start-job-run --job-name northstar-dev-feature-engineer`
   and wait ~90 s, [$]). Define `TOPIC` and the handout's `snap` function in the shell,
   from the repo root. Alarm must read **OK** before starting (it notifies on state change).
   1. `snap "BEFORE"` -> `docs/lab4-failure-demo.txt` (note the newest package version).
   2. Add one deliberately failing assertion to **`tests/test_data.py`** (E3; pre_build),
      e.g. a test named `test_deliberate_ci_failure_demo` asserting `False` with a message
      that says what it is. Commit with a message that says so, push. ~$0.15.
   3. Wait: build FAILED at PRE_BUILD, no BUILD phase; alarm ALARM ~2 min after the build
      ends. `snap "AFTER FAILED RUN"`. `save_log <build-id> docs/lab4-build-failed.log`
      (`MSYS_NO_PATHCONV=1`). Check the log shows the failing assertion and PRE_BUILD failed.
   4. **[by hand]** Screenshot the ALARM email (subject starts `ALARM: "northstar-ci-build-failure"`,
      received time visible, address may be blurred) -> `docs/lab4-alarm-email.png`.
   5. `git revert <failure sha>` (keeps both commits in `main` history, which is required),
      push. ~$0.15. Wait for the green build and for the alarm to return to OK (next 5-min
      period with no failed builds). `snap "AFTER REVERT"`: newest version is BEFORE + 1,
      `PendingManualApproval`; history shows `Successfully executed action`; the
      subscription shows an ARN, not `PendingConfirmation`. **This revert build is the final
      green run.** Its log must show the regression test PASSED with the
      `champion vN auc=...` warning and the fairness flag.
9. **[free] Lineage on the final green package (Task 3).** From the final build's log get
   the package ARN, training job name and `COMMIT_SHA`; from
   `describe-training-job` get the input channel URI (`s3://northstar-dev-data-829485866627/features/customers/`)
   and the data timestamp (feature job run time / `event_time`). Then
   `aws sagemaker update-model-package --model-package-arn <arn> --customer-metadata-properties
   training_data_uri=...,training_data_timestamp=...,commit_sha=<full 40-char sha>,auc_roc=...,precision_top10=...,recall_top10=...,lift_ci_low=...,lift_ci_high=...`
   (the full SHA, so the TA finds it in `main` history). Then
   `aws sagemaker describe-model-package --model-package-name <full ARN> > docs/lab4-model-package.json`
   (short form is rejected). Read it: `CustomerMetadataProperties` present, `ModelApprovalStatus`
   PendingManualApproval.
10. **[free, BEFORE any teardown] Capture block** (handout "Evidence You Submit"), with
    `MSYS_NO_PATHCONV=1` on the `save_log` calls:
    - block 1 -> `docs/lab4-pipeline-runs.txt` (every CodePipeline execution, every CodeBuild
      build with phases and `pytest:` summary lines, every SageMaker Pipeline execution);
    - `save_log <final green build id> docs/lab4-build-green.log`;
    - `list-model-packages ... --output json > docs/lab4-model-registry.json` (shows the
      Approved champion and the final PendingManualApproval version).
    Open and read each: green log has `test_data.py`, `test_features.py`, `test_model.py`,
    `test_fairness.py` each with >= 2 `PASSED`, >= 5 `test_features.py ... PASSED`, the
    champion warning, the fairness warning, `Pipeline succeeded`, `Registered: arn:...`;
    failed log has the assertion and PRE_BUILD failed; pipeline-runs has a SUCCEEDED build
    whose phases reach POST_BUILD; failure-demo has all three snapshots. None of these can
    be regenerated after step 11.
11. **[stops the meter] Teardown, in this order.**
    1. `aws cloudformation delete-stack --stack-name northstar-cicd` and
       `wait stack-delete-complete` (ends CodePipeline/CodeBuild; the GitHub webhook goes
       with it, so later pushes run nothing).
    2. Empty the versioned artifacts bucket (all versions + delete markers, same loop as
       `teardown-lab2.sh` step 1) and `aws s3api delete-bucket --bucket $AB`.
    3. `MSYS_NO_PATHCONV=1 aws logs delete-log-group --log-group-name /aws/codebuild/northstar-ci`.
    4. Set the Approved champion to **Rejected** (handout: no Approved package may point at
       a deleted artifact). Optional tidy-ups (free either way): delete the SageMaker
       pipeline definition `northstar-churn-pipeline`, the CodeBuild report group
       `northstar-ci-northstar-test-reports`, the auto-created SageMaker Experiment.
    5. `bash scripts/teardown-lab2.sh` (NAT, Domain, Glue, Feature Group, data bucket).
       Keep the MLflow App, the model package group and the GitHub connection (all free;
       Labs 5-6 reuse them).
    6. Read-only checks: no NAT, no endpoints, no tracking servers, no CodeBuild projects,
       no CodePipeline, `list-model-packages` shows no Approved version. Save as
       `docs/lab4-teardown-output.txt` (ours, not graded; the handout has no teardown gate
       this time, but Lab 3's habit is cheap).
12. Finish the maturity text (cite lines from the captured logs), commit `docs/` and the
    assessment, push (docs-only, runs nothing now).

## Phase 5 - Submit

- [ ] `bash scripts/check-secrets.sh`; the account id is not a secret, but no keys, no
      `.env`, no `*.tfvars`.
- [ ] Every file in the rubric table below exists at exact name; both demo commits are in
      `main` history (`git log --oneline | grep -i -E "deliberate|revert"`).
- [ ] `aws sagemaker list-endpoints` empty, `list-mlflow-tracking-servers` empty,
      `aws ec2 describe-nat-gateways` none available.
- [ ] `git tag lab4-submit && git push origin lab4-submit`; paste the repo URL in Canvas.

---

## Rubric cross-reference

| Points | Item | Closed by | Evidence (exact file names) |
|---|---|---|---|
| 12 | All 4 test categories, >= 2 PASSED each; fairness flag in warnings summary | Phase 1, Phase 4 step 10 | `tests/test_data.py`, `test_features.py`, `test_model.py`, `test_fairness.py`; `docs/lab4-build-green.log` |
| 10 | >= 5 feature unit tests with normal / boundary / edge | Phase 1c | `tests/test_features.py` (names and bodies), >= 5 PASSED lines in `docs/lab4-build-green.log` |
| 8 | Regression test vs newest Approved champion; PASSED, not skipped; champion warning | Phase 1d, Phase 4 steps 7-8 | `tests/test_model.py`; `docs/lab4-build-green.log` (`champion vN auc=...`); `docs/lab4-model-registry.json` (version N Approved) |
| 10 | 5 phases present, ordered, mapped; green run trained **our** `train()` and reached Register | Phase 2, Phase 3 mapping, Phase 4 step 10 | `buildspec.yml`, `pipeline.yaml`; `## Pipeline Stage Mapping` in `docs/lab4-xops-assessment.md`; `docs/lab4-pipeline-runs.txt` (SUCCEEDED through POST_BUILD); `docs/lab4-build-green.log` (Pipeline succeeded, package registered); TA runs `git show <sha>:models/churn/churn_model.py` |
| 8 | Pipeline halts on test failure in the phase that owns tests | Phase 4 step 8 (E3) | `docs/lab4-failure-demo.txt` AFTER FAILED RUN (FAILED, PRE_BUILD FAILED, no BUILD); `docs/lab4-build-failed.log`; failure commit in `main`, SHA matches the build |
| 6 | Registry promotion only on green gates | Phase 4 step 8 | `docs/lab4-failure-demo.txt` (same newest version BEFORE / AFTER FAILED RUN; +1 PendingManualApproval AFTER REVERT); `docs/lab4-model-registry.json` |
| 6 | Failure notification demonstrably delivers | Phase 4 steps 5, 8 | `docs/lab4-failure-demo.txt` (OK -> ALARM, `Successfully executed action`, subscription ARN not PendingConfirmation); `docs/lab4-alarm-email.png` |
| 5 | Champion-challenger criterion numeric and binary | Phase 3 | `## Champion-Challenger` in `docs/lab4-xops-assessment.md` |
| 8 | Both retraining triggers, threshold/schedule + AWS service that fires them | Phase 3 | `## Retraining Triggers` |
| 4 | >= 3 MLflow App runs, differing params, metrics filled; tracking URI is an App ARN | Phase 3 (E7) | `docs/lab4-mlflow-runs.txt` |
| 3 | Lineage metadata (data URI + timestamp, commit SHA, metrics) on the final package | Phase 4 step 9 | `docs/lab4-model-package.json` |
| 10 | Maturity levels backed by specific files / measured lines that exist at the tag | Phase 3 + Phase 4 step 12 | `## DataOps Maturity`, `## MLOps Maturity` |
| 6 | Gap analysis specific | same | `Gap to Level` lines |
| 4 | Priority investment names a tool or practice | same | `Top priority investment` lines |
| req | Both demo commits left in `main` | Phase 4 step 8 | git history |
| req | Never commit secrets (automatic zero) | Phase 5 | `scripts/check-secrets.sh` |

---

## Gotchas collected from the handout and kit

- The training container is XGBoost 1.7-1: Python 3.8, pandas 1.x, xgboost 1.7. Our laptop
  runs 3.14 / 3.4.1. Numbers differ slightly; both pass the gates on the reference data.
  Keep `churn_model.py` to 3.8 syntax; the kit's `from __future__ import annotations` is
  what makes the skeleton's `tuple[...]` annotations survive.
- Only `models/churn/` is uploaded to the training job. `import churn_training_skeleton`
  works locally and fails in the container. Copy functions.
- No `mlflow`/`sagemaker` imports at the top of `churn_model.py`; the container has neither.
- `/opt/ml/model` must hold only `xgboost-model` (serving container enumerates it; defect 72).
  `train_sagemaker.py` handles this; do not write extra files to `model_dir`.
- `post_build` does **not** run when `pre_build` fails (CodeBuild goes to FINALIZING), which
  is why the alarm watches `AWS/CodeBuild FailedBuilds`, not the custom metric. Do not
  "improve" this.
- SNS topic policy without `cloudwatch.amazonaws.com` fails silently; the only trace is
  `Failed to execute action` in `describe-alarm-history --history-item-type Action`. The kit
  template has it; check the history anyway (it is graded evidence).
- An alarm notifies on state **change**; start the demo from OK. OK -> ALARM takes ~2 min
  after the build ends; ALARM -> OK comes with the next 5-minute period that has no failed
  build (`TreatMissingData: notBreaching`), not with the green build itself.
- `Successfully executed action` = SNS accepted the message, not that anyone got it. Delivery
  is proved by the confirmed subscription ARN and the email screenshot.
- CodeBuild does not carry `export`s across phases; the buildspec re-derives
  `ACCOUNT_ID`/`DATA_BUCKET` in every phase. Same if we add commands.
- CodeBuild runs commands with `/bin/sh`: no `${VAR:0:8}` (defect 66); use `cut`.
- Every push to `main` is a run and a training job, even docs-only. Batch commits; delete
  the stack before the final docs push. Creating the stack runs immediately on current `main`.
- A newer pipeline execution supersedes one waiting at ManualApproval; no cleanup needed.
- `describe-model-package` needs the **full ARN**; `northstar-churn-models/N` is rejected.
- The feature job must have run < 48 h before any push, or the freshness test fails the
  build by design. Re-running it also appends to the Feature Group offline store; both
  loaders dedup (`ROW_NUMBER` in Lab 3, `sort + drop_duplicates(keep="last")` in
  `train_sagemaker.read_channel`).
- `read_channel` sorts parts and rows by `customer_id`, so the split matches the Athena
  path's `ORDER BY customer_id`; expect the Lab 3 numbers to reproduce (modulo xgboost version).
- Spot: `use_spot_instances=True`, `max_run=1800`, `max_wait=3600` stay. On-demand quota is
  not the limit on our account (15), but the kit's instructions and grading assume spot.
- `pytest_addoption` lives in `tests/conftest.py` only (defect 50); never move it.
- A missing dataset or module is a FAIL, not a skip; `ALLOW_MISSING_*` must never reach CI.
- Git Bash: `MSYS_NO_PATHCONV=1` for every `aws logs` call with a `/aws/...` log group name;
  `/tmp/...` paths are fine; `python3` is 3.13, not the venv (only matters for `save_log`,
  which is stdlib-only).
- Registry v1 (Lab 3) points at a deleted artifact; v2+ are CI's. Before the data bucket is
  destroyed, no package may be Approved.
- `docs/lab4-model-registry.json` must be captured **before** the champion is set to
  Rejected at teardown (the TA wants to see version N Approved).
