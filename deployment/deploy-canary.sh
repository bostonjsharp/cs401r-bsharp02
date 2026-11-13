#!/usr/bin/env bash
# deploy-canary.sh
# Deploys the NorthStar churn model as a 9:1 canary on one real-time endpoint,
# then attaches auto-scaling and the rollback alarm. Everything it creates is
# defined by a file in deployment/configs/; this script only sequences the
# calls. It is the "deploy" step of docs/lab5-deployment-plan.md.
#
# Usage (from the repo root):
#   bash deployment/deploy-canary.sh <champion-package-arn> <canary-package-arn>
#
# The two ARNs are Model Registry packages in the group northstar-churn-models:
#   aws sagemaker list-model-packages --model-package-group-name northstar-churn-models \
#     --query 'ModelPackageSummaryList[].[ModelPackageArn,ModelApprovalStatus]' --output text
#
# BILLING: two ml.m5.large instances, $0.23/hour in total, from the moment the
# endpoint is InService until scripts/teardown-lab5.sh deletes it.
#
# The script refuses to start if
#   * either package is not Approved (approval is a human step, plan section 2)
#   * either package's model artifact is missing from S3
#   * the model names in endpoint-config-canary.json do not match the versions
#     of the two packages (endpoint configs are immutable; a mismatch would
#     deploy the wrong model and could not be corrected in place)
#
# Re-running: SageMaker models that already exist are reused. An existing
# endpoint config or endpoint is an error; run scripts/teardown-lab5.sh first.
set -euo pipefail

# Git Bash on Windows rewrites arguments that look like POSIX paths. Nothing
# here needs that, and ARNs and resource IDs contain slashes.
export MSYS_NO_PATHCONV=1

CONFIG_DIR="deployment/configs"
ENDPOINT="northstar-churn-prod"
TF_DIR="infrastructure/environments/dev"

if [ "$#" -ne 2 ]; then
  echo "usage: bash deployment/deploy-canary.sh <champion-package-arn> <canary-package-arn>" >&2
  exit 2
fi
CHAMPION_ARN="$1"
CANARY_ARN="$2"

for f in endpoint-config-canary.json scalable-target.json scaling-policy.json rollback-alarm.json; do
  [ -f "$CONFIG_DIR/$f" ] || { echo "missing $CONFIG_DIR/$f (run from the repo root)" >&2; exit 2; }
done

ENDPOINT_CONFIG=$(grep -o '"EndpointConfigName": *"[^"]*"' "$CONFIG_DIR/endpoint-config-canary.json" | cut -d'"' -f4)

# The endpoint runs as the Lab 1 MLEngineer role. ROLE_ARN overrides the
# Terraform lookup for anyone deploying without the state backend.
ROLE="${ROLE_ARN:-$(terraform -chdir="$TF_DIR" output -raw ml_engineer_role_arn)}"
echo "==> Execution role: $ROLE"

# -- Pre-flight (free, read-only) ---------------------------------------------
preflight () {  # $1 = variant name, $2 = package ARN; prints the model name
  local variant="$1" arn="$2" status data version model
  status=$(aws sagemaker describe-model-package --model-package-name "$arn" \
             --query ModelApprovalStatus --output text)
  if [ "$status" != "Approved" ]; then
    echo "REFUSING: $variant package is $status, not Approved: $arn" >&2
    exit 1
  fi
  data=$(aws sagemaker describe-model-package --model-package-name "$arn" \
           --query 'InferenceSpecification.Containers[0].ModelDataUrl' --output text)
  if ! aws s3 ls "$data" >/dev/null 2>&1; then
    echo "REFUSING: $variant model artifact does not exist: $data" >&2
    exit 1
  fi
  version="${arn##*/}"
  model="northstar-churn-v${version}"
  if ! grep -q "\"ModelName\": *\"$model\"" "$CONFIG_DIR/endpoint-config-canary.json"; then
    echo "REFUSING: $variant is package version $version, but $CONFIG_DIR/endpoint-config-canary.json" >&2
    echo "          has no variant with ModelName $model. Edit the JSON, commit, re-run." >&2
    exit 1
  fi
  echo "$model"
}

echo "==> Pre-flight: approval status, artifacts, config/version match"
CHAMPION_MODEL=$(preflight champion "$CHAMPION_ARN")
CANARY_MODEL=$(preflight canary "$CANARY_ARN")
echo "    champion: $CHAMPION_MODEL  <- $CHAMPION_ARN"
echo "    canary:   $CANARY_MODEL  <- $CANARY_ARN"

# -- Models -------------------------------------------------------------------
create_model () {  # $1 = model name, $2 = package ARN
  if aws sagemaker describe-model --model-name "$1" >/dev/null 2>&1; then
    echo "    model $1 already exists, reusing"
  else
    aws sagemaker create-model --model-name "$1" --execution-role-arn "$ROLE" \
      --containers "ModelPackageName=$2" --query ModelArn --output text
  fi
}
echo "==> Creating SageMaker models from the approved packages"
create_model "$CHAMPION_MODEL" "$CHAMPION_ARN"
create_model "$CANARY_MODEL" "$CANARY_ARN"

# -- Endpoint -----------------------------------------------------------------
echo "==> Creating endpoint config $ENDPOINT_CONFIG (two variants 9:1, data capture on)"
aws sagemaker create-endpoint-config \
  --cli-input-json "file://$CONFIG_DIR/endpoint-config-canary.json" \
  --query EndpointConfigArn --output text

echo "==> Creating endpoint $ENDPOINT (about 7 minutes; billing starts at InService)"
aws sagemaker create-endpoint --endpoint-name "$ENDPOINT" \
  --endpoint-config-name "$ENDPOINT_CONFIG" --query EndpointArn --output text
aws sagemaker wait endpoint-in-service --endpoint-name "$ENDPOINT"
echo "    InService at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# -- Auto-scaling and rollback alarm --------------------------------------------
echo "==> Registering the scalable target and the target-tracking policy"
aws application-autoscaling register-scalable-target \
  --cli-input-json "file://$CONFIG_DIR/scalable-target.json" >/dev/null
aws application-autoscaling put-scaling-policy \
  --cli-input-json "file://$CONFIG_DIR/scaling-policy.json" \
  --query PolicyARN --output text

echo "==> Creating the rollback alarm"
aws cloudwatch put-metric-alarm --cli-input-json "file://$CONFIG_DIR/rollback-alarm.json"

echo ""
aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
  --query 'ProductionVariants[].[VariantName,CurrentWeight,CurrentInstanceCount]' --output text
echo ""
echo "==> Deployed. $ENDPOINT is billing \$0.23/hour until scripts/teardown-lab5.sh runs."
echo "    Rollback: bash deployment/configs/rollback-action.sh"
