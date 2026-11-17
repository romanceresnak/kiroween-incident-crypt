"""Run incident-memory: ``python -m kiroween_incident_memory``."""

from __future__ import annotations

import os

import boto3
from botocore.config import Config

from kiroween_shared.config import env_int
from kiroween_shared.metrics import start_metrics_server
from kiroween_shared.server import configure_logging, serve

from .api import create
from .memory import Memory


def main() -> None:
    env = os.environ
    configure_logging(env.get("LOG_LEVEL", "INFO"))
    # Credentials come from EKS Pod Identity (incident-memory-sa); nothing static.
    client = boto3.client("bedrock-agent-runtime", region_name=env.get("AWS_REGION", "eu-west-1"),
                          config=Config(retries={"max_attempts": 3, "mode": "standard"},
                                        connect_timeout=5, read_timeout=20))
    memory = Memory(client, env.get("KNOWLEDGE_BASE_ID", ""),
                    top_k=env_int(env, "TOP_K", 3),
                    min_score=float(env.get("MIN_SCORE", "0.3")))
    start_metrics_server(env_int(env, "METRICS_PORT", 9090))
    serve(create(memory), env_int(env, "API_PORT", 8080))


main()
