import secrets

import pytest


@pytest.fixture(autouse=True)
def auth_signing_secret(monkeypatch):
    monkeypatch.setenv("FLOWFORGE_JWT_SECRET", secrets.token_urlsafe(48))
