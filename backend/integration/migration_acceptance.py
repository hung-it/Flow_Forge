"""Preserve existing rows across workflow and run migration round trips.

Only run in the disposable smoke stack, after stopping API/background writers.
"""

import asyncio
import subprocess
import sys

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


async def snapshots(
    include_runs: bool = False,
    *,
    include_outbox: bool = False,
) -> dict[str, list[str]]:
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
    if include_runs:
        tables += ("workflow_runs", "task_runs", "task_dependencies")
    if include_outbox:
        tables += ("outbox_events",)
    try:
        async with engine.connect() as conn:
            return {
                table: list(
                    (
                        await conn.execute(
                            text(f"""SELECT (to_jsonb(t) - ARRAY[
                                'dispatch_generation','lease_token','lease_until',
                                'hard_deadline','next_run_at','result_json'])::text
                                FROM {table} t ORDER BY 1""")
                        )
                    ).scalars()
                )
                for table in tables
            }
    finally:
        await engine.dispose()


def main(worker_only: bool = False) -> None:
    if worker_only:
        backlog = asyncio.run(snapshots(include_runs=True, include_outbox=True))
        assert any('"status": "PENDING"' in row for row in backlog["outbox_events"])
        assert all(backlog[table] for table in ("workflow_runs", "task_runs", "outbox_events"))
        subprocess.run(["alembic", "downgrade", "0006_scheduler_outbox"], check=True)
        assert asyncio.run(snapshots(include_runs=True, include_outbox=True)) == backlog
        subprocess.run(["alembic", "upgrade", "head"], check=True)
        assert asyncio.run(snapshots(include_runs=True, include_outbox=True)) == backlog
        print("PASS: worker migration round trip preserves queued tasks and pending outbox")
        return
    existing_runs = asyncio.run(snapshots(include_runs=True))
    assert all(
        existing_runs[table] for table in ("workflow_runs", "task_runs", "task_dependencies")
    )
    subprocess.run(["alembic", "downgrade", "0005_runs"], check=True)
    assert asyncio.run(snapshots(include_runs=True)) == existing_runs
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    assert asyncio.run(snapshots(include_runs=True)) == existing_runs
    print("PASS: scheduler migration round trip preserves run/task/dependency rows")
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
    main(sys.argv[1:] == ["worker"])
