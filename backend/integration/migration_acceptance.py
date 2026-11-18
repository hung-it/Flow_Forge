"""Preserve existing rows across workflow and run migration round trips.

Only run in the disposable smoke stack, after stopping API/background writers.
"""

import asyncio
import subprocess

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.config import Settings


async def counts() -> dict[str, int]:
    engine = create_async_engine(Settings().database_url)
    try:
        async with engine.connect() as conn:
            return {
                table: int((await conn.scalar(text("SELECT count(*) FROM " + table))) or 0)
                for table in ("users", "projects", "project_members", "auth_sessions")
            }
    finally:
        await engine.dispose()


async def snapshots() -> dict[str, list[str]]:
    engine = create_async_engine(Settings().database_url)
    tables = (
        "users",
        "projects",
        "project_members",
        "auth_sessions",
        "refresh_tokens",
        "login_counters",
        "workflow_definitions",
        "workflow_drafts",
        "workflow_versions",
    )
    try:
        async with engine.connect() as conn:
            return {
                table: list(
                    (
                        await conn.execute(
                            text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY 1")
                        )
                    ).scalars()
                )
                for table in tables
            }
    finally:
        await engine.dispose()


def main() -> None:
    saved = asyncio.run(snapshots())
    assert all(
        saved[table] for table in ("users", "projects", "workflow_drafts", "workflow_versions")
    )
    subprocess.run(["alembic", "downgrade", "0004_workflows"], check=True)
    assert asyncio.run(snapshots()) == saved
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    assert asyncio.run(snapshots()) == saved
    print("PASS: run migration round trip preserves identity/project/draft/version rows")
    before = asyncio.run(counts())
    assert before["users"] > 0 and before["auth_sessions"] > 0
    subprocess.run(["alembic", "downgrade", "0003_auth_sessions"], check=True)
    assert asyncio.run(counts()) == before
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    assert asyncio.run(counts()) == before
    print("PASS: workflow migration downgrade/reapply preserves identity/session rows")


if __name__ == "__main__":
    main()
