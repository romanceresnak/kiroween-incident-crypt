# gitops/apps

This directory is the source of the root Argo CD `Application` (`app-of-apps`)
that Terraform installs. Terraform points it at `gitops/apps` on `main`.

**Put only Argo CD `Application` manifests here.** Each file is a child
application, for example the agents Helm chart or monitoring. The root app
creates them, and each child then deploys its own workload from its own path.

Do not put workload manifests (Deployments, Services, RBAC, charts) in this
directory. They belong with the thing they deploy, for example
`gitops/charts/agents/`, and are referenced from a child `Application` here.
Mixing the two makes the root app try to apply workloads directly, and it loses
the one-app-per-workload boundary that gives each workload its own sync and
health status.

Argo CD's directory source reads only `.yaml`, `.yml` and `.json` files, so this
README is ignored by the sync.

The root app has automated sync with `prune` and `selfHeal`. Deleting a file
here deletes the child `Application` and everything it manages.
