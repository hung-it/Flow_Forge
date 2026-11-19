import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
COOKIE_NAME = "flowforge_refresh"
COOKIE_PATH = "/api/v1/auth"
_HASH_SLOTS = threading.BoundedSemaphore(2)


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWFORGE_", extra="ignore", hide_input_in_errors=True
    )
    jwt_secret: SecretStr
    cookie_secure: bool = False
    allowed_origins: list[str] = [
        "http://localhost:8000",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:5173",
    ]

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT secret must contain at least 32 bytes")
        return value


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as error:
        raise ValueError("Invalid email") from error
    if len(email) > 254:
        raise ValueError("Email too long")
    return email


def verify_password(password: str, hashed: str) -> bool:
    with _HASH_SLOTS:
        try:
            return passwords.verify(password, hashed)
        except (UnknownHashError, ValueError):
            return False


def hash_password(password: str) -> str:
    with _HASH_SLOTS:
        return passwords.hash(password)


def issue_access(settings: AuthSettings, user: UUID, session: UUID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        dict(
            sub=str(user),
            sid=str(session),
            iat=now,
            exp=now + timedelta(minutes=30),
            iss="flowforge",
            aud="flowforge-api",
        ),
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def read_access(settings: AuthSettings, token: str) -> tuple[UUID, UUID]:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="flowforge",
            audience="flowforge-api",
            options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"]},
        )
        return UUID(claims["sub"]), UUID(claims["sid"])
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid access token") from error


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
