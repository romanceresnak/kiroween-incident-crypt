#!/usr/bin/env bash
# PVC stuck Pending chaos scenario (specialists REQ-CHS-01, CHS-04).
#
#   chaos/pvc/pvc.sh up     create namespace chaos-pvc: a PVC naming a StorageClass that does
#                           not exist and a pod that mounts it (both stay Pending)
#   chaos/pvc/pvc.sh down   delete namespace chaos-pvc (succeeds if it does not exist)
#
# Refuses to run unless the current kubectl context targets $EXPECTED_CLUSTER
# (default kiroween-prod). Acts only on the namespace chaos-pvc.
set -euo pipefail

EXPECTED_CLUSTER="${EXPECTED_CLUSTER:-kiroween-prod}"
NAMESPACE="chaos-pvc"
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
    echo "Deployed. The claim and the pod stay Pending: kubectl -n ${NAMESPACE} get pvc,pods,events"
    ;;
  down)
    check_context
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true
    ;;
  *)
    usage
    ;;
esac
