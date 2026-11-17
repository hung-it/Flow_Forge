from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from flowforge.auth.security import (
    AuthSettings,
    issue_access,
    normalize_email,
    passwords,
    read_access,
)


def settings():
    return AuthSettings(jwt_secret="x" * 48)


def test_email_password_and_secret():
    assert normalize_email(" Person@Example.com ") == "person@example.com"
    with pytest.raises(ValueError):
        normalize_email("not-an-email")
    hashed = passwords.hash(" a long password ")
    assert passwords.verify(" a long password ", hashed)
    assert not passwords.verify("a long password", hashed)
    with pytest.raises(ValidationError) as error:
        AuthSettings(jwt_secret="private-short-key")
    assert "private-short-key" not in str(error.value)


def test_access_claims():
    user, session = uuid4(), uuid4()
    token = issue_access(settings(), user, session)
    assert read_access(settings(), token) == (user, session)


@pytest.mark.parametrize(
    "kind", ["expired", "signature", "audience", "issuer", "algorithm", "missing", "uuid"]
)
def test_reject_bad_access(kind):
    config = settings()
    now = datetime.now(UTC)
    claims = dict(
        sub=str(uuid4()),
        sid=str(uuid4()),
        iat=now,
        exp=now + timedelta(minutes=30),
        iss="flowforge",
        aud="flowforge-api",
    )
    secret, algorithm = config.jwt_secret.get_secret_value(), "HS256"
    if kind == "expired":
        claims["exp"] = now - timedelta(seconds=1)
    elif kind == "signature":
        secret = "z" * 48
    elif kind == "audience":
        claims["aud"] = "other"
    elif kind == "issuer":
        claims["iss"] = "other"
    elif kind == "algorithm":
        algorithm = "HS384"
    elif kind == "missing":
        del claims["sid"]
    else:
        claims["sub"] = "not-a-uuid"
    with pytest.raises(ValueError):
        read_access(config, jwt.encode(claims, secret, algorithm=algorithm))
