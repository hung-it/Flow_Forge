"""Run explicitly against a disposable migrated PostgreSQL database."""
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from flowforge.config import Settings


@pytest_asyncio.fixture
async def connection():
    url = Settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


async def test_unique_email(connection):
    email = f"{uuid4()}@example.test"
    await connection.execute(
        "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')", uuid4(), email
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')", uuid4(), email
        )


async def test_project_requires_owner(connection):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await connection.execute(
            "INSERT INTO projects(id,owner_id,name) VALUES($1,$2,'test')", uuid4(), uuid4()
        )


async def test_membership_role_constraint(connection):
    user_id, project_id = uuid4(), uuid4()
    await connection.execute(
        "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
        user_id, f"{user_id}@example.test",
    )
    await connection.execute(
        "INSERT INTO projects(id,owner_id,name) VALUES($1,$2,'test')", project_id, user_id
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute(
            "INSERT INTO project_members(project_id,user_id,role) VALUES($1,$2,'INVALID')",
            project_id, user_id,
        )


@pytest.mark.parametrize("email", ["UPPER@example.test", " space@example.test ", ""])
async def test_email_requires_canonical_form(connection, email):
    with pytest.raises(asyncpg.CheckViolationError):
        await connection.execute(
            "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')", uuid4(), email
        )


async def test_project_cannot_have_two_owner_memberships(connection):
    first, second, project = uuid4(), uuid4(), uuid4()
    for user in (first, second):
        await connection.execute(
            "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
            user, f"{user}@example.test",
        )
    await connection.execute(
        "INSERT INTO projects(id,owner_id,name) VALUES($1,$2,'test')", project, first
    )
    await connection.execute(
        "INSERT INTO project_members(project_id,user_id,role) VALUES($1,$2,'OWNER')", project, first
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            "INSERT INTO project_members(project_id,user_id,role) VALUES($1,$2,'OWNER')",
            project, second,
        )
