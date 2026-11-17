"""Test isolation: no test may read the developer's AWS configuration or credentials."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_aws(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    empty = tmp_path / "empty"
    empty.write_text("")
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty))
    # Dummy values so boto3 can build a client; nothing is ever sent.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
