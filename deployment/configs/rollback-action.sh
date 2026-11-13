#!/usr/bin/env bash
# rollback-action.sh
# The canary rollback: send 100% of traffic back to the champion variant.
#
# Run this when the alarm northstar-churn-rollback-latency
# (deployment/configs/rollback-alarm.json) goes to ALARM, or when the
# deployment plan's section 3 says to (docs/lab5-deployment-plan.md).
#
# What it does and does not do:
#   * Sets variant weights to champion 1 / canary 0. Takes about 90 seconds.
#     The endpoint reports "Updating" throughout and keeps serving; no request
#     is dropped.
#   * It does NOT stop the bill. The canary instance stays provisioned at
#     weight 0. Only deleting the endpoint (scripts/teardown-lab5.sh) does.
#   * It does not change instance counts, the scaling policy or the alarm.
#
# Usage (from the repo root): bash deployment/configs/rollback-action.sh
set -euo pipefail

ENDPOINT="${ENDPOINT_NAME:-northstar-churn-prod}"

echo "==> Rolling back $ENDPOINT: champion weight 1, canary weight 0"
aws sagemaker update-endpoint-weights-and-capacities \
  --endpoint-name "$ENDPOINT" \
  --desired-weights-and-capacities \
      VariantName=champion,DesiredWeight=1 \
      VariantName=canary,DesiredWeight=0 \
  --query EndpointArn --output text

echo "==> Waiting for InService (about 90 seconds)"
aws sagemaker wait endpoint-in-service --endpoint-name "$ENDPOINT"

echo "==> Weights now"
aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
  --query 'ProductionVariants[].[VariantName,CurrentWeight]' --output text

echo "==> Rolled back. The canary instance is still billing until the endpoint is deleted."
