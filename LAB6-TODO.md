# Lab 6 To-Do: Monitoring & Reliability

Due **Sat Nov 28, midnight**. Tag `lab6-submit`, push the tag, paste the repo URL in Canvas.
Points: Task 1 Five-layer monitoring (35) + Task 2 Drift plan (15) + Task 3 Alert
architecture (15) + Task 4 SLOs (15) + Task 5 Runbooks (20) = 100.
**Teardown is a gate** (`docs/lab6-teardown-output.txt` must end `TEARDOWN COMPLETE`); an
endpoint or drift trigger still alive after the deadline is a further **-10**. The TA grades
the repo at the tag, never the account. `docs/lab6-monitoring-output.txt` (12 sections) is
the evidence for the 15-point dashboard line, and sections 1-11 can only be captured
**while the endpoint is live**. Tasks 2-5 are 65 points and need no AWS.

Handout: `C:\Users\bosto\Downloads\Lab_6--Monitoring & Reliability.html` (already includes all
four errata). Starter kit: `C:\Users\bosto\Downloads\Lab6-Starter-Kit.zip` (the re-issued one:
5 files, no `sample.csv`). Not copied in yet.

Suggested pacing: Phases 0-2 (free) Sat-Mon, Phase 3 (paid sitting, ~3 h) Tue or Wed,
Phase 4 right after. Thanksgiving is Thu Nov 26; do not leave Phase 3 for the holiday weekend.

### Errata received (all five, read first)

1. **Kit now exists** (Nov 18). `scripts/preflight-lab6.sh`, `scripts/teardown-lab6.sh`,
   `monitoring/run_evidently_job.py`, `monitoring/evidently_drift.py`,
   `deployment/configs/canary_deploy_realtime.py`. Our zip is the second issue (script
   uploads under `monitoring/code/`, `load_capture` de-duplicates, no `sample.csv`), so the
   "don't unzip over your file" warning does not concern us: nothing of ours exists yet.
2. **`drift_direction()` is a stub we write.** PSI drifts on `value > threshold`, KS on
   `value < threshold` (KS is a p-value). The launcher refuses to run while the stub raises.
3. **`publish_metrics.py` is ours to write**, run from the laptop after the job, never
   inside the container.
4. **Prerequisite 2**: create `ModelMonitorExecution` role + policy + output in
   `infrastructure/modules/iam/`, apply, wait 30 s.
5. **Prerequisite 3**: `cloudwatch:PutMetricData` for MLEngineer (namespace
   `/aws/sagemaker/Endpoints`) and DataEngineer (namespace `Glue`). Silent when missing:
   capture sections 7 and 9 come back empty.
6. **`$folder$` keys** (Nov 19): add `processed_$folder$` and `features_$folder$` to the
   DataEngineer S3 statement, or every Glue **re-run** fails AccessDenied.
7. **Submitting section** (Nov 19): the two-window capture block, the complete file list and
   the pre-tag checks. ModelLatency is **microseconds** (200 ms = `200000`). Every alarm name
   must contain `northstar`.
8. **Cost table corrected** (Nov 21). The handout priced one endpoint instance; the canary
   deploy runs two. Corrected 3 h session: $0.34 on 2 x t2.medium, **$0.70 on 2 x m5.large**
   (ours, already what D7 assumed). Overnight (14 h) on m5.large is $3.26; forgotten, it
   breaks the $10 budget in **~43 hours**. Custom metrics **are** prorated by the hour, and
   only for hours with data: a session's worth is a couple of cents. No work or grading
   change. If Phase 3 is ever interrupted, run `scripts/teardown-lab6.sh` before walking
   away; redeploying is ~7 min.

## What we are building, in one paragraph

Re-create the Lab 5 canary endpoint (`northstar-churn-prod`, champion v4 / canary v5, data
capture 100%), send it >= 700 distinct single-row requests, and compare the champion's
captured inputs against a baseline of the 11 endpoint features using Evidently in a
`ml.t3.medium` processing job that runs as a new `ModelMonitorExecution` role. A laptop-side
publisher turns `drift_violations.json` into CloudWatch metrics (`DriftDetected`, per-feature
drift values, and the business proxy `ChurnAlertVolume`) in namespace `NorthStar/ChurnModel`.
One dashboard, `NorthStar-AI-Platform`, shows all five layers; >= 6 `northstar-*` alarms with
P1-P4 tiers live as Terraform under `monitoring/alerts/`. `docs/lab6-runbook.md` holds the
drift plan, four SLOs and two runbooks. Everything is captured to
`docs/lab6-monitoring-output.txt`, then torn down the same sitting.

## Possible errata and our deviations (found 2026-11-21)

| # | What | Why it matters | What we do |
|---|---|---|---|
| **D1** | **Nothing is deployed and no artifact is in S3.** Lab 5 ended with the Lab 2 stack destroyed, v4/v5 `Rejected`, bucket gone. Local copies: `model/v4/`, `model/v5/` (`model.tar.gz`, gitignored). | The handout assumes "the endpoint from Lab 5". | Phase 3: re-apply the Lab 2 stack, upload both tarballs back to the **exact `ModelDataUrl` keys** of packages v4/v5, set both `Approved`, run the existing `deployment/deploy-canary.sh <v4> <v5>`. No CI stack, no retraining. Fallback if the keys cannot be restored: `canary_deploy_realtime.py` or one CI run. |
| **D2** | Handout Terraform uses `local.bucket_arn`; our module has **`local.data_bucket_arn`** (`infrastructure/modules/iam/main.tf:24-29`, a `...-data-*` wildcard ARN). | `terraform validate` fails on the pasted block. | Substitute `local.data_bucket_arn` everywhere (same as Lab 5 D3). |
| **D3** | The Sid the Nov 19 errata names, `S3DataPrefixesReadWrite`, does not exist here. Ours is **`S3DataPrefixes`** (`main.tf:274-285`). | Nothing to grep for. | Add the two `$folder$` keys to `S3DataPrefixes`. In HCL write `$folder$` literally (only `${` interpolates). |
| **D4** | **Glue jobs do not pass `--enable-metrics`** (no hit anywhere under `infrastructure/`). | Prerequisite 3's DataEngineer grant is useless without it; the `Glue` namespace stays empty. | Add `"--enable-metrics" = "true"` to both jobs' `default_arguments` in the Glue module. Capture section 7 itself is `get-job-runs`, so it fills regardless; the dashboard widget needs the metric or the EventBridge rule (D8). |
| **D5** | MLEngineer already has `PutMetricData` with `StringLike cloudwatch:namespace = /aws/sagemaker/*` (`main.tf:180-189`), which already matches `/aws/sagemaker/Endpoints`. | Prerequisite 3's MLEngineer statement may be redundant. | Add the handout's `EndpointInstanceMetrics` statement anyway (harmless, matches the handout for the TA). Check section 9 is non-empty in the sitting. |
| **D6** | Capture block is written for macOS/Linux bash. On Git Bash: `python3` may not exist, and **`--namespace /aws/sagemaker/Endpoints` is rewritten to a Windows path** by MSYS. | Section 9 silently returns nothing; `S=` is empty. | Put the block in `scripts/capture-lab6.sh` with `export MSYS_NO_PATHCONV=1` and `python` (venv) instead of `python3`. Same section titles, same commands, so `grep -c '^===== '` still gives 12. |
| **D7** | Our endpoint is 2 x `ml.m5.large` ($0.23/h), not the handout's t2.medium table. `deploy-canary.sh` attaches autoscaling, which needs non-burstable. | The handout's original table understated ours; the Nov 21 correction (erratum 8) now gives the same $0.70 for 3 h. | Keep m5.large; 3 h is ~$0.70. `teardown-lab6.sh` deregisters the scalable target. |
| **D8** | Trap 14: Glue task metrics miss driver failures; the "pipeline" alert should be an EventBridge rule on `Glue Job State Change` (FAILED/ERROR/TIMEOUT). `teardown-lab6.sh` deletes alarms by name but **not** EventBridge rules or SNS topics. | An orphan rule/topic (free, but the handout lists rules under required teardown). | Define rule + SNS topic + alarms in `monitoring/alerts/main.tf`; run `terraform destroy` there **before** `teardown-lab6.sh`. Rule named `northstar-glue-job-failed`; alarm on `AWS/Events` `TriggeredRules`. |
| D9 | `.venv` was created at the old repo path (`D:\dev\bost\cs401r-bsharp02`); launcher shims may be stale. No `evidently`, no `sagemaker` SDK installed (neither is needed locally except to unit-test `drift_direction`, which needs neither). | `pip` shim may fail. | Use `.venv/Scripts/python.exe -m pip`. Local needs: `boto3`, `pandas`, `pyarrow` (baseline export), `sagemaker-mlflow` (optional). Add `requirements-lab6.txt`. |
| D10 | Lab 5's local `model/datacapture/` has only 30 files, mostly 200 copies of `sample.csv`. | Useless as a comparison window (dedup leaves a handful of rows). | Generate fresh traffic: `monitoring/invoke_traffic.py`, one row per request, distinct rows. ~10% goes to canary, so send >= 700 to get >= 500 on champion (plan on 800). |
| D11 | Publisher runs as the `cs401r-lab` IAM user (us), not a role. Capture block's default names: namespace `NorthStar/ChurnModel`, metrics `ChurnAlertVolume` and `DriftDetected`, dimension `EndpointName`. | Different names mean editing the capture block. | Use exactly those names. Lab 4's doc mentions `NorthStar/Model`; ignore it, Lab 6 names win. |
| D12 | MLflow logging (`northstar-drift` experiment) has no rubric line. The MLflow App survived Lab 4/5 per our notes. | Optional work. | Do it inside `publish_metrics.py` behind `--mlflow-arn`; write `docs/lab6-mlflow-runs.txt` if it works, skip without guilt if the App is gone. |

Things that **check out**: `deployment/configs/sample.csv` is intact (11 floats, no header);
`endpoint-config-canary.json` already has `DataCaptureConfig` on at 100% to
`s3://northstar-dev-data-829485866627/datacapture/`; MLEngineer already has
`S3DataCaptureWrite` (Lab 5 prereq 5) and capture worked on Nov 13; endpoint and variant
names match the kit defaults (`northstar-churn-prod`, `champion`, `canary`); the rollback
alarm is already named `northstar-churn-rollback-latency` at `200000`; `lab5-submit` exists.

## COST MAP (read before any AWS step)

| Thing | Cost | Needed? | Rule |
|---|---|---|---|
| Endpoint, 2 x `ml.m5.large` | $0.23/hr | YES | Up for the sitting only; ~3 h |
| Lab 2 stack: NAT + public IPv4 | ~$0.05/hr | YES (bucket, roles, Glue) | Up for the sitting only |
| Glue pipeline run (crawler + 2 jobs) | ~$0.25 | YES (baseline source + pipeline metrics) | Once; a second run only to prove the `$folder$` fix if time allows |
| Evidently job, `ml.t3.medium` | ~$0.002/run | YES | Run as often as needed |
| CloudWatch alarms (6-8) | $0.10/alarm-month, prorated | YES | Destroyed same sitting: cents |
| Custom metrics | $0.30/metric-month, prorated hourly, only for hours with data (erratum 8) | YES | 9 combos for one session: cents |
| Dashboard | free (first 3) | YES | Delete at teardown anyway |
| CI stack / retraining | ~$0.15/run | NO (D1) | Only as fallback |

Budget alarm is $10/month. Working estimate: **$1.25-2.50** for one sitting.

## Where things stand (2026-11-21)

- AWS: nothing billing. Lab 2 stack destroyed, no endpoint, v4/v5 `Rejected`.
- Repo: clean at `9eb2e6f` (= `lab5-submit`). No `monitoring/`, no `docs/lab6-*`, no kit files.
- Local: `model/v4/model.tar.gz`, `model/v5/model.tar.gz`, `glue-scripts/`,
  `scripts/run-lab2-pipeline.sh`, `scripts/check-secrets.sh`, `deployment/deploy-canary.sh`.

---

## Phase 0 - Setup (FREE)

Status 2026-11-21: **done, nothing applied to AWS, nothing committed.**

- [x] Unzip the kit at the repo root (5 files). `sample.csv` untouched.
- [x] Read `preflight-lab6.sh` and `teardown-lab6.sh`. Findings: preflight needs a live
      endpoint (its check 1), so it only runs in Phase 3; teardown deletes every model named
      `northstar-*` and every alarm containing `northstar` or starting `TargetTracking`, but
      no EventBridge rule, SNS topic or dashboard (D8 stands); its output file contains ANSI
      colour codes (`grep 'TEARDOWN COMPLETE'` still matches). Both pass `bash -n`.
- [x] IAM (`infrastructure/modules/iam/main.tf` + `outputs.tf`), with D2/D3 substitutions:
      `ModelMonitorExecution` role/policy/attachment/output; `EndpointInstanceMetrics` in
      MLEngineer; `GlueJobMetrics` in DataEngineer; the two `$folder$` keys in `S3DataPrefixes`.
- [x] `model_monitor_execution_role_arn` also output from `infrastructure/environments/dev`.
- [x] Glue module: `--enable-metrics` in `local.common_arguments` (both jobs, D4).
- [x] `terraform fmt` + `validate` + `plan`: **56 to add, 0 to change, 0 to destroy**
      (Lab 5's 53 + role, policy, attachment).
- [x] `requirements-lab6.txt`. Nothing to install: boto3, pandas, pyarrow, pytest, mlflow
      and sagemaker-mlflow were already in the venv from Lab 3. `python -m pip` works.
- [x] `.gitignore`: `*.tfstate*` and `.terraform/` already cover `monitoring/alerts/`.
      **Fixed a trap:** the Lab 4 rule `reports/` also ignored `monitoring/reports/` (the two
      graded drift JSON files). Now anchored as `/reports/`.

## Phase 1 - Monitoring code (FREE; Task 1 + Task 3)

Status 2026-11-21: **written and checked offline; nothing applied to AWS.**
`pytest monitoring/tests` 18 passed; `terraform validate` clean in `monitoring/alerts/`;
baseline export and traffic generator dry-run against `.dryrun/features`.

- [x] `monitoring/evidently_drift.py`: `drift_direction()` implemented from two explicit
      sets (`STATISTIC_METHODS` use `>`, `P_VALUE_METHODS` use `<`); unknown method or NaN
      raises. The `detail` string uses the same sets. `DRIFT_PLAN` left at the kit's five
      features (3 PSI at 0.2, 2 KS at 0.05); Phase 2a justifies or changes it.
- [x] `monitoring/tests/test_drift_direction.py`: the handout's shift table both ways, the
      trap itself, boundaries, unknown method, and that the container's `FEATURE_COLUMNS`
      equals `models/churn/churn_model.py`'s. Run: `.venv/Scripts/python.exe -m pytest monitoring/tests -q`.
- [x] `monitoring/features.py` (new, shared by the laptop scripts): reproduces the Lab 3/4
      split (sorted by `customer_id`, 70/30, seed 42, stratified). Checked: **6,999 train /
      3,000 val**, the same counts as `model/v4/evaluation_metrics.json`.
- [x] `monitoring/export_baseline.py`: baseline = the 6,999 **training** customers, 11
      columns, header. `monitoring/baseline/baseline.csv` on disk now is a **provisional**
      copy built from `.dryrun/features`; Phase 3 step 5 regenerates it from S3 and uploads.
- [x] `monitoring/invoke_traffic.py`: distinct **validation** customers, one row per
      request, counts per variant and scores >= 0.5; writes
      `monitoring/reports/traffic_summary.json`.
- [x] `monitoring/publish_metrics.py`: `NorthStar/ChurnModel`, dimension `EndpointName`:
      `DriftDetected`, `DriftViolationCount`, `FeaturePSI` and `FeatureKSPValue` (separate
      names because they alert in opposite directions; extra `Feature` dimension),
      `ChurnAlertVolume`, `ChurnAlertVolumeRatio` (volume / trailing 7-day average, 1.0
      with no history). 9 metric/dimension combinations, cents for one session (erratum 8). `--dry-run`,
      optional `--mlflow-arn` (writes `docs/lab6-mlflow-runs.txt`).
- [x] `monitoring/dashboards/build_dashboard.py`: 16 widgets, five labelled rows, 11 metric
      widgets. `--put` creates `NorthStar-AI-Platform`; `--export` writes the committed
      `northstar-dashboard.json` from `get-dashboard`. **Unverified until the sitting:** the
      Glue metric names/dimensions (`glue.driver.aggregate.numCompletedTasks`, `JobRunId=ALL`,
      `Type=count`); check with `aws cloudwatch list-metrics --namespace Glue` and adjust.
- [x] `monitoring/alerts/main.tf` + `README.md`: 4 SNS topics (one per tier), EventBridge
      rule `northstar-glue-job-failed`, 9 metric alarms and 1 composite:

      | Alarm | Tier |
      |---|---|
      | `northstar-p1-endpoint-5xx` (>= 5/min, 2 of 2) | P1 |
      | `northstar-p1-latency-p95-500ms` (> 500000, 5 of 5) | P1 |
      | `northstar-p2-latency-p95-200ms` (> 200000, 3 of 3) | P2 |
      | `northstar-p2-cpu-high` (> 80, 5 of 5) | P2 |
      | `northstar-p3-feature-drift` (DriftDetected >= 1; notifies via composite `northstar-p3-feature-drift-notify`) | P3 |
      | `northstar-p3-glue-job-failed` (`AWS/Events` TriggeredRules >= 1) | P3 |
      | `northstar-p3-churn-alert-volume-drop` (ratio < 0.7) | P3 |
      | `northstar-p4-drift-job-stale` (no datapoint in 24 h; missing = breaching) | P4 |
      | `northstar-suppress-retraining-window` (suppressor, not an alert) | - |

      README: tier table with escalation action per tier, three suppression rules (rule 1 is
      real: composite alarm `actions_suppressor`), threshold notes, observer-vs-executor
      role note, operating commands. Capture section 10 lists metric alarms only, so the
      composite will not appear there; that is expected.
- [x] `scripts/capture-lab6.sh 1|2`: the handout's blocks verbatim plus D6 fixes; refuses
      to overwrite a finished file; lists empty sections and AccessDenied lines after window 1.
- [x] `terraform init -backend=false` + `validate` in `monitoring/alerts/` (commit
      `.terraform.lock.hcl`; `.terraform/` and state are ignored); `bash -n` on the script.

## Phase 2 - Documents (FREE; Tasks 2, 4, 5) -> `docs/lab6-runbook.md`

### 2a. Drift detection plan (Task 2, 15 pts)
- [ ] Drift types ranked for retail churn, tied to NorthStar drivers (holiday promotions
      pulling `days_since_last_purchase` and `purchase_frequency_30d`, catalog refresh
      moving `category_diversity_score`, price/economy moving `avg_order_value`).
- [ ] Per-feature table: feature, test (PSI / KS), baseline window, numeric threshold,
      why. >= 3 features; must equal `DRIFT_PLAN` exactly. State which variant is analyzed
      (champion) and why, and the 500-record minimum.
- [ ] Concept-drift proxy with its lag stated (prediction-score distribution shift from
      captured `endpointOutput`, lag ~1 day; early label proxy such as 30-day no-purchase
      rate among top-decile scores, lag 30 days vs 90 for the true label).

### 2b. SLOs (Task 4, 15 pts)
- [ ] Four rows, each with a **numeric** error budget and a **specific** freeze trigger:
      availability 99.5% (216 min / 30-day month, or 0.5% of requests), latency p95 <= 20000
      us, Recall@10% >= 0.25 weekly, fairness gap <= 10 pp weekly. Note that our own cold
      start (~24,000 us) spends latency budget; cite our measured Lab 5 p95 (5,310 us).

### 2c. Two runbooks (Task 5, 20 pts)
- [ ] Runbook A (data drift, PSI > 0.2 on `days_since_last_purchase`) and Runbook B (latency
      spike, ModelLatency p95 > 500000 for > 5 min). All six sections, no TBD. Each has a
      graceful-degradation containment option (A: fall back to the recency
      `churn_risk_score` rule / hold offers; B: shift 100% to champion with
      `deployment/configs/rollback-action.sh`, or serve cached scores). Resolution
      verification names an alarm returning to `OK` and a metric value.
- [ ] Fill real numbers in from Phase 3 afterwards (drift values, latency, alarm states).

## Phase 3 - AWS session (**COSTS MONEY**: one sitting, same-day teardown)

Ask before step 1. Expected wall clock ~3 h, expected spend $1.25-2.50.
Tags: **[$]** bills, **[free]** API calls only, **[Boston]** run by hand with `!` if blocked.

1. **[$] Apply the Lab 2 stack** (`terraform -chdir=infrastructure/environments/dev apply`).
   NAT meter starts. Wait 30 s for IAM. Check the quota:
   `get-service-quota --quota-code L-0CE343FE` (t3.medium processing, expect 4).
2. **[$] Run the pipeline**: `bash scripts/run-lab2-pipeline.sh` (~6 min). Confirm `Glue`
   namespace metrics appear (`list-metrics --namespace Glue`).
3. **[free] Restore the models (D1)**: read `ModelDataUrl` from packages v4 and v5, upload
   `model/v4/model.tar.gz` and `model/v5/model.tar.gz` to those exact keys, set both
   `Approved`.
4. **[$] Deploy**: `bash deployment/deploy-canary.sh <v4-arn> <v5-arn>` (~7 min). **Endpoint
   meter starts: $0.23/h.** Note the InService time.
5. **[free] Baseline**: `export_baseline.py` -> `monitoring/baseline/baseline.csv` + S3.
6. **[free] Traffic**: `invoke_traffic.py --n 800`. Wait ~5 min, confirm objects under
   `datacapture/northstar-churn-prod/champion/`.
7. **[free] `bash scripts/preflight-lab6.sh`**: all PASS; copy the role ARN it prints.
8. **[$] Drift job**: `python monitoring/run_evidently_job.py --bucket ... --role-arn ...
   --endpoint northstar-churn-prod --variant champion` (~4 min wall, ~$0.002). Read the
   "dropped N duplicate payload(s)" line. Need `captured_records >= 500`,
   `underpowered_window: false`. If a KS feature drifts on clean traffic, investigate
   before accepting it.
9. **[free] Download** `drift_report.json`, `drift_violations.json` (and the HTML for
   MLflow) to `monitoring/reports/`.
10. **[free] Publish**: `publish_metrics.py`. Verify both metrics with
    `get-metric-statistics`. Optional MLflow run -> `docs/lab6-mlflow-runs.txt`.
11. **[$] Alarms**: `terraform -chdir=monitoring/alerts apply` (cents).
12. **[free] Dashboard**: `python monitoring/dashboards/build_dashboard.py --put --export`
    -> `monitoring/dashboards/northstar-dashboard.json`. First run
    `aws cloudwatch list-metrics --namespace Glue` and fix the Glue widget if the names differ.
13. **[$] Optional (~$0.15, 5 min)**: re-run one Glue job to prove the `$folder$` fix and
    get a second run in section 7.
14. **[free] Window 1**: `bash scripts/capture-lab6.sh 1`. **Open the file and read all 11
    sections.** Empty 7 or 9, or any AccessDenied: fix now, re-run, endpoint still up.
    CPU/memory need a few minutes of endpoint life after the grant.
15. **[free]** `terraform -chdir=monitoring/alerts destroy` (removes the EventBridge rule
    SNS topics and composite alarm the kit script does not know about, D8). Delete the
    dashboard (`aws cloudwatch delete-dashboards --dashboard-names NorthStar-AI-Platform`).
16. **[stops the endpoint meter]** `bash scripts/teardown-lab6.sh` ->
    `docs/lab6-teardown-output.txt` ending `TEARDOWN COMPLETE`.
17. **[free] Window 2**: `bash scripts/capture-lab6.sh 2`. All three lines read 0.
18. **[Boston if blocked] Lab 2 teardown**: `bash scripts/teardown-lab2.sh`, wait ~15 min
    after the last Glue run first; set v4/v5 back to `Rejected` (Lab 5 D10 rule). Billing
    sweep all zero.

## Phase 4 - Submit

- [ ] Back-fill real numbers into `docs/lab6-runbook.md` and `monitoring/alerts/README.md`.
- [ ] README: add a Lab 6 section (what is where, how to re-run).
- [ ] Handout pre-tag checks: 12 section headers; baseline header has 11 columns; 0
      `raise NotImplementedError`; `captured_records >= 500`, `underpowered_window` false;
      dashboard JSON loads and has widgets; every ModelLatency threshold is 200000/500000;
      `TEARDOWN COMPLETE` present; `bash scripts/check-secrets.sh` CLEAN;
      `git status --short monitoring/alerts/` shows no tfstate.
- [ ] Commit, `git tag lab6-submit`, `git push origin main lab6-submit`.
- [ ] **[Boston]** Paste the repo URL in Canvas.

---

## Rubric cross-reference

| Points | Item | Closed by | Evidence (exact file names) |
|---|---|---|---|
| 15 | All 5 layers in the dashboard | P1 dashboard, P3 steps 10-14 | `monitoring/dashboards/northstar-dashboard.json`, `docs/lab6-monitoring-output.txt` sections 5-9, 11 |
| 10 | Evidently baseline + drift run | P1, P3 steps 5-9 | `monitoring/baseline/baseline.csv`, `monitoring/reports/drift_report.json`, `drift_violations.json`, `monitoring/evidently_drift.py`, output sections 3-4 |
| 5 | Business custom metric | P1 publisher, P3 step 10 | `monitoring/publish_metrics.py`, output section 5 |
| 5 | Dashboard JSON committed | P3 step 12 | `monitoring/dashboards/northstar-dashboard.json` |
| 5+5+5 | Drift plan: NorthStar drivers, per-feature tests, concept proxy | P2a | `docs/lab6-runbook.md` |
| 8+4+3 | >= 6 tiered alerts, escalation, suppression | P1 alerts | `monitoring/alerts/main.tf`, `monitoring/alerts/README.md`, output section 10 |
| 10+5 | 4 SLOs with budgets, freeze triggers | P2b | `docs/lab6-runbook.md` |
| 10+6+4 | 2 runbooks, degradation fallback, observable verification | P2c | `docs/lab6-runbook.md` |
| gate | Teardown | P3 steps 15-18 | `docs/lab6-teardown-output.txt`, output section 12 |

## Gotchas collected from the handout, the kit and our own checks

- KS is a p-value: drift is `<`. One shared `>` reports "no drift" on maximally drifted data.
- ModelLatency is microseconds. 200 ms = 200000, 500 ms = 500000, SLO 20 ms = 20000.
- Cold start is ~6x steady state; never `evaluation_periods = 1` on latency.
- One CSV row per request, >= 500 distinct rows on the analyzed variant; duplicates are dropped.
- Capture lags minutes behind invocations; the launcher refuses an empty prefix.
- Use `ModelMonitorExecution`, never the read-only `ModelMonitor`, for the job.
- Image tag `1.4-2-py312-cpu-py3` and `evidently==0.7.21` are load-bearing. The pip
  "dependency conflict" error in the job log is expected.
- Empty metrics = missing permission, not an idle system (Prereq 3, D4, D6).
- Git Bash mangles `/aws/sagemaker/...` arguments: `MSYS_NO_PATHCONV=1` (D6). AWS CLI on
  Windows chokes on non-ASCII output; use boto3 where it matters.
- IAM takes ~30 s to propagate; a retry inside that window looks like the fix failed.
- Alarms without `northstar` in the name are invisible to the capture block and teardown.
- Do not use anomaly-detection alarms (orphan detectors survive `terraform destroy`).
- No timers. If one is ever added, delete it before the endpoint.
- Never commit `monitoring/alerts/terraform.tfstate`.
- Custom metrics cannot be deleted; they age out. Expected. They stop billing the hour we
  stop publishing.
- A forgotten 2 x m5.large endpoint passes the $10 budget in ~43 hours. Never end a sitting
  with the endpoint up, even half done.
- Keep AWS-facing description strings ASCII.
