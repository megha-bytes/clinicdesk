"""Secrets access behind one interface, so the backend never cares where keys live.

- EnvProvider: laptop (.env.local), CI (GitHub secrets) and the free host (env vars).
- SecretStashProvider: upgrade path for Nebius SecretStash (formerly MysteryBox) on a
  Nebius VM whose service account has the `mysterybox.payload-viewer` role.

Select with SECRETS_PROVIDER=env|secretstash.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Protocol


class SecretNotFound(KeyError):
    pass


class SecretsProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvProvider:
    def get(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise SecretNotFound(name)
        return value


class SecretStashProvider:
    """Reads one SecretStash secret (a set of key/value pairs) once, then serves keys from memory."""

    def __init__(self, secret_id: str, cli: str = "nebius") -> None:
        if not secret_id:
            raise ValueError("SECRETSTASH_SECRET_ID is required for the secretstash provider")
        self.secret_id = secret_id
        self.cli = cli
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if shutil.which(self.cli) is None:
            raise RuntimeError("Nebius CLI not found; SecretStash needs a Nebius VM or the CLI installed")
        out = subprocess.run(
            [self.cli, "mysterybox", "payload", "get", "--secret-id", self.secret_id, "--format", "json"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
        data = json.loads(out)
        entries = data.get("data", data.get("payload", []))
        result: dict[str, str] = {}
        for e in entries:
            key = e.get("key")
            val = e.get("string_value", e.get("text_value", e.get("value")))
            if key and val is not None:
                result[key] = val
        return result

    def get(self, name: str) -> str:
        if self._cache is None:
            self._cache = self._load()
        if name not in self._cache:
            raise SecretNotFound(name)
        return self._cache[name]


@lru_cache
def get_secrets_provider() -> SecretsProvider:
    kind = os.environ.get("SECRETS_PROVIDER", "env").lower()
    if kind == "env":
        return EnvProvider()
    if kind == "secretstash":
        return SecretStashProvider(os.environ.get("SECRETSTASH_SECRET_ID", ""))
    raise ValueError(f"Unknown SECRETS_PROVIDER: {kind}")
