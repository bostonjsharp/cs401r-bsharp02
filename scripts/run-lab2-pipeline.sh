#!/usr/bin/env bash
# run-lab2-pipeline.sh
# Runs the Lab 2 data pipeline end to end and waits for it to finish.
#
# Every name comes from `terraform output`, so nothing here is hardcoded and
# the script keeps working if the project or environment is renamed.
#
# Default: one Glue workflow run (crawler -> transform -> feature-engineer),
# each stage gated on the previous one succeeding.
#
#   bash scripts/run-lab2-pipeline.sh            # workflow mode
#   bash scripts/run-lab2-pipeline.sh --steps    # run the three stages one by one
#
# Prerequisites: terraform apply has completed in infrastructure/environments/dev
# and the AWS CLI is authenticated against the lab account.

set -euo pipefail

TF_DIR="${TF_DIR:-infrastructure/environments/dev}"
MODE="${1:-workflow}"

out () { terraform -chdir="${TF_DIR}" output -raw "$1"; }

CRAWLER=$(out glue_crawler_name)
TRANSFORM=$(out glue_transform_job_name)
FEATURES=$(out glue_feature_engineer_job_name)
WORKFLOW=$(out glue_workflow_name)
DB=$(out glue_database_name)
TABLE=$(out glue_table_name)
FG=$(out feature_group_name)

echo "==> Lab 2 pipeline"
echo "    crawler   : ${CRAWLER}"
echo "    transform : ${TRANSFORM}"
echo "    features  : ${FEATURES}"
echo "    workflow  : ${WORKFLOW}"
echo ""

wait_crawler () {
  while true; do
    state=$(aws glue get-crawler --name "$1" --query 'Crawler.State' --output text)
    [ "${state}" = "READY" ] && break
    printf "      crawler %s\r" "${state}"; sleep 15
  done
  status=$(aws glue get-crawler --name "$1" --query 'Crawler.LastCrawl.Status' --output text)
  echo "      crawler finished: ${status}"
  [ "${status}" = "SUCCEEDED" ]
}

wait_job () { # job-name run-id
  while true; do
    state=$(aws glue get-job-run --job-name "$1" --run-id "$2" --query 'JobRun.JobRunState' --output text)
    case "${state}" in
      SUCCEEDED) echo "      ${1}: SUCCEEDED"; return 0 ;;
      FAILED|ERROR|TIMEOUT|STOPPED)
        echo "      ${1}: ${state}"
        aws glue get-job-run --job-name "$1" --run-id "$2" --query 'JobRun.ErrorMessage' --output text
        return 1 ;;
      *) printf "      %s %s\r" "$1" "${state}"; sleep 20 ;;
    esac
  done
}

if [ "${MODE}" = "--steps" ]; then
  echo "[1/3] crawler"
  aws glue start-crawler --name "${CRAWLER}"
  wait_crawler "${CRAWLER}"
  aws glue get-table --database-name "${DB}" --name "${TABLE}" \
    --query 'Table.StorageDescriptor.Columns[*].[Name,Type]' --output text | sed 's/^/      /'

  echo "[2/3] transform"
  run=$(aws glue start-job-run --job-name "${TRANSFORM}" --query JobRunId --output text)
  wait_job "${TRANSFORM}" "${run}"

  echo "[3/3] feature-engineer"
  run=$(aws glue start-job-run --job-name "${FEATURES}" --query JobRunId --output text)
  wait_job "${FEATURES}" "${run}"
else
  echo "[1/1] workflow run"
  run=$(aws glue start-workflow-run --name "${WORKFLOW}" --query RunId --output text)
  echo "      run id ${run}"
  while true; do
    status=$(aws glue get-workflow-run --name "${WORKFLOW}" --run-id "${run}" \
      --query 'Run.Status' --output text)
    [ "${status}" = "COMPLETED" ] || [ "${status}" = "STOPPED" ] || [ "${status}" = "ERROR" ] && break
    printf "      workflow %s\r" "${status}"; sleep 30
  done
  echo "      workflow ${status}"
  aws glue get-workflow-run --name "${WORKFLOW}" --run-id "${run}" \
    --query 'Run.Statistics' --output table
  # A workflow run reports COMPLETED even when an action failed, so check
  # the individual stages.
  wait_crawler "${CRAWLER}"
  for job in "${TRANSFORM}" "${FEATURES}"; do
    run=$(aws glue get-job-runs --job-name "${job}" --query 'JobRuns[0].Id' --output text)
    wait_job "${job}" "${run}"
  done
fi

echo ""
echo "==> Feature group ${FG}: $(aws sagemaker describe-feature-group --feature-group-name "${FG}" \
  --query 'FeatureGroupStatus' --output text)"
echo "==> Done. Next: bash scripts/verify-lab2.sh | tee docs/lab2-verify-output.txt"
