"""The orchestrator's view of the lab-runner: three HTTP calls, nothing else.

This module never imports the runner or the Kubernetes writer, and the orchestrator's service
account has no permission to write, so a bug here cannot change a cluster.
"""

from __future__ import annotations

from typing import Any

import httpx


class LabUnavailable(RuntimeError):
    """The runner did not answer."""


class LabError(RuntimeError):
    """The runner answered with an error status."""

    def __init__(self, status: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


class LabClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None
                    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout,
                                         transport=self._transport) as client:
                response = await client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise LabUnavailable(type(exc).__name__) from exc
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", ""))[:300]
            except (ValueError, AttributeError):
                detail = ""
            retry = response.headers.get("retry-after")
            raise LabError(response.status_code,
                           detail or f"the runner answered {response.status_code}",
                           int(retry) if retry and retry.isdigit() else None)
        try:
            data = response.json()
        except ValueError as exc:
            raise LabUnavailable("not JSON") from exc
        return data if isinstance(data, dict) else {}

    async def status(self) -> dict[str, Any]:
        return await self._call("GET", "/status")

    async def run(self, scenario: str) -> dict[str, Any]:
        return await self._call("POST", "/run", {"scenario": scenario})

    async def reset(self) -> dict[str, Any]:
        return await self._call("POST", "/reset")
