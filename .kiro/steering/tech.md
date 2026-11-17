# Tech Stack

## Core

| Layer | Technology |
|---|---|
| Agent framework | Strands Agents (Python 3.12) |
| LLM provider | Amazon Bedrock |
| Cluster | Amazon EKS |
| IaC | Terraform |
| GitOps | Argo CD |
| CI/CD | GitHub Actions |
| Observability | Prometheus + Grafana |
| Region | eu-west-1 |

## Python

- Version: **3.12**
- One package per agent under `/agents/`
- Dependencies managed with `pyproject.toml` per package

## Security

Each agent runs under its own Kubernetes ServiceAccount with a least-privilege, read-only RBAC role scoped to only the API groups it needs. No agent has write access to the cluster. Cross-agent calls go through the orchestrator — agents never call each other directly.

AWS access from pods is granted via **EKS Pod Identity** — no static credentials anywhere.

## Remediation Flow

Agents are read-only. When a human approves a remediation, it is implemented as a pull request against `/gitops`. Argo CD applies the change after merge. Git history serves as the audit trail and rollback mechanism.

## RAG — Incident Memory

- **Knowledge base**: Amazon Bedrock Knowledge Base over postmortems stored in S3
- **Vector store**: Amazon S3 Vectors
- Postmortems live in `/docs/postmortems/`; 6–8 seeded samples are included for the demo

## Cost Attribution

Token usage from every Bedrock response is exported as Prometheus metrics labelled with `agent` and `incident_id`. Grafana dashboards surface per-agent and per-incident cost from these metrics.

## Bedrock

- Model selection per agent type (reasoning-heavy agents may use a larger model)
- All Bedrock calls are logged for cost attribution per agent and per incident
