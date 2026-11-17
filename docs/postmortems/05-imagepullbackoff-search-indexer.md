# PM-2026-072: search-indexer rollout stuck on a tag that was never pushed

- **Date:** 2026-08-14
- **Severity:** SEV-3
- **Signal:** ErrImagePull, ImagePullBackOff
- **Namespace / workload:** `search` / Deployment `search-indexer`, container `indexer`

## Summary

A release bumped the image of `search-indexer` to `registry.example.internal/search/
indexer:2.14.0`, but the CI job that pushes the image had failed, so the tag did not
exist. The new ReplicaSet's pod sat in ImagePullBackOff. The old ReplicaSet (revision 41)
kept serving, so users were not affected; the rollout simply never finished.

## Impact

No user impact. The indexing improvements in 2.14.0 were delayed by one day, and the
stuck rollout blocked two later releases until it was noticed.

## Root cause

The release pipeline updated the GitOps manifest before confirming that the image push
succeeded. The registry returned `manifest unknown` for the new tag. Kubernetes behaved
as designed: `maxUnavailable: 0` kept the old pod running.

## Evidence

- Pod events: `Failed to pull image "...indexer:2.14.0": not found`, then `Back-off
  pulling image`.
- Container state: `waiting.reason: ImagePullBackOff`.
- ReplicaSet history: revision 41 image `indexer:2.13.2`, ready 1/1; revision 42 image
  `indexer:2.14.0`, ready 0/1.
- Deployment status: `updatedReplicas: 1`, `availableReplicas: 1`,
  `unavailableReplicas: 1`, condition `Progressing=False, ProgressDeadlineExceeded`.

## Fix

Rolled the container `indexer` image back to the last revision that ran,
`indexer:2.13.2` (revision 41), via a pull request to the GitOps manifest, then re-ran the
image build and released 2.14.0 again once the tag existed.

## Prevention

- The release pipeline now checks that the tag exists in the registry
  (`describe-images`) before it edits any manifest.
- Alert on `ProgressDeadlineExceeded` for any Deployment.
- Prefer rolling back to the image of a revision that was Ready over guessing a newer tag.
