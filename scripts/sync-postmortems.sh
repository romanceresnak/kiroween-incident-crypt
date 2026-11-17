#!/usr/bin/env bash
# Upload docs/postmortems to the postmortem bucket and re-index the Bedrock Knowledge Base
# (memory-and-ui REQ-MEM-02). Run locally by an operator with AWS credentials.
#
#   scripts/sync-postmortems.sh [--delete] [--timeout SECONDS]
#
#   --delete          also delete objects under postmortems/ that are not in git
#   --timeout N       give up waiting for the ingestion job after N seconds (default 900)
#
# Environment (all optional):
#   POSTMORTEM_BUCKET, KNOWLEDGE_BASE_ID   default: terraform output in infra/
#   AWS_REGION                             default: eu-west-1
#   EXPECTED_ACCOUNT_ID                    default: 255834079310; refuses any other account
#   POLL_SECONDS                           default: 10
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${ROOT}/docs/postmortems"
PREFIX="postmortems/"
REGION="${AWS_REGION:-eu-west-1}"
EXPECTED_ACCOUNT_ID="${EXPECTED_ACCOUNT_ID:-255834079310}"
POLL_SECONDS="${POLL_SECONDS:-10}"
TIMEOUT=900
DELETE=0

usage() {
  echo "usage: $0 [--delete] [--timeout SECONDS]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete) DELETE=1; shift ;;
    --timeout)
      [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || usage
      TIMEOUT="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -d "${SOURCE_DIR}" ]] || { echo "missing ${SOURCE_DIR}" >&2; exit 1; }

account="$(aws sts get-caller-identity --query Account --output text --region "${REGION}")"
if [[ "${account}" != "${EXPECTED_ACCOUNT_ID}" ]]; then
  echo "refusing to run: AWS account is '${account}', expected ${EXPECTED_ACCOUNT_ID}" >&2
  exit 1
fi

tf_output() {
  terraform -chdir="${ROOT}/infra" output -raw "$1"
}
BUCKET="${POSTMORTEM_BUCKET:-$(tf_output postmortem_bucket_name)}"
KB_ID="${KNOWLEDGE_BASE_ID:-$(tf_output knowledge_base_id)}"
[[ -n "${BUCKET}" && -n "${KB_ID}" ]] || { echo "bucket or knowledge base id is empty" >&2; exit 1; }

sync_args=(s3 sync "${SOURCE_DIR}" "s3://${BUCKET}/${PREFIX}" --region "${REGION}"
           --exclude "*" --include "*.md" --include "*.md.metadata.json")
if [[ "${DELETE}" -eq 1 ]]; then
  sync_args+=(--delete)
fi
echo "Uploading ${SOURCE_DIR} to s3://${BUCKET}/${PREFIX}"
aws "${sync_args[@]}"

read -r -a sources <<< "$(aws bedrock-agent list-data-sources --knowledge-base-id "${KB_ID}" \
  --region "${REGION}" --query 'dataSourceSummaries[].dataSourceId' --output text)"
if [[ "${#sources[@]}" -ne 1 ]]; then
  echo "expected exactly one data source on ${KB_ID}, found ${#sources[@]}" >&2
  exit 1
fi
DS_ID="${sources[0]}"

job="$(aws bedrock-agent start-ingestion-job --knowledge-base-id "${KB_ID}" \
  --data-source-id "${DS_ID}" --region "${REGION}" \
  --query 'ingestionJob.ingestionJobId' --output text)"
echo "Started ingestion job ${job} on ${KB_ID}/${DS_ID}"

job_field() {
  aws bedrock-agent get-ingestion-job --knowledge-base-id "${KB_ID}" --data-source-id "${DS_ID}" \
    --ingestion-job-id "${job}" --region "${REGION}" --query "ingestionJob.$1" --output text
}

waited=0
while true; do
  status="$(job_field status)"
  case "${status}" in
    COMPLETE)
      echo "Ingestion complete: $(job_field statistics)"
      exit 0
      ;;
    FAILED|STOPPED)
      echo "Ingestion ${status}: $(job_field failureReasons)" >&2
      exit 1
      ;;
  esac
  if (( waited >= TIMEOUT )); then
    echo "Ingestion job ${job} still ${status} after ${TIMEOUT}s" >&2
    exit 1
  fi
  echo "Ingestion ${status}..."
  sleep "${POLL_SECONDS}"
  waited=$(( waited + (POLL_SECONDS > 0 ? POLL_SECONDS : 1) ))
done
