#!/usr/bin/env bash
# Bad image tag chaos scenario (specialists REQ-CHS-01, CHS-03).
#
#   chaos/badimage/badimage.sh up     create namespace chaos-badimage, roll out a working
#                                     revision, then update it to an image tag that does
#                                     not exist (ImagePullBackOff)
#   chaos/badimage/badimage.sh down   delete namespace chaos-badimage (succeeds if it does
#                                     not exist)
#
# Refuses to run unless the current kubectl context targets $EXPECTED_CLUSTER
# (default kiroween-prod). Acts only on the namespace chaos-badimage.
#
# Re-running up is safe: apply returns the Deployment to the working image, and the bad
# image is set again on top of it.
set -euo pipefail

EXPECTED_CLUSTER="${EXPECTED_CLUSTER:-kiroween-prod}"
NAMESPACE="chaos-badimage"
DEPLOYMENT="badimage-demo"
CONTAINER="web"
BAD_IMAGE="public.ecr.aws/docker/library/python:0.0.0-does-not-exist"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
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
    # Revision 1 must exist and run, so there is a good image to roll back to.
    kubectl apply -f "${HERE}/manifests.yaml"
    kubectl -n "${NAMESPACE}" rollout status "deployment/${DEPLOYMENT}" --timeout="${ROLLOUT_TIMEOUT}"
    kubectl -n "${NAMESPACE}" set image "deployment/${DEPLOYMENT}" "${CONTAINER}=${BAD_IMAGE}"
    echo "Broken. The new pod cannot pull its image: kubectl -n ${NAMESPACE} get pods -w"
    ;;
  down)
    check_context
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true
    ;;
  *)
    usage
    ;;
esac
