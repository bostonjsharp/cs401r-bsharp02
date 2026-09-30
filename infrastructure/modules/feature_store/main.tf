# ── modules/feature_store ────────────────────────────────────────────────────
# One SageMaker Feature Group: the contract between the feature engineering
# job (writer, as DataEngineer) and the training jobs of Labs 3-4 (readers,
# as MLEngineer). Only aws_sagemaker_feature_group lives here.
#
# Two stores, one group:
#   online  - low-latency key lookup by customer_id, for real-time inference
#   offline - Parquet in S3 (plus a Glue table), for training-set queries
# The offline store lags PutRecord by up to ~15 minutes; the online store is
# immediate, which is what scripts/verify-lab2.sh reads back.

locals {
  name_prefix        = "${var.project}-${var.environment}"
  feature_group_name = "${local.name_prefix}-${var.dataset}-features"
}

resource "aws_sagemaker_feature_group" "this" {
  feature_group_name             = local.feature_group_name
  description                    = "One labeled feature row per ${var.dataset}: RFM features from the observation window, churn_label from the outcome window"
  record_identifier_feature_name = var.record_identifier
  event_time_feature_name        = var.event_time_feature
  role_arn                       = var.execution_role_arn

  # event_time MUST be Fractional (epoch seconds). Declared String and fed a
  # number, or vice versa, PutRecord returns success and the record silently
  # never lands. The type lives in the variable default; the verify script
  # checks it, and churn_label being Integral, by name.
  dynamic "feature_definition" {
    for_each = var.feature_definitions
    content {
      feature_name = feature_definition.value.name
      feature_type = feature_definition.value.type
    }
  }

  online_store_config {
    enable_online_store = true
  }

  # features/offline-store/, not features/<dataset>/: Feature Store lays out
  # its own <account>/sagemaker/<region>/offline-store/ tree under this URI,
  # and the job's Parquet must not be mixed into it.
  offline_store_config {
    s3_storage_config {
      s3_uri = "s3://${var.bucket_name}/${var.offline_store_prefix}"
    }
  }

  tags = {
    Name = local.feature_group_name
  }
}
