from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from flowforge.api import create_app


def test_live_and_database_failure(monkeypatch):
    monkeypatch.setenv("FLOWFORGE_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("FLOWFORGE_RABBITMQ_URL", "amqp://test:test@localhost/")
    monkeypatch.setattr("flowforge.api.check_database", AsyncMock(side_effect=OSError()))
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.headers["X-Request-ID"]


def test_ready_checks_schema(monkeypatch):
    monkeypatch.setenv("FLOWFORGE_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("FLOWFORGE_RABBITMQ_URL", "amqp://test:test@localhost/")
    check = AsyncMock(return_value=True)
    monkeypatch.setattr("flowforge.api.check_database", check)
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").json() == {"status": "ready"}
    check.assert_awaited_once()
