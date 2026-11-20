import asyncio
import signal
import sys
from contextlib import suppress
from pathlib import Path

import aio_pika
import pytest
from pamqp.commands import Basic
from test_dispatch import dispatch as dispatch
from test_dispatch import make_run, run_state
from test_execution_worker import attempts, event_for, row
from test_workflows import example
from test_workflows import setup as setup

from flowforge.config import Settings
from flowforge.outbox.service import publish_next
from flowforge.scheduler.service import reconcile
from flowforge.worker.consumer import handle
from flowforge.worker.service import recover


async def wait_for_message(queue):
    async with asyncio.timeout(15):
        while True:
            delivery = await queue.get(fail=False, timeout=5)
            if delivery is not None:
                return delivery
            await asyncio.sleep(0.1)


async def wait_for_recovery(engine, timeout):
    async with asyncio.timeout(timeout):
        while not await recover(engine):
            await asyncio.sleep(0.2)


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
async def test_worker_crash_and_redelivery(dispatch, boundary):
    _, engine, _, _ = dispatch
    dag = example()
    root = {**dag["nodes"][0], "timeout_seconds": 90, "retry": {"max_attempts": 2}}
    run = await make_run(dispatch, {**dag, "nodes": [root], "edges": []})
    assert await reconcile(engine) == 1
    original = await event_for(engine, run)
    connection = await aio_pika.connect(Settings().rabbitmq_url, timeout=5)
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        # The child and replacement share this queue without touching other tests' deliveries.
        queue = await channel.declare_queue()
        try:
            async def send(event):
                confirmation = await channel.default_exchange.publish(
                    aio_pika.Message(
                        event.model_dump_json(exclude_none=True).encode(),
                        message_id=str(event.event_id),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=queue.name,
                    mandatory=True,
                    timeout=5,
                )
                return isinstance(confirmation, Basic.Ack)

            assert await publish_next(engine, send)
            child = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("worker_crash_fixture.py")),
                queue.name, boundary,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                marker = await asyncio.wait_for(child.stdout.readline(), 20)
                assert marker.strip() == b"fault-boundary", marker.decode(errors="replace")
                committed = await row(engine, original.task_run_id)
                assert committed.attempt_count == 1
                before_commit = boundary == "before_commit"
                assert committed.status == ("RUNNING" if before_commit else "SUCCEEDED")
                assert (committed.result_json is None) == before_commit
            finally:
                if child.returncode is None:
                    with suppress(ProcessLookupError):
                        child.kill()
                await asyncio.wait_for(child.communicate(), 5)
            assert child.returncode == -signal.SIGKILL

            redelivered = await wait_for_message(queue)
            assert redelivered.redelivered and redelivered.message_id == str(original.event_id)
            assert original == original.model_validate_json(redelivered.body)

            async def no_rerun(*args):
                pytest.fail("Original delivery executed again")

            if before_commit:
                await wait_for_recovery(engine, 45)
                assert (await row(engine, original.task_run_id)).status == "RETRY_WAIT"
                assert await attempts(engine, original.task_run_id) == [("LOST", "WORKER_LOST", 1)]
            await handle(
                engine, "replacement", redelivered, asyncio.Event(), lambda: None, no_rerun
            )
            assert redelivered.processed
            if before_commit:
                await wait_for_recovery(engine, 10)
                assert await reconcile(engine) == 1
                fresh = await event_for(engine, run)
                assert fresh.event_id != original.event_id and fresh.dispatch_generation == 2
                assert await publish_next(engine, send)
                retry = await wait_for_message(queue)
                assert retry.message_id == str(fresh.event_id)
                await handle(engine, "replacement", retry, asyncio.Event(), lambda: None)
                assert retry.processed
                assert await attempts(engine, original.task_run_id) == [
                    ("LOST", "WORKER_LOST", 1), ("SUCCEEDED", None, 2)
                ]
            else:
                assert await row(engine, original.task_run_id) == committed
                assert await attempts(engine, original.task_run_id) == [("SUCCEEDED", None, 1)]
            assert await reconcile(engine) == 1
            assert (await run_state(engine, run)).status == "SUCCEEDED"
            assert await queue.get(fail=False, timeout=5) is None
        finally:
            await queue.delete(if_unused=False, if_empty=False, timeout=5)
