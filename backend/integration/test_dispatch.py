import asyncio
from uuid import UUID

import aio_pika
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_runs import post, publish
from test_workflows import example, resource
from test_workflows import setup as setup

from flowforge.config import Settings
from flowforge.contracts import DispatchMessage
from flowforge.outbox.broker import TASK_KEY, TASKS, Broker, declare_topology
from flowforge.outbox.service import claim_next, finish, preflight, publish_next
from flowforge.scheduler.service import reconcile, redrive


@pytest_asyncio.fixture
async def dispatch(setup):
    client, engine, first, *_ = setup
    auth, _, workflow = await resource(client, first)
    workflow_id = UUID(workflow.rsplit("/", 1)[1])
    try:
        yield client, engine, auth, workflow
    finally:
        async with engine.begin() as conn:
            params = {"workflow": workflow_id}
            await conn.execute(
                text("""DELETE FROM task_attempts a USING task_runs t,
                workflow_runs r WHERE a.task_run_id=t.id AND t.run_id=r.id
                AND r.workflow_id=:workflow"""),
                params,
            )
            await conn.execute(
                text("""DELETE FROM outbox_events e USING task_runs t,
                workflow_runs r WHERE e.task_run_id=t.id AND t.run_id=r.id
                AND r.workflow_id=:workflow"""),
                params,
            )
            for table in ("task_dependencies", "task_runs"):
                await conn.execute(
                    text(f"""DELETE FROM {table} USING workflow_runs r
                    WHERE {table}.run_id=r.id AND r.workflow_id=:workflow"""),
                    params,
                )
            await conn.execute(
                text("DELETE FROM workflow_runs WHERE workflow_id=:workflow"), params
            )


async def make_run(dispatch, dag=None):
    client, _, auth, workflow = dispatch
    await publish(client, auth, workflow, dag if dag is not None else example())
    response = await post(client, auth, workflow)
    assert response.status_code == 202, response.text
    return UUID(response.json()["run_id"])


async def state(engine, run):
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("""SELECT node_key,status,dispatch_generation,attempt_count
            FROM task_runs WHERE run_id=:run"""),
            {"run": run},
        )
        return {row.node_key: tuple(row)[1:] for row in rows}


async def run_state(engine, run):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("""SELECT status,started_at,finished_at
            FROM workflow_runs WHERE id=:run"""),
                {"run": run},
            )
        ).one()


async def complete(engine, run, node, status):
    # Fixtures stand in for worker commits and acquire the same run lock.
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT id FROM workflow_runs WHERE id=:run FOR UPDATE"), {"run": run}
        )
        await conn.execute(
            text("""UPDATE task_runs SET status=:status
            WHERE run_id=:run AND node_key=:node AND status='QUEUED'"""),
            {"run": run, "node": node, "status": status},
        )


async def events(engine, run):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("""SELECT e.* FROM outbox_events e
            JOIN task_runs t ON t.id=e.task_run_id WHERE t.run_id=:run ORDER BY t.node_key"""),
                {"run": run},
            )
        ).all()


@pytest.mark.parametrize("failed", [False, True])
async def test_fork_join_and_aggregation(dispatch, failed):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    assert sum(await asyncio.gather(reconcile(engine), reconcile(engine))) == 1
    assert len(await events(engine, run)) == 1
    started = await run_state(engine, run)
    assert started.status == "RUNNING" and started.started_at and started.finished_at is None
    assert (await state(engine, run))["validate"] == ("QUEUED", 1, 0)
    await complete(engine, run, "validate", "SUCCEEDED")
    assert await reconcile(engine) == 2
    result = await state(engine, run)
    assert result["aggregate"] == ("PENDING", 0, 0)
    assert result["extract"] == result["analyze"] == ("QUEUED", 1, 0)
    assert (await run_state(engine, run)).started_at == started.started_at
    await complete(engine, run, "extract", "FAILED" if failed else "SUCCEEDED")
    if failed:
        assert await reconcile(engine) == 1
        assert await reconcile(engine) == 1
        result = await state(engine, run)
        assert result["aggregate"] == result["report"] == ("SKIPPED", 0, 0)
        assert result["analyze"][0] == "QUEUED"
        assert (await run_state(engine, run)).status == "RUNNING"
    else:
        assert await reconcile(engine) == 0
    await complete(engine, run, "analyze", "SUCCEEDED")
    if not failed:
        assert await reconcile(engine) == 1
        await complete(engine, run, "aggregate", "SUCCEEDED")
        assert await reconcile(engine) == 1
        await complete(engine, run, "report", "SUCCEEDED")
    assert await reconcile(engine) == 1
    finished = await run_state(engine, run)
    assert finished.status == ("FAILED" if failed else "SUCCEEDED")
    assert finished.finished_at >= finished.started_at
    assert len(await events(engine, run)) == (3 if failed else 5)
    assert await reconcile(engine) == 0
    assert await run_state(engine, run) == finished


async def test_large_batch_and_dispatch_rollback(dispatch):
    _, engine, _, _ = dispatch
    root = example()["nodes"][0]
    dag = {**example(), "nodes": [{**root, "key": f"node_{n}"} for n in range(52)], "edges": []}
    run = await make_run(dispatch, dag)
    async with engine.begin() as conn:
        await conn.execute(
            text("""CREATE FUNCTION fail_outbox_fixture() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'fixture'; END $$ LANGUAGE plpgsql""")
        )
        await conn.execute(
            text("""CREATE TRIGGER fail_outbox_fixture BEFORE INSERT ON outbox_events
          FOR EACH ROW EXECUTE FUNCTION fail_outbox_fixture()""")
        )
    try:
        with pytest.raises(DBAPIError):
            await reconcile(engine)
        assert {x for x in (await state(engine, run)).values()} == {("PENDING", 0, 0)}
        assert (await run_state(engine, run)).status == "PENDING"
        assert await events(engine, run) == []
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER fail_outbox_fixture ON outbox_events"))
            await conn.execute(text("DROP FUNCTION fail_outbox_fixture()"))
    assert await reconcile(engine) == 50
    assert await reconcile(engine) == 2
    assert len(await events(engine, run)) == 52


async def test_lease_fencing_redrive_and_obsolete(dispatch):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    await reconcile(engine)
    claims = await asyncio.gather(claim_next(engine), claim_next(engine))
    assert sum(c is not None for c in claims) == 1
    original = next(c for c in claims if c is not None)
    event = await preflight(engine, original)
    assert event.event_id == original.event_id
    # Simulate broker confirm followed by publisher death before persisting it.
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE outbox_events SET claim_until=clock_timestamp()-interval '1 second'
            WHERE id=:id"""),
            {"id": event.event_id},
        )
    assert await preflight(engine, original) is None
    recovered = await claim_next(engine)
    assert recovered.event_id == original.event_id and recovered.token != original.token
    assert await preflight(engine, recovered) == event
    assert not await finish(engine, original, True)
    assert await finish(engine, recovered, True)
    assert await redrive(engine) == 0
    params = {"id": event.event_id}
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE outbox_events SET published_at=clock_timestamp()-interval '61 seconds'
            WHERE id=:id"""),
            params,
        )
    assert sum(await asyncio.gather(redrive(engine), redrive(engine))) == 1
    assert await redrive(engine) == 0
    (row,) = await events(engine, run)
    assert row.id == event.event_id and row.status == "PENDING" and row.last_redriven_at
    assert (await state(engine, run))["validate"] == ("QUEUED", 1, 0)
    next_claim = await claim_next(engine)
    assert await preflight(engine, next_claim) == event
    assert await finish(engine, next_claim, True)
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE outbox_events SET published_at=clock_timestamp()-interval '61 seconds'
            WHERE id=:id"""),
            params,
        )
    assert await redrive(engine) == 0  # last redrive still recent
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE outbox_events SET last_redriven_at=published_at
            WHERE id=:id"""),
            params,
        )
    assert await redrive(engine) == 1
    await complete(engine, run, "validate", "SUCCEEDED")
    assert await publish_next(engine, unexpected_send)
    assert (await events(engine, run))[0].status == "OBSOLETE"


async def unexpected_send(event):
    pytest.fail("The invalid dispatch must not be sent")


@pytest.mark.parametrize("failure", ["nack", "timeout"])
async def test_retry_is_durable(dispatch, failure):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    await reconcile(engine)
    sent = []

    async def send(event):
        sent.append(event)
        if failure == "timeout":
            raise TimeoutError
        return False

    assert await publish_next(engine, send)
    (row,) = await events(engine, run)
    assert row.status == "PENDING" and row.claim_token is None
    assert row.publish_attempts == 1
    assert await claim_next(engine) is None
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE outbox_events SET available_at=clock_timestamp()
            WHERE id=:id"""),
            {"id": row.id},
        )

    async def confirm(event):
        assert event == sent[0]
        return True

    assert await publish_next(engine, confirm)
    assert not await publish_next(engine, unexpected_send)
    assert (await events(engine, run))[0].status == "PUBLISHED"


async def test_real_confirm_and_mandatory_return(dispatch):
    _, engine, _, _ = dispatch
    run = await make_run(dispatch)
    await reconcile(engine)
    broker = Broker(Settings().rabbitmq_url)
    try:
        await broker.ensure()
        queue = await broker.channel.get_queue(TASKS)
        await queue.unbind(broker.exchange, TASK_KEY)
        try:
            assert await publish_next(engine, broker.send)
            (row,) = await events(engine, run)
            assert row.status == "PENDING" and row.published_at is None
        finally:
            await queue.bind(broker.exchange, TASK_KEY)

        async with engine.begin() as conn:
            await conn.execute(
                text("""UPDATE outbox_events SET available_at=clock_timestamp()
                WHERE id=:id"""),
                {"id": row.id},
            )
        await broker.close()
        assert await publish_next(engine, broker.send)
        queue = await broker.channel.get_queue(TASKS)
        message = await queue.get(timeout=5)
        async with message.process():
            parsed = DispatchMessage.model_validate_json(message.body)
            assert message.message_id == str(parsed.event_id) == str(row.id)
            assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
            assert parsed.run_id == run and parsed.dispatch_generation == 1
            assert set(parsed.model_dump(exclude_none=True)) == {
                "schema_version",
                "event_id",
                "run_id",
                "task_run_id",
                "dispatch_generation",
            }
        assert (await events(engine, run))[0].status == "PUBLISHED"
        await declare_topology(broker.channel)
    finally:
        await broker.close()
