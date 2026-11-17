"""Settings read from the environment.

Every agent calls ``Settings.from_env`` at startup. Invalid configuration raises
``ConfigError`` so the process exits before serving traffic (REQ-BED-03).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Configuration is invalid; the process must not start."""


def require_eu_model(model_id: str) -> str:
    """Return ``model_id`` if it is an EU cross-region inference profile ID.

    The agents' IAM policies only allow ``inference-profile/eu.*`` and EU foundation
    models, so any other ID would fail on the first call. Failing at startup is clearer.
    """
    if not model_id or not model_id.startswith("eu."):
        raise ConfigError(
            f"model id {model_id!r} is not an EU cross-region inference profile "
            "(it must start with 'eu.')"
        )
    return model_id


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0, got {value}")
    return value


def env_list(env: Mapping[str, str], name: str, default: list[str]) -> list[str]:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Settings shared by the agents that call Bedrock."""

    agent_name: str
    model_id: str
    region: str = "eu-west-1"
    max_model_calls: int = 15
    max_tokens: int = 4096
    max_attempts: int = 4
    api_port: int = 8080
    metrics_port: int = 9090
    prices_json: str = "{}"

    @classmethod
    def from_env(cls, agent_name: str, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        settings = cls(
            agent_name=agent_name,
            model_id=require_eu_model(env.get("MODEL_ID", "")),
            region=env.get("AWS_REGION", "eu-west-1"),
            max_model_calls=env_int(env, "MAX_MODEL_CALLS", 15),
            max_tokens=env_int(env, "MAX_TOKENS", 4096),
            max_attempts=env_int(env, "BEDROCK_MAX_ATTEMPTS", 4),
            api_port=env_int(env, "API_PORT", 8080),
            metrics_port=env_int(env, "METRICS_PORT", 9090),
            prices_json=env.get("PRICES_JSON", "{}"),
        )
        if settings.api_port == settings.metrics_port:
            # The network policy opens the metrics port to Prometheus; sharing it
            # with the API would expose the API too (REQ-MET-05).
            raise ConfigError("API_PORT and METRICS_PORT must differ")
        return settings
