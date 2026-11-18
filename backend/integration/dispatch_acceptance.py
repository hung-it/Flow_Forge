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
        if stage == "outage":
            run_id = await seed(engine)
            pending = await wait_event(engine, run_id, "PENDING")
            RUN_FILE.write_text(f"{run_id} {pending}", encoding="utf-8")
            print("PASS: scheduler committed QUEUED and pending outbox during broker outage")
        elif stage == "recovery":
            run_id, pending = map(UUID, RUN_FILE.read_text(encoding="utf-8").split())
            confirmed = await wait_event(engine, run_id, "PUBLISHED")
            assert confirmed == pending
            await verify_delivery(settings, run_id, confirmed)
            print("PASS: publisher confirmed and delivered the same outbox event after recovery")
        else:
            raise ValueError("Expected outage or recovery")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
