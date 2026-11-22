# ── monitoring/alerts ────────────────────────────────────────────────────────
# Lab 6 Task 3: the alert set for the NorthStar churn model, as code.
# The specification (tiers, escalation, suppression rules, rationale) is
# README.md in this directory; this file is its implementation.
#
# Separate root module with LOCAL state on purpose. These alarms watch an
# endpoint that exists for one sitting, so they are applied after the endpoint
# is InService and destroyed before it is deleted, without touching the
# platform stack in infrastructure/environments/dev. The state file is
# gitignored (*.tfstate) and must never be committed.
#
#   terraform -chdir=monitoring/alerts init
#   terraform -chdir=monitoring/alerts apply
#   terraform -chdir=monitoring/alerts destroy      # BEFORE scripts/teardown-lab6.sh
#
# Rules every alarm here follows:
#   * the name contains "northstar" (the capture block and teardown-lab6.sh
#     find alarms by that string; one without it is invisible to both);
#   * ModelLatency thresholds are MICROSECONDS (200 ms = 200000);
#   * latency alarms need >= 2 periods: the first call after a deploy is ~6x
#     slower (~24,000 us) and would trip a single-period alarm on our own deploy;
#   * no anomaly-detection bands: the detector they create survives
#     `terraform destroy`;
#   * descriptions are ASCII.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "northstar"
      Lab       = "6"
      ManagedBy = "terraform-monitoring-alerts"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "endpoint_name" {
  type    = string
  default = "northstar-churn-prod"
}

variable "variant_name" {
  description = "Variant the serving alarms watch. The canary has its own rollback alarm from Lab 5."
  type        = string
  default     = "champion"
}

variable "metric_namespace" {
  description = "Namespace monitoring/publish_metrics.py writes to"
  type        = string
  default     = "NorthStar/ChurnModel"
}

variable "glue_job_names" {
  type    = list(string)
  default = ["northstar-dev-transform", "northstar-dev-feature-engineer"]
}

locals {
  endpoint_dims = {
    EndpointName = var.endpoint_name
    VariantName  = var.variant_name
  }
  custom_dims = {
    EndpointName = var.endpoint_name
  }

  # One SNS topic per severity tier. The topic IS the escalation path: what
  # is subscribed to it decides who is woken. Subscriptions are not created
  # here (a lab account has no pager); README.md names what each tier gets.
  tiers = {
    p1 = "P1 - wake on-call and page the engineering manager"
    p2 = "P2 - page on-call"
    p3 = "P3 - Slack channel and a ticket"
    p4 = "P4 - ticket only"
  }
}

# ── Escalation targets ───────────────────────────────────────────────────────

resource "aws_sns_topic" "tier" {
  for_each     = local.tiers
  name         = "northstar-alerts-${each.key}"
  display_name = "NorthStar ${upper(each.key)}"
}

# EventBridge publishes Glue failures straight to the P3 topic, so that topic
# must allow the events service. CloudWatch alarms in the same account need no
# resource policy.
data "aws_iam_policy_document" "p3_topic" {
  statement {
    sid       = "EventBridgeGlueFailures"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.tier["p3"].arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "p3" {
  arn    = aws_sns_topic.tier["p3"].arn
  policy = data.aws_iam_policy_document.p3_topic.json
}

# ── P1: customers are being failed right now ─────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "p1_5xx" {
  alarm_name          = "northstar-p1-endpoint-5xx"
  alarm_description   = "P1. Champion variant returned 5 or more 5XX errors per minute for 2 consecutive minutes. Predictions are failing. Runbook: docs/lab6-runbook.md (latency spike / serving failure)."
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation5XXErrors"
  dimensions          = local.endpoint_dims
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p1"].arn]
  ok_actions          = [aws_sns_topic.tier["p1"].arn]
}

resource "aws_cloudwatch_metric_alarm" "p1_latency_500ms" {
  alarm_name          = "northstar-p1-latency-p95-500ms"
  alarm_description   = "P1. ModelLatency p95 on champion above 500000 microseconds (500 ms) for 5 consecutive minutes. Runbook B in docs/lab6-runbook.md."
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.endpoint_dims
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 500000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p1"].arn]
  ok_actions          = [aws_sns_topic.tier["p1"].arn]
}

# ── P2: degraded, on-call should look now ────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "p2_latency_200ms" {
  alarm_name          = "northstar-p2-latency-p95-200ms"
  alarm_description   = "P2. ModelLatency p95 on champion above 200000 microseconds (200 ms) for 3 consecutive minutes. Early warning for the 500 ms P1."
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.endpoint_dims
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 200000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p2"].arn]
}

resource "aws_cloudwatch_metric_alarm" "p2_cpu" {
  alarm_name          = "northstar-p2-cpu-high"
  alarm_description   = "P2. Endpoint instance CPUUtilization on champion above 80 percent for 5 consecutive minutes. Saturation precedes latency."
  namespace           = "/aws/sagemaker/Endpoints"
  metric_name         = "CPUUtilization"
  dimensions          = local.endpoint_dims
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p2"].arn]
}

# ── P3: something is wrong, nobody is being failed yet ───────────────────────

# The raw drift signal. It has NO actions of its own: notification goes
# through the composite alarm below so the retraining suppression applies.
resource "aws_cloudwatch_metric_alarm" "p3_drift" {
  alarm_name          = "northstar-p3-feature-drift"
  alarm_description   = "P3 signal. The latest Evidently drift run found at least one monitored feature over its threshold (PSI above 0.2 or KS p-value below 0.05). Notifies through northstar-p3-feature-drift-notify. Runbook A in docs/lab6-runbook.md."
  namespace           = var.metric_namespace
  metric_name         = "DriftDetected"
  dimensions          = local.custom_dims
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

# Suppression rule 1 (README.md). While a scheduled retraining run is in
# progress the model is about to be replaced and the baseline rebuilt, so a
# drift page is noise. The retraining pipeline publishes RetrainingInProgress
# = 1 for its duration; this alarm turns that into a suppressor.
resource "aws_cloudwatch_metric_alarm" "suppress_retraining" {
  alarm_name          = "northstar-suppress-retraining-window"
  alarm_description   = "Suppressor, not an alert. In ALARM while the scheduled retraining pipeline reports RetrainingInProgress = 1. Silences northstar-p3-feature-drift-notify."
  namespace           = var.metric_namespace
  metric_name         = "RetrainingInProgress"
  dimensions          = local.custom_dims
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_composite_alarm" "p3_drift_notify" {
  alarm_name        = "northstar-p3-feature-drift-notify"
  alarm_description = "P3. Feature drift, except during a retraining window. This is the alarm that notifies."
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.p3_drift.alarm_name}\")"
  alarm_actions     = [aws_sns_topic.tier["p3"].arn]

  actions_suppressor {
    alarm            = aws_cloudwatch_metric_alarm.suppress_retraining.alarm_name
    wait_period      = 60
    extension_period = 900
  }
}

# Pipeline layer. Glue task metrics report numFailedTasks = 0 when the DRIVER
# dies (for example on an S3 AccessDenied), so the alarm watches the job's
# state-change event instead.
resource "aws_cloudwatch_event_rule" "glue_failed" {
  name        = "northstar-glue-job-failed"
  description = "Glue job run ended FAILED, ERROR or TIMEOUT for a NorthStar pipeline job"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = var.glue_job_names
      state   = ["FAILED", "ERROR", "TIMEOUT"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_failed_to_p3" {
  rule      = aws_cloudwatch_event_rule.glue_failed.name
  target_id = "northstar-alerts-p3"
  arn       = aws_sns_topic.tier["p3"].arn
}

resource "aws_cloudwatch_metric_alarm" "p3_glue_failed" {
  alarm_name          = "northstar-p3-glue-job-failed"
  alarm_description   = "P3. A NorthStar Glue job ended FAILED, ERROR or TIMEOUT (EventBridge rule northstar-glue-job-failed matched). Features are going stale."
  namespace           = "AWS/Events"
  metric_name         = "TriggeredRules"
  dimensions          = { RuleName = aws_cloudwatch_event_rule.glue_failed.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p3"].arn]
}

resource "aws_cloudwatch_metric_alarm" "p3_volume_drop" {
  alarm_name          = "northstar-p3-churn-alert-volume-drop"
  alarm_description   = "P3. Churn alert volume fell more than 30 percent below its trailing 7-day average (ChurnAlertVolumeRatio below 0.7). The model may have gone quiet without erroring."
  namespace           = var.metric_namespace
  metric_name         = "ChurnAlertVolumeRatio"
  dimensions          = local.custom_dims
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0.7
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p3"].arn]
}

# ── P4: fix it this week ─────────────────────────────────────────────────────

# Monitoring the monitor. Every other drift alarm treats missing data as
# healthy, so a drift job that silently stops running would look like "no
# drift" forever. This is the one alarm where missing data is the breach.
resource "aws_cloudwatch_metric_alarm" "p4_drift_stale" {
  alarm_name          = "northstar-p4-drift-job-stale"
  alarm_description   = "P4. No DriftDetected datapoint in the last 24 hours: the drift job or the publisher has stopped. Absence of drift data is not absence of drift."
  namespace           = var.metric_namespace
  metric_name         = "DriftDetected"
  dimensions          = local.custom_dims
  statistic           = "SampleCount"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.tier["p4"].arn]
}

# ── Outputs ──────────────────────────────────────────────────────────────────

output "alarm_names" {
  value = sort([
    aws_cloudwatch_metric_alarm.p1_5xx.alarm_name,
    aws_cloudwatch_metric_alarm.p1_latency_500ms.alarm_name,
    aws_cloudwatch_metric_alarm.p2_latency_200ms.alarm_name,
    aws_cloudwatch_metric_alarm.p2_cpu.alarm_name,
    aws_cloudwatch_metric_alarm.p3_drift.alarm_name,
    aws_cloudwatch_metric_alarm.p3_glue_failed.alarm_name,
    aws_cloudwatch_metric_alarm.p3_volume_drop.alarm_name,
    aws_cloudwatch_metric_alarm.p4_drift_stale.alarm_name,
    aws_cloudwatch_metric_alarm.suppress_retraining.alarm_name,
    aws_cloudwatch_composite_alarm.p3_drift_notify.alarm_name,
  ])
}

output "escalation_topics" {
  value = { for k, t in aws_sns_topic.tier : k => t.arn }
}
