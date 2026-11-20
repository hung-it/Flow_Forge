import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_dispatch import dispatch as dispatch
from test_dispatch import events, make_run, run_state
from test_execution_worker import attempts, event_for, row
from test_runs import post, publish
from test_workflows import example, sign_in
from test_workflows import setup as setup

from flowforge.config import Settings
from flowforge.outbox.broker import TASKS, Broker
from flowforge.outbox.service import claim_next, preflight, publish_next
from flowforge.outbox.service import finish as finish_publish
from flowforge.runs import service as runs
from flowforge.scheduler.service import reconcile, redrive
from flowforge.worker import consumer
from flowforge.worker.consumer import handle
from flowforge.worker.service import Claim, Disposition, claim_task, finish, recover, renew


async def test_cancel_pending_and_replay(dispatch):
    client, _, auth, workflow = dispatch
    await publish(client, auth, workflow, example())
    created = await post(client, auth, workflow)
    path = created.headers["Location"]
    responses = await asyncio.gather(
        client.post(path + "/cancel", headers=auth), client.post(path + "/cancel", headers=auth)
    )
    assert [response.status_code for response in responses] == [200, 200]
    canceled = responses[0].json()
    assert canceled == responses[1].json() == (await client.get(path, headers=auth)).json()
    assert canceled["status"] == "CANCELED"
    assert canceled["started_at"] is None and canceled["finished_at"] is not None
    tasks = (await client.get(path + "/tasks", headers=auth)).json()
    assert len(tasks) == 5
    assert all(task["status"] == "CANCELED" and task["attempt_count"] == 0 for task in tasks)
    replay = await post(client, auth, workflow)
    assert replay.status_code == 202
    assert replay.json() == {"run_id": canceled["run_id"], "status": "CANCELED"}


async def test_cancel_permissions(dispatch, setup):
    client, _, auth, workflow = dispatch
    _, _, _, second, *_ = setup
    metadata = (await client.get(workflow, headers=auth)).json()
    project = "/api/v1/projects/" + metadata["project_id"]
    await publish(client, auth, workflow, example())
    path = (await post(client, auth, workflow)).headers["Location"]
    other = await sign_in(client, second)
    assert (await client.post(path + "/cancel")).status_code == 401
    assert (await client.post(path + "/cancel", headers=other)).status_code == 404
    assert (await client.post(f"/api/v1/runs/{uuid4()}/cancel", headers=auth)).status_code == 404
    member = await client.post(
        project + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"}
    )
    assert member.status_code == 201
    assert (await client.post(path + "/cancel", headers=other)).status_code == 403
    own = (await post(client, other, workflow, key=None)).headers["Location"]
    assert (await client.post(own + "/cancel", headers=other)).json()["status"] == "CANCELED"
    second_run = (await post(client, other, workflow, key=None)).headers["Location"]
    assert (await client.post(second_run + "/cancel", headers=auth)).json()["status"] == "CANCELED"
    third_run = (await post(client, other, workflow, key=None)).headers["Location"]
    member_path = project + "/members/" + member.json()["user_id"]
    demoted = await client.patch(member_path, headers=auth, json={"role": "VIEWER"})
    assert demoted.status_code == 200
    assert (await client.post(third_run + "/cancel", headers=other)).status_code == 403


async def wait_for_lock(engine, query):
    async with asyncio.timeout(10):
        while True:
            async with engine.connect() as observer:
                waiting = await observer.scalar(
                    text("""SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                    WHERE datname=current_database() AND wait_event_type='Lock'
                    AND query LIKE :query)"""),
                    {"query": query},
                )
            if waiting:
                return
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("revoked", ["membership", "session"])
async def test_cancel_rechecks_access_under_locks(dispatch, setup, revoked):
    client, engine, auth, workflow = dispatch
    _, _, _, second, _, member_id = setup
    project_id = UUID((await client.get(workflow, headers=auth)).json()["project_id"])
    added = await client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=auth,
        json={"email": second, "role": "DEVELOPER"},
    )
    assert added.status_code == 201
    await publish(client, auth, workflow, example())
    member = await sign_in(client, second)
    created = await post(client, member, workflow)
    assert created.status_code == 202
    path = created.headers["Location"]
    async with engine.begin() as conn:
        if revoked == "membership":
            await conn.execute(
                text("SELECT id FROM projects WHERE id=:id FOR UPDATE"), {"id": project_id}
            )
            query = "SELECT id FROM projects WHERE id=%FOR UPDATE%"
            revoke = "DELETE FROM project_members WHERE project_id=:project AND user_id=:member"
            expected = 404
        else:
            await conn.execute(
                text("SELECT id FROM auth_sessions WHERE user_id=:member FOR UPDATE"),
                {"member": member_id},
            )
            query = "SELECT u.id, u.email FROM auth_sessions s%FOR UPDATE OF s"
            revoke = "UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE user_id=:member"
            expected = 401
        pending = asyncio.create_task(client.post(path + "/cancel", headers=member))
        try:
            await wait_for_lock(engine, query)
            await conn.execute(text(revoke), {"project": project_id, "member": member_id})
        except (TimeoutError, asyncio.CancelledError):
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise
    assert (await asyncio.wait_for(pending, 10)).status_code == expected
    assert (await client.get(path, headers=auth)).json()["status"] == "PENDING"


@pytest.mark.parametrize("state", ["READY", "QUEUED", "RUNNING", "RETRY_WAIT"])
async def test_cancel_active_task_states(dispatch, state):
    client, engine, auth, _ = dispatch
    run = await make_run(dispatch)
    if state == "READY":
        async with engine.begin() as conn:
            await conn.execute(
                text("""UPDATE task_runs SET status='READY'
                WHERE run_id=:run AND node_key='validate'"""),
                {"run": run},
            )
    else:
        assert await reconcile(engine) == 1
    claim = None
    if state in ("RUNNING", "RETRY_WAIT"):
        event = await event_for(engine, run)
        claim = await claim_task(engine, event, "worker")
        assert isinstance(claim, Claim)
        if state == "RETRY_WAIT":
            assert await finish(engine, claim, None, "HANDLER_FAILED", True)

    path = f"/api/v1/runs/{run}"
    before = await run_state(engine, run)
    result = await client.post(path + "/cancel", headers=auth)
    assert result.status_code == 200 and result.json()["status"] == "CANCELED"
    assert (await run_state(engine, run)).started_at == before.started_at
    tasks = (await client.get(path + "/tasks", headers=auth)).json()
    assert len(tasks) == 5 and {task["status"] for task in tasks} == {"CANCELED"}
    stored_events = await events(engine, run)
    assert len(stored_events) == (0 if state == "READY" else 1)
    assert all(e.status == "OBSOLETE" and e.claim_token is None for e in stored_events)
    if claim is not None:
        stored = await row(engine, claim.task_id)
        assert stored.attempt_count == stored.dispatch_generation == 1
        assert stored.lease_token is stored.lease_until is stored.hard_deadline is None
        assert stored.next_run_at is stored.result_json is None
        expected = ("CANCELED", None, 1) if state == "RUNNING" else ("FAILED", "HANDLER_FAILED", 1)
        assert await attempts(engine, claim.task_id) == [expected]
        assert await claim_task(engine, event, "late") is Disposition.ACK
    assert await recover(engine) == await reconcile(engine) == await redrive(engine) == 0


@pytest.mark.parametrize("recovery_first", [True, False])
async def test_cancel_and_due_retry(dispatch, recovery_first):
    client, engine, auth, _ = dispatch
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    claim = await claim_task(engine, event, "worker")
    assert isinstance(claim, Claim)
    assert await finish(engine, claim, None, "HANDLER_FAILED", True)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_runs SET next_run_at=clock_timestamp() WHERE id=:id"),
            {"id": claim.task_id},
        )
    if recovery_first:
        assert await recover(engine) == 1
        assert (await row(engine, claim.task_id)).status == "READY"
    response = await client.post(f"/api/v1/runs/{run}/cancel", headers=auth)
    assert response.status_code == 200 and response.json()["status"] == "CANCELED"
    assert await recover(engine) == await reconcile(engine) == 0
    stored = await row(engine, claim.task_id)
    assert stored.status == "CANCELED" and stored.next_run_at is None
    assert stored.attempt_count == stored.dispatch_generation == 1
    assert await attempts(engine, claim.task_id) == [("FAILED", "HANDLER_FAILED", 1)]


@pytest.mark.parametrize("scheduler_first", [True, False])
@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED"])
async def test_cancel_and_scheduler_completion(dispatch, scheduler_first, terminal):
    client, engine, auth, _ = dispatch
    dag = example()
    run = await make_run(dispatch, {**dag, "nodes": dag["nodes"][:1], "edges": []})
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    claim = await claim_task(engine, event, "worker")
    assert isinstance(claim, Claim)
    value, error = ('{"saved":true}', None) if terminal == "SUCCEEDED" else (None, "OUTPUT_INVALID")
    assert await finish(engine, claim, value, error, False)
    before = await row(engine, claim.task_id)
    if scheduler_first:
        assert await reconcile(engine) == 1
    path = f"/api/v1/runs/{run}"
    result = await client.post(path + "/cancel", headers=auth)
    assert result.status_code == 200
    assert result.json()["status"] == (terminal if scheduler_first else "CANCELED")
    assert result.json() == (await client.get(path, headers=auth)).json()
    assert await row(engine, claim.task_id) == before
    assert await attempts(engine, claim.task_id) == [(terminal, error, 1)]
    assert await reconcile(engine) == 0


async def test_cancel_fences_waiting_worker_and_scheduler(dispatch, monkeypatch):
    client, engine, auth, _ = dispatch
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    claim = await claim_task(engine, event, "worker")
    assert isinstance(claim, Claim) and await renew(engine, claim) is not None
    locked, release = asyncio.Event(), asyncio.Event()
    original = runs._visible_run

    async def pause_cancel(*args, **kwargs):
        metadata = await original(*args, **kwargs)
        if kwargs.get("lock"):
            locked.set()
            await asyncio.wait_for(release.wait(), 15)
        return metadata

    monkeypatch.setattr(runs, "_visible_run", pause_cancel)
    path = f"/api/v1/runs/{run}/cancel"
    cancellation = asyncio.create_task(client.post(path, headers=auth))
    contenders = []
    try:
        await asyncio.wait_for(locked.wait(), 10)
        contenders = [
            asyncio.create_task(renew(engine, claim)),
            asyncio.create_task(finish(engine, claim, '{"late":true}', None, False)),
            asyncio.create_task(claim_task(engine, event, "redelivery")),
        ]
        assert await asyncio.wait_for(reconcile(engine), 5) == 0
        assert await asyncio.wait_for(recover(engine), 5) == 0
        await wait_for_lock(engine, "SELECT status FROM workflow_runs WHERE id=%FOR UPDATE%")
    finally:
        release.set()
        result, *outcomes = await asyncio.wait_for(asyncio.gather(cancellation, *contenders), 10)
    assert result.status_code == 200 and result.json()["status"] == "CANCELED"
    assert outcomes == [None, False, Disposition.ACK]
    assert (await row(engine, claim.task_id)).result_json is None
    assert await attempts(engine, claim.task_id) == [("CANCELED", None, 1)]


async def test_lost_lease_signals_handler(dispatch, monkeypatch):
    client, engine, auth, _ = dispatch
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    running, signaled = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(consumer, "HEARTBEAT", 0.02)

    async def handler(engine, claim, cancelled):
        running.set()
        try:
            await asyncio.Future()
        finally:
            if cancelled.is_set():
                signaled.set()

    async def unexpected_settlement(*args, **kwargs):
        pytest.fail("Worker settled its delivery after losing the lease")

    delivery = SimpleNamespace(
        body=event.model_dump_json().encode(),
        ack=unexpected_settlement,
        reject=unexpected_settlement,
    )
    worker = asyncio.create_task(
        handle(engine, "worker", delivery, asyncio.Event(), lambda: None, handler)
    )
    try:
        await asyncio.wait_for(running.wait(), 10)
        response = await client.post(f"/api/v1/runs/{run}/cancel", headers=auth)
        assert response.status_code == 200 and response.json()["status"] == "CANCELED"
        with pytest.raises(consumer.Reconnect, match="lease expired"):
            await asyncio.wait_for(worker, 10)
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert signaled.is_set()
    assert await attempts(engine, event.task_run_id) == [("CANCELED", None, 1)]


async def test_cancel_rollback_keeps_lease_and_publisher_claim(dispatch):
    client, engine, auth, _ = dispatch
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    event = await event_for(engine, run)
    publisher = await claim_next(engine)
    claim = await claim_task(engine, event, "worker")
    assert isinstance(claim, Claim) and publisher.event_id == event.event_id
    before = await row(engine, event.task_run_id)
    before_events = await events(engine, run)
    async with engine.begin() as conn:
        await conn.execute(text("""CREATE FUNCTION fail_test_cancel() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'fixture cancellation failure'; END; $$ LANGUAGE plpgsql"""))
        await conn.execute(text("""CREATE TRIGGER fail_test_cancel BEFORE UPDATE ON workflow_runs
            FOR EACH ROW WHEN (NEW.status='CANCELED') EXECUTE FUNCTION fail_test_cancel()"""))
    path = f"/api/v1/runs/{run}/cancel"
    try:
        result = await client.post(path, headers=auth)
        assert result.status_code == 503 and result.json()["code"] == "DATABASE_UNAVAILABLE"
        assert (await run_state(engine, run)).status == "RUNNING"
        assert await row(engine, event.task_run_id) == before
        assert await attempts(engine, event.task_run_id) == [("RUNNING", None, 1)]
        assert await events(engine, run) == before_events
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER fail_test_cancel ON workflow_runs"))
            await conn.execute(text("DROP FUNCTION fail_test_cancel()"))
    assert (await client.post(path, headers=auth)).json()["status"] == "CANCELED"


@pytest.mark.parametrize("confirm_recorded", [False, True])
async def test_cancel_races_with_broker_confirmation(dispatch, confirm_recorded):
    client, engine, auth, workflow = dispatch
    dag = example()
    run = await make_run(dispatch, {**dag, "nodes": dag["nodes"][:1], "edges": []})
    assert await reconcile(engine) == 1
    broker = Broker(Settings().rabbitmq_url)
    try:
        claim = await claim_next(engine)
        assert claim is not None
        event = await preflight(engine, claim)
        assert event is not None and event.run_id == run
        assert await broker.send(event)
        queue = await broker.channel.get_queue(TASKS)
        if confirm_recorded:
            assert await finish_publish(engine, claim, True)
        result = await client.post(f"/api/v1/runs/{run}/cancel", headers=auth)
        assert result.status_code == 200 and result.json()["status"] == "CANCELED"
        assert not await finish_publish(engine, claim, True)
        (stored,) = await events(engine, run)
        assert stored.status == ("PUBLISHED" if confirm_recorded else "OBSOLETE")
        assert stored.claim_token is stored.claim_until is None
        assert (stored.published_at is not None) == confirm_recorded

        async def unexpected_handler(*args):
            pytest.fail("Canceled delivery reached the handler")

        late = await queue.get(timeout=5)
        assert late.message_id == str(event.event_id)
        await handle(engine, "worker", late, asyncio.Event(), lambda: None, unexpected_handler)
        assert late.processed and await attempts(engine, event.task_run_id) == []
        assert not await publish_next(engine, broker.send)
        assert await redrive(engine) == 0

        fresh = await post(client, auth, workflow, key=None)
        other = UUID(fresh.json()["run_id"])
        assert await reconcile(engine) == 1 and await publish_next(engine, broker.send)
        fresh_delivery = await queue.get(timeout=5)
        await handle(engine, "worker", fresh_delivery, asyncio.Event(), lambda: None)
        assert await reconcile(engine) == 1
        assert (await run_state(engine, other)).status == "SUCCEEDED"
    finally:
        await broker.close()
