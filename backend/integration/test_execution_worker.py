import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import aio_pika
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_dispatch import dispatch as dispatch
from test_dispatch import events, make_run, run_state
from test_runs import post, publish
from test_workflows import example
from test_workflows import setup as setup

from flowforge.config import Settings
from flowforge.contracts import DispatchMessage
from flowforge.outbox.broker import DEAD, TASK_KEY, Broker
from flowforge.outbox.service import publish_next
from flowforge.scheduler.service import reconcile
from flowforge.worker import consumer
from flowforge.worker.service import Claim, Disposition, claim_task, finish, recover, renew


async def row(engine, task):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("""SELECT status,lease_token,lease_until,hard_deadline,
            attempt_count,dispatch_generation,next_run_at,result_json
            FROM task_runs WHERE id=:id"""),
                {"id": task},
            )
        ).one()


async def attempts(engine, task):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("""SELECT status,error_code,dispatch_generation
            FROM task_attempts WHERE task_run_id=:id ORDER BY attempt_no"""),
                {"id": task},
            )
        ).all()


async def event_for(engine, run):
    entries = await events(engine, run)
    e = max(entries, key=lambda row: row.dispatch_generation)
    return DispatchMessage(
        schema_version=1,
        event_id=e.id,
        run_id=run,
        task_run_id=e.task_run_id,
        dispatch_generation=e.dispatch_generation,
    )


@pytest.mark.parametrize("deadline_expired", [False, True])
async def test_concurrent_claim_fencing_and_recovery(dispatch, deadline_expired):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    assert (
        await claim_task(engine, event.model_copy(update={"event_id": uuid4()}), "invalid")
        is Disposition.REJECT
    )
    claims = await asyncio.gather(*(claim_task(engine, event, str(n)) for n in range(4)))
    (claim,) = [c for c in claims if isinstance(c, Claim)]
    assert claims.count(Disposition.ACK) == 3
    assert await renew(engine, claim) is not None
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE task_runs SET lease_until=clock_timestamp()+interval '1 second',
            hard_deadline=clock_timestamp()+interval '8 seconds' WHERE id=:id"""),
            {"id": claim.task_id},
        )
    assert await renew(engine, claim) == (await row(engine, claim.task_id)).hard_deadline
    async with engine.begin() as conn:
        deadline = (
            ",hard_deadline=clock_timestamp()-interval '1 second'" if deadline_expired else ""
        )
        await conn.execute(
            text(f"""UPDATE task_runs SET lease_until=
            clock_timestamp()-interval '2 seconds'{deadline} WHERE id=:id"""),
            {"id": claim.task_id},
        )
    assert await claim_task(engine, event, "late") is Disposition.DEFER
    assert await renew(engine, claim) is None
    assert not await finish(engine, claim, "{}", None, False)
    assert sum(await asyncio.gather(recover(engine), recover(engine))) == 1
    expected = ("FAILED", "TASK_TIMEOUT") if deadline_expired else ("LOST", "WORKER_LOST")
    assert tuple((await attempts(engine, claim.task_id))[0])[:2] == expected
    recovering = await row(engine, claim.task_id)
    assert recovering.status == "RETRY_WAIT" and recovering.next_run_at
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE task_runs SET next_run_at=clock_timestamp()-interval '1 second'
            WHERE id=:id"""),
            {"id": claim.task_id},
        )
    assert await recover(engine) == 1
    assert await reconcile(engine) == 1
    assert await claim_task(engine, event, "stale") is Disposition.ACK
    next_event = await event_for(engine, run)
    fresh = await claim_task(engine, next_event, "replacement")
    assert isinstance(fresh, Claim) and fresh.attempt == 2 and fresh.generation == 2
    assert not await finish(engine, claim, "{}", None, False)
    assert await finish(engine, fresh, "{}", None, False)


async def test_claim_rolls_back_when_attempt_insert_fails(dispatch):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    await reconcile(engine)
    event = await event_for(engine, run)
    async with engine.begin() as conn:
        await conn.execute(
            text("""CREATE FUNCTION worker_attempt_fixture() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$""")
        )
        await conn.execute(
            text("""CREATE TRIGGER worker_attempt_fixture BEFORE INSERT ON task_attempts
            FOR EACH ROW EXECUTE FUNCTION worker_attempt_fixture()""")
        )
    try:
        with pytest.raises(DBAPIError):
            await claim_task(engine, event, "rollback")
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER worker_attempt_fixture ON task_attempts"))
            await conn.execute(text("DROP FUNCTION worker_attempt_fixture()"))
    task = await row(engine, event.task_run_id)
    assert task.status == "QUEUED" and task.attempt_count == 0 and task.lease_token is None
    assert await attempts(engine, event.task_run_id) == []


@pytest.mark.parametrize("failure_boundary", ["ack", "commit", "db"])
async def test_commit_before_ack_and_redelivery(dispatch, monkeypatch, failure_boundary):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    await reconcile(engine)
    event = await event_for(engine, run)
    acked = []

    async def ack():
        task = await row(engine, event.task_run_id)
        assert task.status == "SUCCEEDED" and task.result_json
        acked.append(True)
        if failure_boundary == "ack" and len(acked) == 1:
            raise OSError("fixture lost ACK")

    async def reject(*, requeue):
        pytest.fail("Valid delivery was rejected")

    delivery = SimpleNamespace(body=event.model_dump_json().encode(), ack=ack, reject=reject)

    async def fail_commit(engine, claim, result, error, retryable):
        if failure_boundary == "commit":
            assert await finish(engine, claim, result, error, retryable)
        raise OSError("fixture database disconnected")

    with monkeypatch.context() as patch:
        if failure_boundary != "ack":
            patch.setattr(consumer, "finish", fail_commit)
        with pytest.raises(OSError):
            await consumer.handle(engine, "worker", delivery, asyncio.Event(), lambda: None)
    assert len(acked) == (1 if failure_boundary == "ack" else 0)
    if failure_boundary == "db":
        assert (await row(engine, event.task_run_id)).status == "RUNNING"
        return
    await consumer.handle(engine, "worker2", delivery, asyncio.Event(), lambda: None)
    assert len(acked) == (2 if failure_boundary == "ack" else 1)
    assert await attempts(engine, event.task_run_id) == [("SUCCEEDED", None, 1)]


@pytest.mark.parametrize("failure", ["temporary", "binding", "output"])
async def test_permanent_failure_and_retry_budget(dispatch, monkeypatch, failure):
    client, engine, auth, workflow = dispatch
    spec = example()["nodes"][0]
    spec["retry"] = {"max_attempts": 1}
    dag = {**example(), "nodes": [spec], "edges": []}
    if failure == "binding":
        await publish(client, auth, workflow, dag)
        result = await post(client, auth, workflow, {"version": 1, "input": {"document": 42}})
        run = UUID(result.json()["run_id"])
    else:
        run = await make_run(dispatch, dag)
    await reconcile(engine)
    event = await event_for(engine, run)
    acked = []

    async def ack():
        assert (await row(engine, event.task_run_id)).status == "FAILED"
        acked.append(True)

    async def fail(*args):
        raise consumer.HandlerFailure("fixture")

    message = SimpleNamespace(body=event.model_dump_json().encode(), ack=ack)
    if failure == "output":
        monkeypatch.setattr(consumer, "MAX_OUTPUT", 1)
    if failure == "temporary":
        await consumer.handle(engine, "a", message, asyncio.Event(), lambda: None, fail)
    else:
        await consumer.handle(engine, "a", message, asyncio.Event(), lambda: None)
    assert acked == [True]
    error = {
        "temporary": "HANDLER_FAILED",
        "binding": "INPUT_BINDING_INVALID",
        "output": "OUTPUT_INVALID",
    }[failure]
    assert await attempts(engine, event.task_run_id) == [("FAILED", error, 1)]
    assert await reconcile(engine) == 1
    assert (await run_state(engine, run)).status == "FAILED"


@pytest.mark.parametrize("linear", [False, True])
async def test_four_real_consumers_run_graph_and_dead_letter(dispatch, linear):
    _, engine, _, _ = dispatch
    dag = example()
    if linear:
        dag = {**dag, "nodes": dag["nodes"][:2], "edges": [["validate", "extract"]]}
    run = await make_run(dispatch, dag)
    assert await reconcile(engine) == 1
    root = await event_for(engine, run)
    broker = Broker(Settings().rabbitmq_url)
    stop = asyncio.Event()
    consumers = [
        asyncio.create_task(
            consumer.consume(engine, Settings().rabbitmq_url, str(i), stop, lambda healthy: None)
        )
        for i in range(4)
    ]
    try:
        await broker.ensure()
        assert await publish_next(engine, broker.send)
        await asyncio.gather(*(broker.send(root) for _ in range(7)))
        for payload in (b"{", b"x" * (16 * 1024 + 1)):
            await broker.exchange.publish(aio_pika.Message(payload), routing_key=TASK_KEY)
        async with asyncio.timeout(20):
            while (await run_state(engine, run)).status == "RUNNING":
                await reconcile(engine)
                while await publish_next(engine, broker.send):
                    pass
                await asyncio.sleep(0.05)
        assert (await run_state(engine, run)).status == "SUCCEEDED"
        async with engine.connect() as conn:
            attempts_count = await conn.scalar(
                text("""SELECT count(*) FROM task_attempts a
                JOIN task_runs t ON a.task_run_id=t.id WHERE t.run_id=:id"""),
                {"id": run},
            )
            last = await conn.scalar(
                text("""SELECT result_json->>:field FROM task_runs
                WHERE run_id=:id AND node_key=:node"""),
                {
                    "id": run,
                    "node": "extract" if linear else "report",
                    "field": "character_count" if linear else "report_text",
                },
            )
        assert attempts_count == len(dag["nodes"])
        assert last == ("7" if linear else "FlowForge: 1 words, 1 lines, 7 characters")
        dead = await broker.channel.get_queue(DEAD)
        async with asyncio.timeout(10):
            async with dead.iterator() as messages:
                bodies = []
                for _ in range(2):
                    msg = await anext(messages)
                    bodies.append(msg.body)
                    await msg.ack()
        assert set(bodies) == {b"{", b"x" * (16 * 1024 + 1)}
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.gather(*consumers), 12)
        await broker.close()
