import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flowforge.errors import ApiError, unauthorized

from .security import DUMMY_HASH, hash_password, normalize_email, token_hash, verify_password

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    session_id: UUID
    email: str


async def principal(
    connection: AsyncConnection, user: UUID, session: UUID, *, lock: bool = False
) -> Principal:
    suffix = " FOR UPDATE OF s" if lock else ""
    row = (
        (
            await connection.execute(
                text(
                    """SELECT u.id, u.email FROM auth_sessions s
        JOIN users u ON u.id = s.user_id WHERE s.id=:sid AND s.user_id=:uid
        AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp()"""
                    + suffix
                ),
                dict(sid=session, uid=user),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise unauthorized()
    return Principal(row["id"], session, row["email"])


async def bootstrap(engine: AsyncEngine, email: str, password: str) -> UUID:
    email = normalize_email(email)
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")
    hashed = await asyncio.to_thread(hash_password, password)
    user = uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users(id,email,password_hash) VALUES(:id,:e,:p)"),
                dict(id=user, e=email, p=hashed),
            )
    except IntegrityError as error:
        raise ApiError(
            409, "EMAIL_EXISTS", "Email already exists; password was not changed"
        ) from error
    return user


async def login(
    engine: AsyncEngine, email: str, password: str, ip: str
) -> tuple[UUID, UUID, str, datetime]:
    # Commit the counter before hashing, including failed logins and concurrent API instances.
    async with engine.begin() as conn:
        await conn.execute(
            text("""DELETE FROM login_counters
            WHERE window_start < date_trunc('minute', clock_timestamp())""")
        )
        rate = (
            await conn.execute(
                text("""INSERT INTO login_counters(ip,window_start,attempts)
            VALUES(:ip,date_trunc('minute',clock_timestamp()),1)
            ON CONFLICT(ip,window_start) DO UPDATE SET attempts=login_counters.attempts+1
            RETURNING attempts"""),
                dict(ip=ip),
            )
        ).scalar_one()
    if rate > 10:
        raise ApiError(429, "LOGIN_RATE_LIMIT", "Too many login attempts; try again later")
    async with engine.connect() as conn:
        user = (
            (
                await conn.execute(
                    text("SELECT id,password_hash FROM users WHERE email=:e"), dict(e=email)
                )
            )
            .mappings()
            .first()
        )
    valid = await asyncio.to_thread(
        verify_password, password, user["password_hash"] if user else DUMMY_HASH
    )
    if not valid or user is None:
        raise unauthorized()
    session, raw = uuid4(), secrets.token_urlsafe(32)
    async with engine.begin() as conn:
        expiry = (
            await conn.execute(
                text("""INSERT INTO auth_sessions(id,user_id,expires_at)
            VALUES(:s,:u,clock_timestamp()+interval '7 days') RETURNING expires_at"""),
                dict(s=session, u=user["id"]),
            )
        ).scalar_one()
        await conn.execute(
            text("INSERT INTO refresh_tokens(token_hash,session_id) VALUES(:h,:s)"),
            dict(h=token_hash(raw), s=session),
        )
    logger.info("auth_login_succeeded session_id=%s", session)
    return user["id"], session, raw, expiry


async def refresh(engine: AsyncEngine, raw: str) -> tuple[UUID, UUID, str, datetime]:
    digest, replacement = token_hash(raw), secrets.token_urlsafe(32)
    replay = False
    async with engine.begin() as conn:
        session = (
            (
                await conn.execute(
                    text("""SELECT s.id,s.user_id,s.expires_at FROM auth_sessions s
            JOIN refresh_tokens t ON t.session_id=s.id WHERE t.token_hash=:h
            AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() FOR UPDATE OF s"""),
                    dict(h=digest),
                )
            )
            .mappings()
            .first()
        )
        if session is None:
            raise unauthorized()
        used = (
            await conn.execute(
                text("SELECT consumed_at FROM refresh_tokens WHERE token_hash=:h"), dict(h=digest)
            )
        ).scalar_one()
        if used is not None:
            await conn.execute(
                text("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=:s"),
                dict(s=session["id"]),
            )
            replay = True
        else:
            await conn.execute(
                text("UPDATE refresh_tokens SET consumed_at=clock_timestamp() WHERE token_hash=:h"),
                dict(h=digest),
            )
            await conn.execute(
                text("INSERT INTO refresh_tokens(token_hash,session_id) VALUES(:h,:s)"),
                dict(h=token_hash(replacement), s=session["id"]),
            )
    # Raising within engine.begin() would roll back the revocation.
    if replay:
        logger.warning("auth_refresh_replay session_id=%s", session["id"])
        raise unauthorized()
    return session["user_id"], session["id"], replacement, session["expires_at"]


async def logout(engine: AsyncEngine, raw: str | None) -> None:
    if raw is not None:
        async with engine.begin() as conn:
            await conn.execute(
                text("""UPDATE auth_sessions SET revoked_at=clock_timestamp()
                WHERE id=(SELECT session_id FROM refresh_tokens WHERE token_hash=:h)
                AND revoked_at IS NULL"""),
                dict(h=token_hash(raw)),
            )
