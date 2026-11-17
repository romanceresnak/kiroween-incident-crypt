#!/usr/bin/env bash
# OOMKilled chaos scenario (REQ-CHO).
#
#   chaos/oom/oom.sh up     create namespace chaos-oom with a pod that is OOMKilled repeatedly
#   chaos/oom/oom.sh down   delete namespace chaos-oom (succeeds if it does not exist)
#
# Refuses to run unless the current kubectl context targets $EXPECTED_CLUSTER
# (default kiroween-prod). Acts only on the namespace chaos-oom.
set -euo pipefail

EXPECTED_CLUSTER="${EXPECTED_CLUSTER:-kiroween-prod}"
NAMESPACE="chaos-oom"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "usage: $0 up|down" >&2
  exit 2
}

check_context() {
  local context
  context="$(kubectl config current-context 2>/dev/null || true)"
  # aws eks update-kubeconfig names the context with the cluster ARN.
  if [[ "${context}" != *":cluster/${EXPECTED_CLUSTER}" && "${context}" != "${EXPECTED_CLUSTER}" ]]; then
    echo "refusing to run: kubectl context is '${context:-<none>}', expected cluster ${EXPECTED_CLUSTER}" >&2
    exit 1
  fi
}

case "${1:-}" in
  up)
    check_context
    kubectl apply -f "${HERE}/manifests.yaml"
    echo "Deployed. Watch it: kubectl -n ${NAMESPACE} get pods -w"
    ;;
  down)
    check_context
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true
    ;;
  *)
    usage
    ;;
esac
