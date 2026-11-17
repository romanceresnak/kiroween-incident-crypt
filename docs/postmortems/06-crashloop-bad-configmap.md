# PM-2026-079: notification-gateway crash loop after a ConfigMap key rename

- **Date:** 2026-09-02
- **Severity:** SEV-2
- **Signal:** CrashLoopBackOff (exit code 1), CreateContainerConfigError on new pods
- **Namespace / workload:** `notify` / Deployment `notification-gateway`, container `gateway`

## Summary

A config clean-up renamed the key `SMTP_HOST` to `smtp.host` in ConfigMap
`gateway-config`. The gateway reads `SMTP_HOST` from the environment and exits with an
error when it is empty. Running pods kept their old environment until they restarted;
every pod that started after the change crash-looped with exit code 1.

## Impact

Email notifications were delayed by 70 minutes. Push and SMS were not affected. Messages
were queued and delivered after the fix.

## Root cause

The Deployment maps the ConfigMap with `envFrom`, so the key name is the variable name.
The rename was valid YAML and passed review, but nothing checked that the application
still found the keys it requires. A second Deployment that used `valueFrom.configMapKeyRef`
on the old key failed earlier and louder, with `CreateContainerConfigError: couldn't find
key SMTP_HOST in ConfigMap notify/gateway-config`.

## Evidence

- Previous container log: `FATAL config: SMTP_HOST is required` then exit code 1.
- Pod state: `waiting.reason: CrashLoopBackOff`, `lastState.terminated.exitCode: 1`,
  restarts 14.
- ConfigMap keys: `smtp.host`, `smtp.port`, `FROM_ADDRESS` (no `SMTP_HOST`).
- Git history of the ConfigMap: the key was renamed in the most recent commit.
- Memory and CPU were normal; this was not an OOM.

## Fix

Restored the key `SMTP_HOST` in ConfigMap `gateway-config` (keeping `smtp.host` for the
new code path) through the GitOps repo, then restarted the Deployment so pods picked up
the environment. All pods were Ready within two minutes.

## Prevention

- Each service lists its required config keys; CI validates the ConfigMap against that
  list.
- Use `configMapKeyRef` for required keys so a missing key fails as
  `CreateContainerConfigError` before the process starts.
- Triage hint: CrashLoopBackOff with exit code 1 and a config error in the previous log
  points to config, not resources.
