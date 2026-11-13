# Lab 5: Churn Model Deployment Plan

Runbook for releasing a new churn model. Run every command from the repo root in Git Bash,
`us-east-1`. Names: the **champion** is the Approved package now serving; the **canary** is
the newer Approved package under test; both live in Model Registry group
`northstar-churn-models` and serve from endpoint `northstar-churn-prod`.

## 1. Deployment strategy and rationale

**Real-time endpoint, canary rollout (9:1).** NorthStar scores every active customer weekly,
due Monday 6 AM ET (`northstar-scenario-overview.md`). That cadence alone suits Batch
Transform. We serve from an endpoint because the same score is also read per customer by
offer generation, which must answer in 2 seconds, and the weekly job can call the same
endpoint, so one model answers both. Batch has no traffic weights, so it cannot expose 10%
of customers first. Canary over blue/green: a cutover puts a whole week's campaign on an
unproven model; a canary limits a bad model to a tenth of it.

## 2. Pre-deployment checklist

1. Data platform is up and features are under 48 hours old:
   `terraform -chdir=infrastructure/environments/dev apply`, then `bash scripts/run-lab2-pipeline.sh`.
2. The CI build that registered the canary is green, including `Running security scan`.
3. Both packages are listed:
   `aws sagemaker list-model-packages --model-package-group-name northstar-churn-models --query 'ModelPackageSummaryList[].[ModelPackageArn,ModelApprovalStatus]' --output text`
4. **Approval, by the ML Lead** (never the engineer who trained it). The ML Lead reads the
   package's `evaluation_metrics.json` (its S3 location:
   `aws sagemaker describe-model-package --model-package-name <arn> --query ModelMetrics.ModelStatistics.S3Uri`)
   and approves only if `precision_top10 >= 0.50`,
   `recall_top10 >= 0.25`, `lift_ci_low > 0`, canary `auc_roc` is within 0.02 of the
   champion's, and the fairness flag is reviewed:
   `aws sagemaker update-model-package --model-package-arn <arn> --model-approval-status Approved`
5. Endpoint quota is at least 2:
   `aws service-quotas get-service-quota --service-code sagemaker --quota-code L-614B09FD --query Quota.Value`
6. `bash scripts/check-secrets.sh` prints CLEAN.

Deploy: `bash deployment/deploy-canary.sh <champion-arn> <canary-arn>`. It refuses
unapproved packages, creates the endpoint from `deployment/configs/endpoint-config-canary.json`
(about 7 minutes), then attaches auto-scaling and the rollback alarm.

## 3. Rollback criteria

- **Metric:** `ModelLatency`, statistic **p95**, variant `canary`, namespace `AWS/SageMaker`.
- **Threshold:** greater than **200000 microseconds (200 ms)** for **3 consecutive 60-second
  periods**. Healthy is about 4,100 microseconds.
- **As code:** `deployment/configs/rollback-alarm.json`, alarm `northstar-churn-rollback-latency`.
- **Who decides:** the on-call ML Engineer rolls back the moment the alarm reads `ALARM`; no
  approval is needed. The ML Lead may also order it on any canary `Invocation5XXErrors`.
- **Action:** `bash deployment/configs/rollback-action.sh`. Canary weight goes to 0 in about
  90 seconds with no dropped requests. The canary instance keeps billing.

## 4. Monitoring window

Production: **48 hours** at 9:1 before promotion. This lab: **60 minutes**.

Watch the alarm state, p95 latency per variant, 4XX and 5XX counts, the observed split, and
new `.jsonl` files under `s3://northstar-dev-data-829485866627/datacapture/`.

**Clean** means: the alarm never left `OK`, zero 5XX on either variant, and about 10% of
calls answered by the canary. Then promote:
`aws sagemaker update-endpoint-weights-and-capacities --endpoint-name northstar-churn-prod --desired-weights-and-capacities VariantName=champion,DesiredWeight=0 VariantName=canary,DesiredWeight=1`

**What 60 minutes cannot see:** the daily traffic cycle, the nightly feature refresh, the
Monday scoring burst that exercises auto-scaling, memory growth, and score drift between
variants. A real rollout would add per-variant score distributions from data capture and
retention-offer redemption.

Auto-scaling: target tracking on `SageMakerVariantInvocationsPerInstance` at 1000, cooldowns
60 s out and 600 s in (`deployment/configs/scaling-policy.json`). It requires `ml.m5.large`:
burstable `ml.t2` instances run on CPU credits, so their load is not a capacity signal.

## 5. Stakeholder notifications

| Role | Deployment start | Promotion | Rollback |
|---|---|---|---|
| ML Lead | Package versions, window end time | Promotion request with window metrics | Alarm state, time, action taken |
| On-call ML Engineer | This runbook, alarm name | Confirmation of new weights | Page from the alarm |
| VP of Technology | Change notice | Completion notice | Incident notice |
| VP of Marketing | Notice that 10% of scores come from a new model | Model version behind next campaign | Notice that scores revert to the champion |
| Chief Privacy Officer | Data capture is on, 7-day retention | None | Only if customer data was exposed |

## 6. Post-deployment review

Held within two business days of promotion or rollback. Attendees: ML Lead (chair), the
deploying ML Engineer, a Data Engineer, a Marketing analytics representative. It produces a
one-page deployment record committed under `docs/`: versions, window metrics, alarm history,
decision, follow-ups with owners.

## 7. Resource cleanup

After the window (in production, the retired variant only; in this lab, everything):

1. `bash scripts/teardown-lab5.sh` deletes, in order, scaling policies and targets, the
   rollback alarm, the endpoint, endpoint configs and models.
2. Verify: `docs/lab5-teardown-output.txt` shows six lines ending `OK`, and
   `aws sagemaker list-endpoints --query 'length(Endpoints)'` prints 0.
3. If the data platform goes too: `bash scripts/teardown-lab2.sh`, then set both packages to
   `Rejected`, because their artifacts no longer exist.

## Lab execution record

Filled in after the paid session (Phase 3); not yet run.

| Item | Value |
|---|---|
| Champion / canary package versions | TBD |
| Approved by (role), date, metrics reviewed | TBD |
| Observed split over 200 calls at 9:1 | TBD |
| p95 `ModelLatency`, champion / canary | TBD |
| Split over 100 calls after rollback | TBD |
