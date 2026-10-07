import json
import subprocess

import pytest

from app.secrets import EnvProvider, SecretNotFound, SecretStashProvider, get_secrets_provider
from app.settings import Settings


def test_env_provider_reads_and_raises(monkeypatch):
    monkeypatch.setenv("X_TEST_SECRET", "abc")
    p = EnvProvider()
    assert p.get("X_TEST_SECRET") == "abc"
    with pytest.raises(SecretNotFound):
        p.get("DOES_NOT_EXIST_123")


def test_settings_secret_is_masked_in_repr(monkeypatch):
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY", "tf-very-secret")
    key = Settings().token_factory_api_key
    assert key is not None
    assert key.get_secret_value() == "tf-very-secret"
    assert "tf-very-secret" not in repr(key) and "tf-very-secret" not in str(key)


def test_blank_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("CORS_ORIGINS", "  ")
    s = Settings()
    assert s.database_url.startswith("sqlite")
    assert s.cors_origin_list == ["http://localhost:5173"]


def test_db_url_normalised_for_hosted_postgres():
    from app.db import _normalise

    assert _normalise("postgres://u:p@h/db").startswith("postgresql+psycopg://")
    assert _normalise("postgresql://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert _normalise("sqlite:///x.db") == "sqlite:///x.db"


def test_unknown_provider_rejected(monkeypatch):
    get_secrets_provider.cache_clear()
    monkeypatch.setenv("SECRETS_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_secrets_provider()
    get_secrets_provider.cache_clear()


def test_secretstash_provider_parses_cli_json(monkeypatch):
    payload = {"data": [{"key": "TOKEN_FACTORY_API_KEY", "string_value": "tf-from-stash"}]}
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("app.secrets.shutil.which", lambda _: "/usr/bin/nebius")
    monkeypatch.setattr("app.secrets.subprocess.run", fake_run)
    p = SecretStashProvider("mbsec-123")
    assert p.get("TOKEN_FACTORY_API_KEY") == "tf-from-stash"
    assert p.get("TOKEN_FACTORY_API_KEY") == "tf-from-stash"
    assert len(calls) == 1                          # cached after first read
    assert calls[0][:4] == ["/usr/bin/nebius", "mysterybox", "payload", "get"] or calls[0][1] == "mysterybox"
    with pytest.raises(SecretNotFound):
        p.get("MISSING")


def test_secretstash_requires_id():
    with pytest.raises(ValueError):
        SecretStashProvider("")


def test_tests_never_read_dot_env_local():
    """Regression: a developer's real .env.local must not leak into tests."""
    from app.settings import ENV_FILE, Settings, get_settings

    get_settings.cache_clear()
    get_settings()
    assert "TOKEN_FACTORY_API_KEY" not in __import__("os").environ
    assert Settings().token_factory_api_key is None
    assert Settings.model_config["env_file"] is None
    assert ENV_FILE.name == ".env.local"
