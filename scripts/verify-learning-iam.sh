#!/usr/bin/env bash
# Check the orchestrator role's learning permissions with the IAM policy simulator
# (polish REQ-IAM-02). Read-only: it calls sts get-caller-identity and
# iam simulate-principal-policy, which evaluate policies without performing any action.
#
#   scripts/verify-learning-iam.sh
#
# Allowed: s3:PutObject on learned/*, bedrock:StartIngestionJob on the Knowledge Base.
# Everything else listed below must be denied. Exit 0 when every check matches, 1 when
# any differs or the account is not the expected one.
#
# Environment (all optional):
#   AWS_REGION               default eu-west-1
#   EXPECTED_ACCOUNT_ID      default 255834079310
#   ORCHESTRATOR_ROLE_NAME   default kiroween-prod-orchestrator
#   POSTMORTEM_BUCKET        default kiroween-prod-postmortems-255834079310
#   KNOWLEDGE_BASE_ID        default VIBAT27SDH
set -euo pipefail

REGION="${AWS_REGION:-eu-west-1}"
EXPECTED_ACCOUNT_ID="${EXPECTED_ACCOUNT_ID:-255834079310}"
ROLE_NAME="${ORCHESTRATOR_ROLE_NAME:-kiroween-prod-orchestrator}"
BUCKET="${POSTMORTEM_BUCKET:-kiroween-prod-postmortems-255834079310}"
KB_ID="${KNOWLEDGE_BASE_ID:-VIBAT27SDH}"

account="$(aws sts get-caller-identity --query Account --output text --region "${REGION}")"
if [[ "${account}" != "${EXPECTED_ACCOUNT_ID}" ]]; then
  echo "refusing to run: AWS account is '${account}', expected ${EXPECTED_ACCOUNT_ID}" >&2
  exit 1
fi

ROLE_ARN="arn:aws:iam::${account}:role/${ROLE_NAME}"
BUCKET_ARN="arn:aws:s3:::${BUCKET}"
KB_ARN="arn:aws:bedrock:${REGION}:${account}:knowledge-base/${KB_ID}"
OTHER_KB_ARN="arn:aws:bedrock:${REGION}:${account}:knowledge-base/NOTTHISONE1"

failures=0

check() { # expected(allowed|denied) action resource
  local expected="$1" action="$2" resource="$3" decision verdict
  decision="$(aws iam simulate-principal-policy --policy-source-arn "${ROLE_ARN}" \
    --action-names "${action}" --resource-arns "${resource}" --region "${REGION}" \
    --query 'EvaluationResults[0].EvalDecision' --output text)"
  if [[ "${decision}" == "allowed" ]]; then verdict="allowed"; else verdict="denied"; fi
  if [[ "${verdict}" == "${expected}" ]]; then
    printf 'PASS  %-8s %-28s %s\n' "${expected}" "${action}" "${resource}"
  else
    printf 'FAIL  expected %s, got %s (%s)  %s  %s\n' \
      "${expected}" "${verdict}" "${decision}" "${action}" "${resource}"
    failures=$((failures + 1))
  fi
}

check allowed s3:PutObject "${BUCKET_ARN}/learned/probe.md"
check allowed bedrock:StartIngestionJob "${KB_ARN}"

check denied s3:PutObject "${BUCKET_ARN}/postmortems/probe.md"
check denied s3:PutObject "${BUCKET_ARN}/probe.md"
check denied s3:GetObject "${BUCKET_ARN}/learned/probe.md"
check denied s3:DeleteObject "${BUCKET_ARN}/learned/probe.md"
check denied s3:ListBucket "${BUCKET_ARN}"
check denied bedrock:StartIngestionJob "${OTHER_KB_ARN}"
check denied bedrock:Retrieve "${KB_ARN}"
check denied bedrock:DeleteKnowledgeBase "${KB_ARN}"

if (( failures > 0 )); then
  echo "${failures} check(s) did not match" >&2
  exit 1
fi
echo "all checks match"
