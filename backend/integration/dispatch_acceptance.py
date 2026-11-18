"""Exercise dispatch during a broker outage in an isolated Compose project."""

import asyncio
import json
import secrets
import sys
from pathlib import Path
from uuid import UUID

import aio_pika
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from flowforge.auth.service import bootstrap
from flowforge.config import Settings
from flowforge.contracts import DispatchMessage
from flowforge.outbox.broker import TASKS

RUN_FILE = Path("/tmp/flowforge-smoke-dispatch-run")


async def seed(engine: AsyncEngine) -> UUID:
    email = f"dispatch-{secrets.token_hex(8)}@example.com"
    password = secrets.token_urlsafe(24)
    await bootstrap(engine, email, password)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1") as client:
        login = await client.post(
            "/auth/login",
            headers={"X-FlowForge-CSRF": "1"},
            json={"email": email, "password": password},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        project = await client.post("/projects", json={"name": "Broker outage"})
        project.raise_for_status()
        workflow = await client.post(
            f"/projects/{project.json()['id']}/workflows", json={"name": "Dispatch smoke"}
        )
        workflow.raise_for_status()
        path = "/workflows/" + workflow.json()["id"]
        dag = json.loads(
            (Path(__file__).parents[2] / "examples/document-workflow.json").read_text()
        )
        saved = await client.put(path + "/draft", json={"expected_revision": 1, "dag": dag})
        saved.raise_for_status()
        version = await client.post(path + "/versions", json={"draft_revision": 2})
        version.raise_for_status()
        run = await client.post(
            path + "/runs",
            json={
                "version": 1,
                "input": {"document": {"title": "Outage", "content": "Example"}},
            },
        )
        assert run.status_code == 202
        return UUID(run.json()["run_id"])


async def wait_event(engine: AsyncEngine, run_id: UUID, status: str) -> UUID:
    async with asyncio.timeout(45):
        while True:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("""SELECT e.id,e.status,t.dispatch_generation,
                    t.attempt_count FROM outbox_events e JOIN task_runs t ON t.id=e.task_run_id
                    WHERE t.run_id=:run AND t.node_key='validate' AND t.status='QUEUED'"""),
                    {"run": run_id},
                )
                event = result.one_or_none()
            if event is not None and event.status == status:
                assert event.dispatch_generation == 1 and event.attempt_count == 0
                return event.id
            await asyncio.sleep(0.25)


async def verify_delivery(settings: Settings, run_id: UUID, event_id: UUID) -> None:
    connection = await aio_pika.connect(settings.rabbitmq_url, timeout=5)
    async with connection:
        channel = await connection.channel()
        queue = await channel.get_queue(TASKS)
        async with asyncio.timeout(30):
            async with queue.iterator() as messages:
                async for message in messages:
                    async with message.process():
                        if message.message_id != str(event_id):
                            continue
                        data = DispatchMessage.model_validate_json(message.body)
                        assert data.event_id == event_id and data.run_id == run_id
                        assert data.dispatch_generation == 1
                        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
                        return
    raise AssertionError("Expected dispatch was not delivered")


async def main(stage: str) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        if stage in {"outage", "backlog"}:
            run_id = await seed(engine)
            pending = await wait_event(engine, run_id, "PENDING")
            RUN_FILE.write_text(f"{run_id} {pending}", encoding="utf-8")
            print(f"PASS: scheduler committed QUEUED and pending outbox in {stage} stage")
        elif stage == "recovery":
            run_id, pending = map(UUID, RUN_FILE.read_text(encoding="utf-8").split())
            confirmed = await wait_event(engine, run_id, "PUBLISHED")
            assert confirmed == pending
            await verify_delivery(settings, run_id, confirmed)
            print("PASS: publisher confirmed and delivered the same outbox event after recovery")
        elif stage == "execution":
            run_id, pending = map(UUID, RUN_FILE.read_text(encoding="utf-8").split())
            async with asyncio.timeout(90):
                while True:
                    async with engine.connect() as conn:
                        status = await conn.scalar(
                            text("SELECT status FROM workflow_runs WHERE id=:id"), {"id": run_id}
                        )
                    assert status != "FAILED", "Fixture execution failed"
                    if status == "SUCCEEDED":
                        break
                    await asyncio.sleep(0.25)
            async with engine.connect() as conn:
                tasks = await conn.execute(
                    text("""SELECT t.node_key,t.status,t.attempt_count,
                    t.dispatch_generation,t.result_json->>'report_text' AS report
                    FROM task_runs t WHERE t.run_id=:id"""),
                    {"id": run_id},
                )
                by_node = {task.node_key: task for task in tasks}
                assert len(by_node) == 5
                assert all(
                    task.status == "SUCCEEDED"
                    and task.attempt_count == 1
                    and task.dispatch_generation == 1
                    for task in by_node.values()
                )
                assert by_node["report"].report == "Outage: 1 words, 1 lines, 7 characters"
                event = await conn.scalar(
                    text("""SELECT e.id FROM outbox_events e
                    JOIN task_runs t ON t.id=e.task_run_id WHERE t.run_id=:run
                    AND t.node_key='validate' AND e.dispatch_generation=1"""),
                    {"run": run_id},
                )
                assert event == pending
            print("PASS: worker executed migrated backlog from original dispatch to report")
        else:
            raise ValueError("Expected outage, recovery, backlog or execution")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
