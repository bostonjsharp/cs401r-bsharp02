#!/usr/bin/env bash
# capture-lab6.sh
# Writes docs/lab6-monitoring-output.txt, the evidence the TA grades Lab 6
# Task 1 from. It is the handout's "two windows" capture block, command for
# command, wrapped in a script so it runs the same way every time.
#
#   bash scripts/capture-lab6.sh 1    # everything live: creates the file (sections 1-11)
#   bash scripts/capture-lab6.sh 2    # after teardown: appends section 12
#
# Window 1 must run AFTER the drift job completed, the publisher ran, the
# alarms exist and the dashboard is saved, and BEFORE teardown. Once the
# endpoint is gone sections 1-11 cannot be recreated without redeploying.
#
# Two changes from the handout block, both for Git Bash on Windows:
#   * MSYS_NO_PATHCONV=1. Without it Git Bash rewrites the argument
#     "/aws/sagemaker/Endpoints" into a Windows path, the call asks for a
#     namespace that does not exist, and section 9 comes back EMPTY, which is
#     indistinguishable from a missing PutMetricData grant.
#   * the start time comes from GNU `date -d` instead of python3 (there is no
#     python3 on PATH here).
#
# After window 1: open the file and read it. A header with nothing under it,
# or an AccessDenied line, means that section captured nothing. Fix it and
# re-run while the endpoint is still up.
set -uo pipefail
export MSYS_NO_PATHCONV=1
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_PAGER=""

WINDOW="${1:-}"
OUT=docs/lab6-monitoring-output.txt
EP="${ENDPOINT_NAME:-northstar-churn-prod}"
ACCT=$(aws sts get-caller-identity --query Account --output text)
B=northstar-dev-data-$ACCT
NS=NorthStar/ChurnModel
S=$(date -u -d '12 hours ago' +%Y-%m-%dT%H:%M:%SZ)
E=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cap () { echo; echo "===== $1  ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ====="; }

[ -d docs ] || { echo "run from the repo root" >&2; exit 2; }
[ -n "$S" ] || { echo "could not compute the start time" >&2; exit 2; }

window1 () {
  echo "Lab 6 monitoring evidence - account $ACCT"
  cap "1. Endpoint, variants, data capture"
  aws sagemaker describe-endpoint --endpoint-name $EP \
    --query '{Status:EndpointStatus,Config:EndpointConfigName,Variants:ProductionVariants[].{Name:VariantName,Weight:CurrentWeight},Capture:DataCaptureConfig}'
  cap "2. Captured objects per variant"
  for v in champion canary; do
    echo "$v: $(aws s3 ls s3://$B/datacapture/$EP/$v/ --recursive | wc -l | tr -d ' ') files"
  done
  cap "3. Baseline in S3"
  aws s3 ls s3://$B/monitoring/baseline/
  cap "4. Drift jobs"
  for j in $(aws sagemaker list-processing-jobs --name-contains nsc-evidently \
               --query 'ProcessingJobSummaries[].ProcessingJobName' --output text); do
    aws sagemaker describe-processing-job --processing-job-name $j \
      --query '{Job:ProcessingJobName,Status:ProcessingJobStatus,Role:RoleArn,Instance:ProcessingResources.ClusterConfig.InstanceType,Start:ProcessingStartTime,End:ProcessingEndTime}'
  done
  cap "5. Business layer: custom metric"
  aws cloudwatch get-metric-statistics --namespace $NS --metric-name ChurnAlertVolume \
    --dimensions Name=EndpointName,Value=$EP \
    --start-time $S --end-time $E --period 3600 --statistics Maximum
  cap "6. Model layer: drift metric"
  aws cloudwatch get-metric-statistics --namespace $NS --metric-name DriftDetected \
    --dimensions Name=EndpointName,Value=$EP \
    --start-time $S --end-time $E --period 300 --statistics Maximum \
    --query 'Datapoints[].[Timestamp,Maximum]' --output text
  cap "7. Pipeline layer: recent Glue job runs"
  for j in northstar-dev-transform northstar-dev-feature-engineer; do
    aws glue get-job-runs --job-name $j \
      --query 'JobRuns[:3].[JobName,StartedOn,JobRunState,ExecutionTime]' --output text
  done
  cap "8. Application layer: ModelLatency p50/p95/p99 on champion (MICROSECONDS)"
  aws cloudwatch get-metric-statistics --namespace AWS/SageMaker --metric-name ModelLatency \
    --dimensions Name=EndpointName,Value=$EP Name=VariantName,Value=champion \
    --start-time $S --end-time $E --period 3600 --extended-statistics p50 p95 p99 \
    --query 'Datapoints[].[Timestamp,ExtendedStatistics.p50,ExtendedStatistics.p95,ExtendedStatistics.p99]' --output text
  cap "9. Infrastructure layer: CPU and memory on champion"
  for m in CPUUtilization MemoryUtilization; do
    echo "-- $m"
    aws cloudwatch get-metric-statistics --namespace /aws/sagemaker/Endpoints --metric-name $m \
      --dimensions Name=EndpointName,Value=$EP Name=VariantName,Value=champion \
      --start-time $S --end-time $E --period 3600 --statistics Average Maximum \
      --query 'Datapoints[].[Timestamp,Average,Maximum]' --output text
  done
  cap "10. Alarms: threshold and state"
  aws cloudwatch describe-alarms \
    --query 'MetricAlarms[?contains(AlarmName,`northstar`)].[AlarmName,MetricName,Threshold,StateValue]' --output text
  cap "11. Dashboard"
  aws cloudwatch list-dashboards --dashboard-name-prefix NorthStar-AI-Platform \
    --query 'DashboardEntries[].[DashboardName,LastModified]' --output text
}

window2 () {
  cap "12. After teardown: live API"
  echo "endpoints:              $(aws sagemaker list-endpoints --query 'length(Endpoints)' --output text)"
  echo "running processing jobs: $(aws sagemaker list-processing-jobs --status-equals InProgress --query 'length(ProcessingJobSummaries)' --output text)"
  echo "northstar alarms:       $(aws cloudwatch describe-alarms --query 'length(MetricAlarms[?contains(AlarmName,`northstar`)])' --output text)"
}

case "$WINDOW" in
  1)
    if [ -s "$OUT" ] && [ "${FORCE:-0}" != "1" ] && grep -q '^===== 12\.' "$OUT"; then
      echo "$OUT already has section 12 (post-teardown). Refusing to overwrite it." >&2
      echo "Set FORCE=1 only if the endpoint is live again and you mean to recapture." >&2
      exit 2
    fi
    window1 2>&1 | tee "$OUT"
    echo
    echo ">> wrote $OUT ($(grep -c '^===== ' "$OUT") of 11 sections). READ IT before teardown."
    # A header immediately followed by a blank line or the next header is an empty section.
    awk '/^===== /{ if (hdr != "" && body == 0) print ">> EMPTY: " hdr; hdr=$0; body=0; next }
         NF { body++ }
         END { if (hdr != "" && body == 0) print ">> EMPTY: " hdr }' "$OUT"
    grep -n -i 'AccessDenied\|An error occurred' "$OUT" && echo ">> errors above: fix and re-run" || true
    ;;
  2)
    [ -s "$OUT" ] || { echo "$OUT does not exist; window 1 was never captured" >&2; exit 2; }
    if grep -q '^===== 12\.' "$OUT"; then
      echo "$OUT already has section 12; not appending a second one." >&2; exit 2
    fi
    window2 2>&1 | tee -a "$OUT"
    echo
    echo ">> $(grep -c '^===== ' "$OUT") sections in $OUT (need 12). All three lines above must read 0."
    ;;
  *)
    echo "usage: bash scripts/capture-lab6.sh 1|2" >&2; exit 2 ;;
esac
