"""Preserve identity/session rows when downgrading/reapplying the workflow migration.

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


def main() -> None:
    before = asyncio.run(counts())
    assert before["users"] > 0 and before["auth_sessions"] > 0
    subprocess.run(["alembic", "downgrade", "0003_auth_sessions"], check=True)
    assert asyncio.run(counts()) == before
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    assert asyncio.run(counts()) == before
    print("PASS: workflow migration downgrade/reapply preserves identity/session rows")


if __name__ == "__main__":
    main()
