import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flowforge.api import create_app
from flowforge.auth.security import AuthSettings, verify_password


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(
        "FLOWFORGE_DATABASE_URL", "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"
    )
    monkeypatch.setenv("FLOWFORGE_RABBITMQ_URL", "amqp://unused/")
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("header", [None, "Bearer invalid", "Basic dXNlcjpwYXNz"])
def test_invalid_bearer_does_not_query_database(client, header):
    response = client.get("/api/v1/auth/me", headers={"Authorization": header} if header else {})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"


def test_missing_signing_key_fails_startup_and_bad_hash_is_not_authenticated(monkeypatch):
    monkeypatch.delenv("FLOWFORGE_JWT_SECRET")
    with pytest.raises(ValidationError):
        AuthSettings()
    assert not verify_password("secret", "corrupt-hash")


def test_errors_do_not_echo_credentials(client):
    response = client.post(
        "/api/v1/auth/login",
        headers={"X-FlowForge-CSRF": "1"},
        json={"email": "invalid", "password": "DO-NOT-ECHO", "role": "OWNER"},
    )
    assert response.status_code == 422
    assert "DO-NOT-ECHO" not in response.text
    assert client.get("/does-not-exist").status_code == 404
    assert (
        client.post("/api/v1/auth/refresh", headers={"X-FlowForge-CSRF": "1"}, json={}).status_code
        == 401
    )
