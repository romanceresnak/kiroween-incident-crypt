# Product — Kiroween Incident Crypt 👻

Spooky-themed EKS incident management platform built for the Kiroween 2026 hackathon.

## Purpose

An autonomous incident response system for Kubernetes clusters. An orchestrator agent triages incoming incidents and delegates to specialist agents based on signal type. All investigation is autonomous; any action that mutates cluster state requires explicit human approval.

## Agents

| Agent | Responsibility |
|---|---|
| **Orchestrator** | Receives incident signals, classifies them, routes to the right specialist, and synthesises the final response. |
| **CPU/Memory** | Analyses resource exhaustion, OOMKills, throttling, and HPA behaviour. |
| **Network** | Diagnoses connectivity failures, DNS issues, NetworkPolicy misconfigurations, and ingress errors. |
| **Deploy/Rollout** | Investigates failed deployments, stuck rollouts, image pull errors, and Argo CD sync failures. |
| **Storage** | Handles PVC binding failures, disk pressure, and volume mount issues. |
| **Incident Memory** | RAG agent over past postmortems. Surfaces similar past incidents to inform the current triage. |

## Human-in-the-loop

Agents have read-only access to the cluster. Any proposed remediation (patch, restart, scale, config change) must be presented to a human operator for approval before execution.

## Demo Scenarios

Each scenario is triggered by a script in `/chaos/`.

| Scenario | Agent |
|---|---|
| OOMKilled pod | cpu_memory |
| Broken NetworkPolicy blocking a service | network |
| Bad image tag causing ImagePullBackOff | deploy_rollout |
| PVC stuck Pending | storage |

## Dashboard

A real-time UI showing:
- Model usage and cost broken down per agent
- Model usage and cost broken down per incident
- Active incident status and agent activity feed

## Out of Scope

- Multi-cluster support
- Service mesh
- Auto-remediation without human approval
