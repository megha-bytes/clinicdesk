from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_ok_and_db():
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["token_factory_key"] == "missing"   # tests never see a real key


def test_health_never_leaks_key(monkeypatch):
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY", "tf-super-secret-value")
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.json()["token_factory_key"] == "set"
    assert "tf-super-secret-value" not in r.text


def test_cors_allows_local_frontend():
    with TestClient(app) as client:
        r = client.options(
            "/health",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
