# Lab 6: Drift Plan, SLOs and Runbooks

NorthStar churn model, endpoint `northstar-churn-prod` (champion = registry v4, canary = v5).
This document covers Task 2 (drift detection plan), Task 4 (SLOs) and Task 5 (runbooks).
The alert specification it refers to is `monitoring/alerts/README.md`; the evidence for
every alarm, metric and job named here is `docs/lab6-monitoring-output.txt`.

Numbers marked *measured* come from our own runs: v4's evaluation
(`model/v4/evaluation_metrics.json`, 2026-11-13), the Lab 5 canary run
(`docs/lab5-deployment-plan.md`, 2026-11-13) and the Lab 6 sitting (section 4 below).

Units used throughout: `ModelLatency` is in **microseconds**. 20 ms = `20000`,
200 ms = `200000`, 500 ms = `500000`.

---

## 1. Drift detection plan (Task 2)

### 1.1 Which drift is most likely here

Ranked for a retail churn model whose label is "no purchase in the next 90 days":

| Rank | Type | How likely | NorthStar driver |
|---|---|---|---|
| 1 | **Data drift** (the inputs move) | Certain, several times a year | **Holiday promotions.** The model was trained on features cut off at 2026-04-01, a quiet spring window. Black Friday through December pulls `days_since_last_purchase` down and `purchase_frequency_30d` up for most of the customer base at once, and January pushes both the other way. **Catalog refresh:** a new or merged product category changes how many of the 8 categories a customer can touch, which moves `category_diversity_score` for everyone on the same day. **Price and economy:** inflation or a site-wide discount moves `avg_order_value` and `total_spend_90d` without any change in who is loyal. |
| 2 | **Concept drift** (same inputs, different outcome) | Likely, slower, and invisible for 90 days | A promotion buyer looks active (recent, frequent) but is a one-time deal-seeker who does not come back: the relationship between recency and churn changes even where the feature values look familiar. A loyalty-program change (new tier thresholds, a competitor's launch) does the same. The retention offers themselves are a third cause: once offers go to the top decile, the customers the model flags stop churning at the rate they used to. |
| 3 | **Model degradation** (the result of 1 and 2) | The consequence, not a cause | Measured as Recall@10% on matured labels (SLO 3). It is what we care about, but it is the last thing we can see: a prediction made today is not gradeable until 90 days from now. |

So the plan watches data drift directly and daily (1.2), because it is the most frequent
and the only one observable immediately, and uses a proxy for concept drift (1.3), because
waiting 90 days for labels means a quarter of bad offers before anyone knows.

Not all data drift is a problem. The December shift is real customer behaviour and the
model's ranking may survive it. Drift is a prompt for a human judgement, which is why the
drift alert is P3 and not a page (`monitoring/alerts/README.md`, alert 5).

### 1.2 Statistical test per feature

Implemented in `monitoring/evidently_drift.py` (`DRIFT_PLAN`) and run by
`monitoring/run_evidently_job.py` as a SageMaker Processing Job (`ml.t3.medium`, role
`northstar-dev-ModelMonitorExecution`). The table below is that dictionary, row for row.

| Feature | Test | Threshold | Drift when | Why this feature, why this test |
|---|---|---|---|---|
| `days_since_last_purchase` | PSI | 0.2 | PSI **>** 0.2 | Joint top feature by gain (about 87, `docs/lab3-evaluation-report.md` 1.3) and the first to move in a promotion. PSI is an effect size: it says how much of the population changed bin, which is the question for a feature the model leans on this hard. |
| `purchase_frequency_30d` | PSI | 0.2 | PSI **>** 0.2 | Third by gain (41) and the shortest window of the three frequency features, so it reacts weeks before the 90- and 180-day ones. Small integer counts bin cleanly for PSI. |
| `avg_order_value` | PSI | 0.2 | PSI **>** 0.2 | Fourth by gain (31); the feature that price changes and discounting move. PSI ignores a shift too small to change bins, which is what we want for ordinary price noise. |
| `category_diversity_score` | KS | 0.05 | p-value **<** 0.05 | Takes only nine values (k/8). A catalog refresh moves the whole distribution one step, a location shift KS picks up with few samples. Ties make KS conservative here, which errs toward fewer false alarms. |
| `total_spend_90d` | KS | 0.05 | p-value **<** 0.05 | Heavy right tail (a few customers spend 10x the median). Fixed PSI bins put the whole tail in one bucket and hide a change among big spenders; KS compares the entire cumulative distribution. |

The two tests point in **opposite directions**. PSI is a statistic that rises with drift;
Evidently reports KS as a p-value that falls with drift. `drift_direction()` applies `>`
to one and `<` to the other and raises on an unknown method, and
`monitoring/tests/test_drift_direction.py` pins both. A single shared `>` would report
"no drift" on maximally drifted data (KS p-value 0.0).

**Baseline window.** The 6,999 customers the champion was trained on: features computed
from transactions on or before the feature cutoff 2026-04-01 (30-, 90- and 180-day
lookbacks ending that day), exported by `monitoring/export_baseline.py` to
`s3://northstar-dev-data-<account>/monitoring/baseline/baseline.csv` using the same
seed-42 stratified split as training. The baseline is rebuilt only when a retrained model
is promoted, never on a schedule: a rolling baseline would absorb slow drift and never
report it.

**Comparison window.** The champion variant's captured requests
(`datacapture/northstar-churn-prod/champion/`) since the previous run, daily in production.
Champion only, because it serves 90% of traffic and is the model whose training data the
baseline describes; the canary has its own baseline question and too few records. A window
needs **at least 500 distinct records** after de-duplication; below that the job sets
`underpowered_window: true` and the result is not acted on.

**Reading the two tests together.** PSI bands we use: below 0.1 stable, 0.1 to 0.2 watch,
above 0.2 drift (alert), above 0.5 severe (our own line, used in Runbook A to choose
containment). KS at these sample sizes (500+ against 6,999) is powerful enough to flag a
shift too small to matter, and at a 0.05 threshold each KS feature will cross by chance in
about 1 run in 20 on perfectly clean traffic. A KS-only violation is therefore confirmed
on a second consecutive run before anyone acts (Runbook A, triage step 3).

The six features not in the plan are covered indirectly: `purchase_frequency_90d` and
`_180d` are slower versions of the 30-day feature (the Lab 3 ablation found the 180-day
column redundant with the other two), and `customer_tenure_days` drifts by construction
(everyone ages one day per day), so a test on it would alarm forever.

### 1.3 Concept drift: proxy signals while the label is 90 days away

A prediction made today is graded on 2027-02 data. Three signals fill the gap, in order of
how early they arrive and how much they prove:

| Signal | What is measured | Lag | What it can and cannot show |
|---|---|---|---|
| **A. Score distribution shift** | PSI of the champion's captured output scores (`endpointOutput` in the capture files) against the score distribution on the validation set, plus `ChurnAlertVolumeRatio` (today's count of scores >= 0.5 over the trailing 7-day average; alarm `northstar-p3-churn-alert-volume-drop` at < 0.7) | **1 day** | Shows the model's view of the population has changed. It **cannot** show pure concept drift: if inputs are unchanged and only the outcome changed, the scores are identical. It is an early smoke detector, not the proxy. |
| **B. Early-settled false positives** (the primary proxy) | Among customers scored in the top decile on day D, the share who make **any purchase by D+30** | **30 days** | A purchase inside the 90-day window settles that customer's label as 0 the day it happens. So this share is a hard lower bound on the final false-positive rate, known 60 days before the label matures. |
| **C. Matured label** | Recall@10% and per-tier recall on predictions made 90 days ago | **90 days** | The truth. It is SLO 3 and SLO 4, and it confirms or clears what B warned about. |

Why B works, with our numbers: v4's precision in the top decile is *measured* 0.73, so 27%
of top-decile customers buy at some point in the full 90 days. If more than 27% of a
cohort have **already** bought by day 30, that cohort's final precision is below 0.73 with
certainty, and by how much is known. The working thresholds are: warn when the 30-day
purchase share exceeds the training-period 30-day share by 5 points, and treat it as
confirmed concept drift when it exceeds 27% outright. The training-period 30-day share is
computed once from the holdout transactions (2026-04-01 to 2026-05-01) for the validation
customers.

Two conditions make B honest:

- **A control group.** The top decile receives retention offers, and an offer that works
  produces exactly the purchase B counts as a model error. 10% of each day's top decile is
  held out from offers, and B is computed on that group only. Without it, a successful
  campaign reads as a failing model.
- **Enough customers.** With about 1,000 scored per day and 100 in the top decile, 10 held
  out per day gives 300 in a 30-day cohort: a 5-point shift on a 27% base is about 2
  standard errors. B is read as a rolling 30-day cohort, not day by day.

Signal A triggers a look; signal B triggers the retrain; signal C is the grade.

---

## 2. Service level objectives (Task 4)

Window: rolling 30 days (43,200 minutes) for the request SLOs, rolling 13 weeks for the
weekly ones. Request budgets are given both as a rate and as events per month at an assumed
production volume of **300,000 requests per month** (the roughly 10,000-customer base
scored once a day).

| SLO | Target | SLI (good / total) | Error budget | Deployment freeze trigger |
|---|---|---|---|---|
| **Availability** | 99.5% | Requests answered without a 5XX / all requests (`Invocations` minus `Invocation5XXErrors`, over `Invocations`, namespace `AWS/SageMaker`) | 0.5% of requests = **1,500 failed requests per month**, or **216 minutes** of full outage per 30 days | **Freeze** all deployments except reliability fixes when 75% of the budget is spent (1,125 failed requests or 162 outage-minutes in the rolling 30 days), **or** when one incident alone spends 25% (54 minutes). Lifts when rolling consumption is back under 50%. |
| **Latency (p95)** | `ModelLatency` <= 20000 us (20 ms) for 95% of requests | Requests completing in <= 20000 us / all requests | 5% of requests = **15,000 slow requests per month** (50 per 1,000) | **Freeze** when more than 7,500 slow requests (50% of budget) are counted before day 15 of the window, **or** when champion hourly p95 exceeds 20000 for 3 consecutive hours. During a freeze only a latency fix or a rollback may deploy. |
| **Prediction quality** | Recall@10% >= 0.25 on the weekly sample | Weekly samples with Recall@10% >= 0.25 / weekly samples (each sample: at least 1,000 customers whose 90-day label matured that week) | **1 failing week per rolling 13 weeks** (12 of 13 good, 92.3%) | **Freeze** feature, pipeline and model-config changes after the first failing week (budget spent); only a retrain on fresh data may deploy. **Roll back** to the previous approved package if two consecutive weeks fail or any week is below 0.20. |
| **Fairness** | Recall gap between the best- and worst-served loyalty tier <= 10 pp | Weeks with gap <= 10 pp / weeks (tiers with fewer than 100 rows or 10 positives excluded from that week) | **1 week over the gap per rolling 13 weeks** | **Block** any candidate whose worst-tier recall is more than 5 pp below the champion's for that tier (Lab 4 gate C4), and **freeze** all non-fairness model changes while the gap has been over 10 pp for 2 consecutive weeks. |

### Notes on each row

**Availability.** 216 minutes is 0.5% of 43,200. The request form is the one that is
actually measured, because an endpoint with no traffic at 3 a.m. is not "up" in any way a
caller cares about. The P1 alarm (`northstar-p1-endpoint-5xx`, >= 5 errors a minute for 2
minutes) is the pager; this row is the monthly account. Rollback with
`deployment/configs/rollback-action.sh` is a weight change on a serving endpoint (the Lab 5
run logged zero 4XX and 5XX across its 60-minute window, rollback included), so rollbacks
are exempt from the freeze.

**Latency.** The SLO (20 ms) and the alerts (200 ms P2, 500 ms P1) are different numbers
with different jobs: the SLO is a promise spent down over a month, the alerts are when a
human is interrupted. Steady state is far inside the target: *measured* p95 5,310 us on
champion and 7,853 us on canary over 802 calls in Lab 5, about a quarter of the budget
line. What spends this budget is **our own deployments**: the first invocation on a new
instance runs about 24,000 us, over the 20000 line. Every deploy, weight shift to a cold
variant or scale-out event buys a few slow requests, which is exactly why a latency budget
that is half gone by mid-window stops deployments.

**Prediction quality.** The target equals the Lab 4 promotion gate (`recall_top10 >= 0.25`),
so the model does not launch in breach: v4 *measured* 0.3318 on 3,000 validation customers.
With about 22% positives, contacting only the top 10% caps achievable recall near 0.45.
The sample-size floor matters: at 1,000 customers (about 220 churners) the standard error
of recall is roughly 0.03, so 0.25 sits about 2.6 standard errors under our measured
value and a failing week is unlikely to be noise. This SLI lags reality by 90 days (the
label has to mature), which is why section 1.3 exists.

**Fairness.** This SLO is **in breach today**, and the document says so instead of
picking a definition that hides it. v4 *measured* per-tier recall at the global top-10%
cutoff: Bronze 0.446, Silver 0.212, Platinum 0.143, Gold 0.135. The gap is **31 pp**.
The cause is the cutoff, not the ranking (per-tier AUC is 0.72 to 0.89): Bronze customers
churn at 34% against 11% for Gold, so one global threshold gives Bronze most of the contact
slots (`docs/lab3-evaluation-report.md` 1.4). The budget is therefore already spent, the
freeze clause is already in force, and the only model change that may ship is one that
narrows the gap: the per-tier contact budget proposed in Lab 3, which is a change to how
scores are consumed and needs no retraining. Until that ships, Lab 4's rule holds the line:
the gap is flagged at approval and no candidate may make any tier worse by more than 5 pp.
Platinum has 21 positives in validation (one churner is 4.8 points of recall), so its
weekly figure is read over a 4-week pool.

---

## 3. Runbooks (Task 5)

Both runbooks assume the on-call engineer has the repo checked out, the `cs401r-lab`
profile, and `NorthStar-AI-Platform` open in CloudWatch. Commands run from the repo root in
Git Bash; `export MSYS_NO_PATHCONV=1` first, or arguments starting with `/aws/` are
rewritten into Windows paths and return nothing.

## Runbook: Data Drift Detected (PSI > 0.2 on `days_since_last_purchase`)

### Detection

- **Alert:** `northstar-p3-feature-drift` goes to ALARM (`DriftDetected` Maximum >= 1 in a
  5-minute period, namespace `NorthStar/ChurnModel`), notifying through
  `northstar-p3-feature-drift-notify` to topic `northstar-alerts-p3` (Slack and a ticket).
  The specific condition for this runbook is `FeaturePSI` with dimension
  `Feature=days_since_last_purchase` above **0.2**.
- **Secondary signals that confirm it:**
  - `FeaturePSI` for `purchase_frequency_30d` is also elevated. Recency and 30-day
    frequency move together when customer behaviour really changes; recency moving alone
    points at the pipeline.
  - `ChurnAlertVolumeRatio` has left the 0.7 to 1.3 band (the model's output is moving, not
    only its input).
  - `drift_violations.json` shows `captured_records >= 500` and
    `underpowered_window: false`. If not, this is a thin sample, not drift.
  - Dashboard row 2: a Glue run failed or ran at an unusual time shortly before.

### Triage (< 5 minutes)

- **Step 1: confirm the run is valid.** Read the latest violations file:
  `aws s3 cp s3://northstar-dev-data-<account>/monitoring/reports/<job-name>/drift_violations.json -`
  Check `captured_records`, `underpowered_window`, and which features are listed. If
  underpowered, close the ticket as "insufficient sample" and stop.
- **Step 2: pipeline bug or real behaviour?** List the last feature runs:
  `aws glue get-job-runs --job-name northstar-dev-feature-engineer --query 'JobRuns[:3].[StartedOn,JobRunState,ExecutionTime]' --output text`
  A failed or missing run means `days_since_last_purchase` is growing by one per day for
  everyone because features are stale: that is a pipeline incident (Option C). Then check
  the calendar: a promotion, holiday or catalog change in the last 14 days explains a real
  shift.
- **Step 3: size it.** Read the PSI value and the direction of the shift in
  `drift_report.html` (same S3 prefix). PSI 0.2 to 0.5 is moderate, above 0.5 severe. If
  the only violations are KS features with no PSI violation, wait for the next run: act
  only if the same KS feature is flagged twice in a row.
- **Step 4: is the business outcome moving?**
  `aws cloudwatch get-metric-statistics --namespace NorthStar/ChurnModel --metric-name ChurnAlertVolumeRatio --dimensions Name=EndpointName,Value=northstar-churn-prod --start-time <24h ago> --end-time <now> --period 3600 --statistics Minimum Maximum`

### Containment Options

- **Option A: keep serving, mark the scores as under review.** The endpoint is untouched;
  the offer generator keeps using model scores, and the ticket records the drift and its
  cause. **Choose when** PSI is between 0.2 and 0.5, the cause is a known seasonal event,
  and `ChurnAlertVolumeRatio` is inside 0.7 to 1.3. The model still ranks customers; the
  population has shifted in a way we expected.
- **Option B: graceful degradation to the rule-based score.** The offer generator stops
  reading the endpoint's score and reads `churn_risk_score` from the feature table instead
  (the recency and frequency rule in `glue-scripts/feature_engineer.py`), with the daily
  offer count capped at the trailing 7-day average. The system keeps sending offers, on a
  worse but drift-proof ranking: the rule's *measured* AUC is 0.7208 against the model's
  0.7848, and it has no training distribution to drift from. **Choose when** PSI is above
  0.5, or `ChurnAlertVolumeRatio` is outside 0.7 to 1.3, or the cause is unknown. This is
  what the system does while a retrain is not yet ready.
- **Option C: hold offers and repair the pipeline.** If step 2 found a failed or stale
  feature run, neither the model nor the rule can be trusted, because both read the same
  stale features. Pause offer generation, serve nothing new (the last good day's offers
  stand, for at most 7 days), re-run `bash scripts/run-lab2-pipeline.sh`, then re-run the
  drift job. **Choose when** the drift is our own data being wrong, not the customers
  changing.

Retraining is the resolution, not a containment step: it takes hours and changes what is
in production. When it starts, the pipeline publishes `RetrainingInProgress = 1`, which
suppresses further drift notifications (suppression rule 1).

### Escalation

- **Trigger for escalating beyond on-call:** any one of: PSI above 0.5 on
  `days_since_last_purchase`; three or more of the five monitored features in violation;
  `northstar-p3-churn-alert-volume-drop` in ALARM at the same time; or the alarm still in
  ALARM after two further daily runs.
- **Who is paged:** the ML Lead (owns the retrain and approval decision). If step 2 points
  at the pipeline, the Data Engineering on-call instead. The Retention Marketing manager is
  informed, not paged, whenever Option B or C changes which offers go out.
- **SLA for response:** 60 minutes during business hours for the ML Lead; 15 minutes for
  Data Engineering on-call when offers are on hold (Option C), because that is revenue
  stopped.

### Resolution Verification

- **Confirmed resolved when** alarm `northstar-p3-feature-drift` is in state **OK** and
  `DriftDetected` = 0 on **two consecutive** drift runs, each with
  `captured_records >= 500`.
- **Metric that must return to range:** `FeaturePSI` for
  `Feature=days_since_last_purchase` at or below **0.2** (below 0.1 if the fix was a
  retrain with a rebuilt baseline, since the new baseline should match live traffic), and
  `ChurnAlertVolumeRatio` between 0.7 and 1.3.
- If a retrained model was promoted: `monitoring/baseline/baseline.csv` was regenerated
  from the new training set and re-uploaded before the verifying runs, and
  `northstar-suppress-retraining-window` is back to OK. A drift run against the old
  baseline proves nothing.
- If Option B was used: the offer generator is confirmed back on endpoint scores.

### Post-Incident Actions

- **Within 24 hours:** write the cause, the PSI values and the containment option chosen
  into the ticket; record the drift run in the `northstar-drift` MLflow experiment; if the
  cause was seasonal, add the date range to the promotion calendar so the next one is
  expected; if offers were degraded or held, send Retention Marketing the affected dates
  and counts.
- **Postmortem required:** **yes** if Option B or C was used for more than 24 hours, if
  the cause was a pipeline defect, or if the matured labels later show Recall@10% below
  0.25 for the affected period. **No** for seasonal drift handled with Option A; the
  ticket is the record.

## Runbook: Inference Latency Spike (p95 > 500 ms for > 5 minutes)

### Detection

- **Alert:** `northstar-p1-latency-p95-500ms` goes to ALARM: `ModelLatency` p95 on variant
  `champion` above **500000 us** (500 ms) for 5 of 5 one-minute periods, namespace
  `AWS/SageMaker`. Notifies `northstar-alerts-p1`: the on-call ML engineer is paged and the
  engineering manager is notified.
- **Secondary signals that confirm it:**
  - `northstar-p2-latency-p95-200ms` has been in ALARM for some minutes already (it is the
    early warning for this one). A P1 with the P2 still OK is suspect: check the metric
    before acting.
  - `CPUUtilization` on `/aws/sagemaker/Endpoints` near or above 80 per vCPU
    (`northstar-p2-cpu-high`), meaning saturation.
  - `OverheadLatency` (`AWS/SageMaker`) is also high. Model time high with overhead normal
    means the container; both high means the platform or the network.
  - `Invocations` well above normal, or `Invocation5XXErrors` above zero
    (`northstar-p1-endpoint-5xx`): timeouts have started.
  - Healthy reference: p95 *measured* 5,310 us on champion, 7,853 us on canary. 500000 is
    roughly 100 times normal; this is never noise.

### Triage (< 5 minutes)

- **Step 1: which variant?**
  `for v in champion canary; do aws cloudwatch get-metric-statistics --namespace AWS/SageMaker --metric-name ModelLatency --dimensions Name=EndpointName,Value=northstar-churn-prod Name=VariantName,Value=$v --start-time <15 min ago> --end-time <now> --period 60 --extended-statistics p95 --query 'Datapoints[].[Timestamp,ExtendedStatistics.p95]' --output text; done`
  Canary only: go straight to Option A.
- **Step 2: did we change something?**
  `aws sagemaker describe-endpoint --endpoint-name northstar-churn-prod --query '{Status:EndpointStatus,Modified:LastModifiedTime,Variants:ProductionVariants[].[VariantName,CurrentWeight,CurrentInstanceCount]}'`
  A `LastModifiedTime` inside the last 30 minutes means a deploy or weight shift is the
  first suspect.
- **Step 3: load or fault?** Compare `Invocations` (Sum, 60 s) with the same hour
  yesterday, and read `CPUUtilization` on dashboard row 1. Traffic up and CPU high is
  load (Option B). Traffic normal and CPU low with latency high is a fault in the
  container or instance (Option A if recent deploy, otherwise Option C and escalate).
- **Step 4: are callers failing yet?** Check `Invocation5XXErrors` Sum for the last 5
  minutes. Above zero means the availability budget is burning as well and containment
  cannot wait for a diagnosis.

### Containment Options

- **Option A: roll back to the champion.** `bash deployment/configs/rollback-action.sh`
  sets weights to champion 1, canary 0. It takes about 90 seconds, the endpoint keeps
  serving throughout and no request is dropped (*measured* in Lab 5: 100 of 100 calls on
  champion afterwards). **Choose when** step 1 shows the canary is the slow variant, or
  step 2 shows a change in the last 30 minutes. It is also the default when the cause is
  not known within 5 minutes, because it is free of side effects.
- **Option B: add capacity.** Champion autoscaling (`deployment/configs/scaling-policy.json`,
  target 1,000 invocations per instance, maximum 2) should already be scaling out; if it
  has not, force it:
  `aws sagemaker update-endpoint-weights-and-capacities --endpoint-name northstar-churn-prod --desired-weights-and-capacities VariantName=champion,DesiredInstanceCount=2`
  A new instance takes several minutes to come into service and its first request is a
  cold start. **Choose when** both variants are slow, CPU is above 80 and invocations are
  above normal: the model is healthy and simply out of room.
- **Option C: graceful degradation, serve the last known score.** The offer generator's
  call to the endpoint has a 1-second timeout; on timeout or 5XX it uses the customer's
  most recent stored score (yesterday's batch output), and if there is none, the
  rule-based `churn_risk_score` from the feature table. Offers keep flowing on scores up
  to 24 hours old, which costs almost nothing for a 90-day churn prediction, and load on
  the endpoint falls because slow calls are abandoned instead of retried. **Choose when**
  A and B have not brought p95 under 500000 within 10 minutes, or 5XX errors are rising.
  This is what the system does when the endpoint cannot recover immediately; it holds for
  up to 24 hours, after which scores are stale enough that offers are paused.

### Escalation

- **Trigger for escalating beyond on-call:** p95 still above 500000 **15 minutes** after
  the first containment action; or `northstar-p1-endpoint-5xx` in ALARM at the same time;
  or the page unacknowledged for 5 minutes (it re-pages, then goes to the secondary at 15).
- **Who is paged:** the secondary on-call ML engineer and the ML Platform lead (owns the
  endpoint configuration and the decision to open an AWS Support case). The engineering
  manager was already notified by the P1 and becomes incident commander if the outage
  passes 30 minutes.
- **SLA for response:** acknowledge within **5 minutes**; first containment action within
  **10 minutes** of the page; secondary on-call responds within 15 minutes of escalation.

### Resolution Verification

- **Confirmed resolved when** alarm `northstar-p1-latency-p95-500ms` **and** alarm
  `northstar-p2-latency-p95-200ms` are both in state **OK**, and `Invocation5XXErrors` Sum
  is **0** for 15 consecutive minutes.
- **Metric that must return to range:** `ModelLatency` p95 on the champion variant below
  **20000 us** (back inside the SLO, not merely under the alert line) for 15 consecutive
  one-minute periods. Normal is about 5,000 to 8,000 us; a p95 that settles at 150000 has
  cleared both alarms and is still 20 times worse than healthy, so the incident is not
  closed.
- If Option C was used: the offer generator's fallback counter is back to zero, so callers
  are confirmed on live scores again.
- If Option A was used: `describe-endpoint` shows champion weight 1, canary weight 0, and
  the canary package is set back to `Rejected` until the cause is found.

### Post-Incident Actions

- **Within 24 hours:** count the requests over 20000 us and any 5XX during the incident
  and subtract them from the latency and availability error budgets (section 2); if either
  freeze trigger is now met, announce the freeze. Save the dashboard time range and the
  alarm history (`aws cloudwatch describe-alarm-history --alarm-name northstar-p1-latency-p95-500ms`)
  to the ticket. If a canary caused it, attach its latency curve to the model package so
  the next approval sees it.
- **Postmortem required:** **yes, always** for this runbook. It is a P1, and it fires only
  after five minutes at 100 times normal latency, so every occurrence has spent real error
  budget. The postmortem must state the minutes above 500000, the budget consumed, which
  containment option worked, and one change that would have caught it at the P2 stage.

---

## 4. Measured in the Lab 6 sitting

Sitting of 2026-11-22 (endpoint InService 19:42 UTC, deleted 20:05 UTC). Sources:
`monitoring/reports/drift_violations.json`, `monitoring/reports/traffic_summary.json` and
`docs/lab6-monitoring-output.txt`.

| Item | Value |
|---|---|
| Drift job name, status, instance | `nsc-evidently-1795377206`, Completed, `ml.t3.medium`, 124 s billable, role `northstar-dev-ModelMonitorExecution` |
| Captured champion records after de-duplication | 718 (of 800 requests; 82 went to canary), `underpowered_window: false` |
| Baseline rows / columns | 6,999 / 11 |
| `days_since_last_purchase` PSI (threshold 0.2) | 0.0171, no drift |
| `purchase_frequency_30d` PSI (threshold 0.2) | 0.0079, no drift |
| `avg_order_value` PSI (threshold 0.2) | 0.0325, no drift |
| `category_diversity_score` KS p-value (threshold 0.05) | 0.6103, no drift |
| `total_spend_90d` KS p-value (threshold 0.05) | 0.8918, no drift |
| `DriftDetected` / violation count | 0 / 0 |
| `ChurnAlertVolume` (scores >= 0.5) / requests sent | 289 / 800, zero request errors |
| `ModelLatency` p50 / p95 / p99, champion (us) | 3,181 / 5,030 / 6,959 |
| Alarm states at capture | All eight alerts `OK`, plus `northstar-churn-rollback-latency` `OK` |
| Glue runs | transform SUCCEEDED (101 s); feature-engineer SUCCEEDED twice (216 s, 194 s), the second an overwrite re-run |

The traffic sent in the sitting is the validation split of the same feature snapshot the
baseline came from, so the expected result is **no drift**, and that is what the job
reported: every PSI is under 0.04 against a 0.2 threshold and both KS p-values are far
above 0.05. A clean run that reports clean is the test passing. The p95 of 5,030 us sits
where Lab 5 measured it (5,310 us), a quarter of the 20000 us SLO line.
