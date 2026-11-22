# NorthStar alert architecture (Lab 6, Task 3)

This is the alert specification for the churn model and the offer-generation system that
consumes its scores. `main.tf` in this directory implements it as CloudWatch alarms, one
EventBridge rule and four SNS topics. Values marked *measured* come from our own endpoint
(Lab 5 run of 2026-11-13 and the Lab 6 sitting); see `docs/lab6-monitoring-output.txt`
section 10 for each alarm's threshold and live state.

## 1. Severity tiers and escalation

A tier is defined by who is being hurt and how fast, not by which metric moved.

| Tier | Meaning | Escalation action | Acknowledge within | SNS topic |
|---|---|---|---|---|
| **P1** | Customers or downstream systems are being failed **now** | Wake the on-call ML engineer (phone page) **and** page the engineering manager | 5 min | `northstar-alerts-p1` |
| **P2** | Service is degraded and will become a P1 if left | Page the on-call ML engineer | 15 min | `northstar-alerts-p2` |
| **P3** | Something is wrong, nobody is being failed yet | Post to `#northstar-ml-alerts` in Slack **and** open a ticket in the ML platform queue | Next business day | `northstar-alerts-p3` |
| **P4** | Hygiene: fix it this week | Open a ticket only, no notification | 5 business days | `northstar-alerts-p4` |

The topic is the escalation path: what is subscribed to it decides who is woken. A lab
account has no pager, so the topics are created without subscriptions. In production P1
and P2 would carry the paging integration's HTTPS endpoint, P3 a Slack webhook (through
AWS Chatbot) plus the ticketing system's email address, and P4 the ticketing address
alone. An unacknowledged P1 re-pages every 5 minutes and goes to the secondary on-call at
15 minutes; an unacknowledged P2 becomes a P1 page at 30 minutes.

## 2. The alerts

Eight alerts, two per layer where the layer can hurt a customer directly.

| # | Alarm | Tier | Layer | Metric (namespace) | Condition | Why this tier |
|---|---|---|---|---|---|---|
| 1 | `northstar-p1-endpoint-5xx` | P1 | Application | `Invocation5XXErrors` (`AWS/SageMaker`) | Sum >= 5 per minute, 2 of 2 minutes | Predictions are failing; the offer generator has no score to act on |
| 2 | `northstar-p1-latency-p95-500ms` | P1 | Application | `ModelLatency` p95 (`AWS/SageMaker`) | > **500000 us** (500 ms), 5 of 5 minutes | 25x the 20 ms SLO for five minutes: callers time out |
| 3 | `northstar-p2-latency-p95-200ms` | P2 | Application | `ModelLatency` p95 (`AWS/SageMaker`) | > **200000 us** (200 ms), 3 of 3 minutes | Early warning for #2; still serving, but 40x steady state |
| 4 | `northstar-p2-cpu-high` | P2 | Infrastructure | `CPUUtilization` (`/aws/sagemaker/Endpoints`) | Average > 80, 5 of 5 minutes | Saturation comes before latency; time to scale or shed |
| 5 | `northstar-p3-feature-drift` (notifies through `northstar-p3-feature-drift-notify`) | P3 | Model | `DriftDetected` (`NorthStar/ChurnModel`) | Maximum >= 1 in 5 minutes | Scores are still served and may still be right; needs a human judgement, not a page |
| 6 | `northstar-p3-glue-job-failed` | P3 | Pipeline | `TriggeredRules` (`AWS/Events`) for rule `northstar-glue-job-failed` | Sum >= 1 in 5 minutes | Features go stale, but the endpoint keeps serving yesterday's |
| 7 | `northstar-p3-churn-alert-volume-drop` | P3 | Business | `ChurnAlertVolumeRatio` (`NorthStar/ChurnModel`) | Minimum < 0.7 (a drop of more than 30% against the trailing 7-day average) | The model can go quiet without a single error; only the business count shows it |
| 8 | `northstar-p4-drift-job-stale` | P4 | Model (meta) | `DriftDetected` sample count (`NorthStar/ChurnModel`) | No datapoint in 24 h (missing data **is** the breach) | Monitoring the monitor: no drift data is not the same as no drift |

Lab 5's `northstar-churn-rollback-latency` (canary p95 > 200000 us, 3 of 3) still exists
alongside these. It is a deployment guard with an automatic action
(`deployment/configs/rollback-action.sh`), not part of the paging set.

Distribution: 2 x P1, 2 x P2, 3 x P3, 1 x P4. Only failures a customer can feel page a
human. Drift, pipeline failure and volume drop are real problems with hours of slack, so
they go to a channel and a ticket; paging for them is how on-call learns to ignore pages.

### Notes on the thresholds

- **ModelLatency is in microseconds.** 200 ms is `200000`, 500 ms is `500000`. A threshold
  of `200` would mean 0.2 ms, below our healthy p95 (*measured* 5,310 us on champion in
  Lab 5), and the alarm would sit in ALARM permanently.
- **No latency alarm uses one evaluation period.** The first invocation after a deploy
  runs about 6x slower (roughly 24,000 us). That is far below 200000, but the principle
  holds for any tighter threshold: a single-period alarm fires on our own deployment.
- **The alert thresholds are not the SLO.** The latency SLO is 20 ms (`20000`), a promise
  measured over a month and spent as an error budget (`docs/lab6-runbook.md`). 200 ms and
  500 ms are the points at which a human is interrupted.
- **CPUUtilization is per vCPU summed**, so an `ml.m5.large` (2 vCPU) tops out at 200.
  The handout's 80 is kept; it means 40% of the instance and is deliberately early.
- **Pipeline failures use the job state-change event, not Glue task metrics.** A Glue run
  whose driver dies (for example on an S3 `AccessDenied`) reports `numFailedTasks = 0`.
  The EventBridge rule matches `FAILED`, `ERROR` and `TIMEOUT` for both pipeline jobs.
- **Missing data is "not breaching" everywhere except alarm 8.** An endpoint with no
  traffic has no latency, and that is not an incident. But a drift metric that stops
  arriving is exactly the failure alarm 8 exists to catch.
- **No anomaly-detection bands.** They create a detector that survives
  `terraform destroy`. The volume alarm gets its 7-day comparison from the publisher
  instead (`monitoring/publish_metrics.py` computes the ratio).

## 3. Suppression rules

### Rule 1: no drift notifications during a scheduled retraining window (implemented)

- **Condition:** the retraining pipeline is running. It publishes
  `RetrainingInProgress = 1` to `NorthStar/ChurnModel` for its duration, which puts
  `northstar-suppress-retraining-window` into ALARM.
- **Suppresses:** notifications from `northstar-p3-feature-drift-notify` (alert 5), from
  60 seconds after the window opens until 15 minutes after it closes.
- **Does not suppress:** the underlying `northstar-p3-feature-drift` alarm state, which
  still shows on the dashboard, or any P1 / P2 alert.
- **Why:** retraining is the response to drift. While it runs, the drift job keeps
  comparing live traffic to the *old* baseline and will keep reporting the drift that
  triggered the retrain. Every one of those notifications is a ticket for work already in
  progress. The 15-minute extension covers the gap between the new model going live and
  the first drift run against the rebuilt baseline.
- **How:** a CloudWatch composite alarm with an `actions_suppressor`. The suppression is
  infrastructure, so it cannot be forgotten on a busy day.

### Rule 2: latency alerts are held for 10 minutes after a deployment (procedural)

- **Condition:** an `UpdateEndpoint` or `UpdateEndpointWeightsAndCapacities` call
  completed within the last 10 minutes (the deploy script records the time).
- **Suppresses:** `northstar-p2-latency-p95-200ms` (alert 3). **Never** alert 1 or 2.
- **Why:** cold start plus the canary shift produce a burst of slow first calls. One
  deploy would otherwise page on-call for the deploy they are watching. P1s stay live
  because a deploy that takes p95 past 500 ms for five minutes is a bad deploy.

### Rule 3: one page per cause during an endpoint outage (alert-storm rule)

- **Condition:** `northstar-p1-endpoint-5xx` is in ALARM.
- **Suppresses:** `northstar-p2-latency-p95-200ms`, `northstar-p2-cpu-high` and
  `northstar-p3-churn-alert-volume-drop` for the same endpoint until the P1 clears.
- **Why:** when the endpoint is failing, latency, CPU and business volume all move within
  minutes. That is one incident, and four pages for it slow the responder down. The P1
  already has the right person awake; the rest are symptoms they will see on the dashboard.

## 4. Who is allowed to do what: observer and executor

Two IAM roles sit behind this monitoring, and they are deliberately not interchangeable.

| Role | Kind | Can | Cannot |
|---|---|---|---|
| `northstar-dev-ModelMonitor` | **Observer** | Read metrics, alarms, processing-job status and `artifacts/`; put metrics and alarms | Write to S3, pull a container image, start a job, invoke or change the endpoint |
| `northstar-dev-ModelMonitorExecution` | **Executor** | Pull the scikit-learn image, read `datacapture/` and `monitoring/`, write under `monitoring/` | Write anywhere else in the bucket (`raw/`, `features/`, `artifacts/`), publish CloudWatch metrics, touch the endpoint |

The drift job runs as the executor because its whole purpose is to write a report, and it
cannot start without pulling its container. Handing it the observer role fails several
minutes into the job with an opaque `Failed` status. The executor in turn cannot overwrite
the data it is judging, and it cannot publish the metric that pages someone: the drift
metric is published from outside the job, by a principal that can be audited separately.
An identity that watches cannot act, and the identity that acts cannot grade its own work.

## 5. Operating this directory

```bash
terraform -chdir=monitoring/alerts init
terraform -chdir=monitoring/alerts apply       # after the endpoint is InService
terraform -chdir=monitoring/alerts destroy     # BEFORE scripts/teardown-lab6.sh
```

`scripts/teardown-lab6.sh` deletes alarms whose name contains `northstar`, but it does not
know about the EventBridge rule, the SNS topics or the composite alarm. `terraform destroy`
here removes all of them, which is why it runs first. State is local and gitignored; never
commit `terraform.tfstate`.
