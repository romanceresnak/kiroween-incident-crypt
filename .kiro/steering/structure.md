# Project Structure

```
kiroween-incident-crypt/
├── agents/           # One Python package per agent
│   ├── orchestrator/
│   ├── cpu_memory/
│   ├── network/
│   ├── deploy_rollout/
│   ├── storage/
│   ├── incident_memory/
│   └── shared/       # Common Bedrock client, metrics helpers, Kubernetes tools
├── infra/            # Terraform — EKS cluster, IAM, networking
├── ui/               # Dashboard (model usage, cost, incident feed)
├── gitops/           # Argo CD Application CRs + Kubernetes manifests
│   └── charts/
│       └── agents/   # Helm chart for deploying the agents
├── chaos/            # Break-the-cluster scenarios for testing
├── docs/
│   └── postmortems/  # 6–8 seeded sample postmortems for the RAG demo
└── .github/
    └── workflows/    # Terraform plan/apply, build and push agent images
```

## Conventions

- Each agent package lives under `/agents/<name>/` with its own `pyproject.toml`.
- `/agents/shared/` holds the common Bedrock client, Prometheus metrics helpers, and Kubernetes API tools shared across agents.
- `/infra/` contains all Terraform. No hardcoded values — everything via `variables.tf`. Terraform is applied by **GitHub Actions**, not Argo CD.
- `/gitops/` contains only Kubernetes manifests and Argo CD `Application` CRs. It does not manage infrastructure.
- `/gitops/charts/agents/` is the Helm chart used to deploy agent pods into the cluster.
- `/chaos/` scenarios are used to validate agent detection and triage, not to run in production.
- Postmortems go in `/docs/postmortems/` — the Incident Memory agent indexes these. 6–8 seeded samples are included for the demo.
