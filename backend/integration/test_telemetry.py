import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from test_dispatch import dispatch as dispatch
from test_dispatch import events, make_run
from test_runs import post
from test_workflows import example
from test_workflows import setup as setup

from flowforge import observability as obs
from flowforge.contracts import DispatchMessage
from flowforge.outbox.service import publish_next
from flowforge.scheduler.service import reconcile, sample_outbox
from flowforge.worker import consumer
from flowforge.worker.service import Claim, claim_task, finish, recover


@pytest.fixture
def signals(monkeypatch):
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(obs, "_tracer", provider.get_tracer("telemetry-test"))
    metrics = obs.Metrics()
    monkeypatch.setattr(obs, "metrics", metrics)
    try:
        yield metrics.registry.get_sample_value, exporter
    finally:
        provider.shutdown()


async def test_run_replay_cancel_link_and_http_label_boundaries(dispatch, signals):
    client, engine, auth, workflow = dispatch
    sample, exporter = signals
    run = await make_run(dispatch)
    async with engine.connect() as conn:
        original = await conn.scalar(
            text("SELECT traceparent FROM workflow_runs WHERE id=:id"), {"id": run}
        )
    assert obs.trace_parent(original) is not None
    assert (await post(client, auth, workflow)).json()["run_id"] == str(run)
    async with engine.connect() as conn:
        assert await conn.scalar(
            text("SELECT traceparent FROM workflow_runs WHERE id=:id"), {"id": run}
        ) == original
    for _ in range(2):
        result = await client.post(f"/api/v1/runs/{run}/cancel", headers=auth)
        assert result.json()["status"] == "CANCELED"
    assert sample("flowforge_workflow_runs_total", {"outcome": "created"}) == 1
    assert sample("flowforge_workflow_runs_total", {"outcome": "canceled"}) == 1
    assert sample("flowforge_task_attempts_total", {"outcome": "canceled"}) is None
    assert sample("flowforge_http_requests_total", {
        "method": "POST", "route": "/api/v1/runs/{run_id}/cancel", "status_class": "2xx",
    }) == 2
    secret = "private-route-and-query-marker"
    assert (await client.get("/" + secret + "?q=" + secret)).status_code == 404
    assert sample("flowforge_http_requests_total", {
        "method": "GET", "route": "unmatched", "status_class": "4xx",
    }) == 1
    spans = exporter.get_finished_spans()
    cancel = next(item for item in spans if item.name == "run.cancel")
    assert f"{cancel.links[0].context.trace_id:032x}" == original.split("-")[1]
    assert cancel.context.trace_id != cancel.links[0].context.trace_id
    assert all(secret not in str(item.attributes) for item in spans)


async def test_retry_lease_and_duplicate_count_only_committed_attempts(dispatch, signals):
    _, engine, *_ = dispatch
    sample, exporter = signals
    dag = example()
    dag["nodes"], dag["edges"] = dag["nodes"][:1], []
    dag["nodes"][0]["retry"] = {"max_attempts": 3}
    run = await make_run(dispatch, dag)
    assert await reconcile(engine) == 1
    await sample_outbox(engine)
    assert sample("flowforge_outbox_pending_events") == 1
    assert sample("flowforge_outbox_snapshot_timestamp_seconds") > 0
    deliveries = []
    acks = []

    async def ack():
        acks.append(True)

    async def send(event):
        deliveries.append(SimpleNamespace(
            body=event.model_dump_json().encode(),
            headers={"traceparent": obs.trace_header()}, ack=ack,
        ))
        return True

    async def fail(*_):
        raise consumer.HandlerFailure("private handler exception")

    assert await publish_next(engine, send)
    await sample_outbox(engine)
    assert sample("flowforge_outbox_pending_events") == 0
    await consumer.handle(engine, "worker", deliveries[0], asyncio.Event(), lambda: None, fail)
    await consumer.handle(engine, "worker", deliveries[0], asyncio.Event(), lambda: None, fail)
    assert acks == [True, True]
    assert sample("flowforge_task_attempts_total", {"outcome": "failed"}) == 1
    assert sample("flowforge_task_retries_total", {"reason": "handler_failed"}) == 1
    assert sample("flowforge_task_attempt_duration_seconds_count", {"outcome": "failed"}) == 1
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_runs SET next_run_at=clock_timestamp() WHERE run_id=:run"),
            {"run": run},
        )
    assert await recover(engine) == 1
    assert await reconcile(engine) == 1
    latest = max(await events(engine, run), key=lambda entry: entry.dispatch_generation)
    event = DispatchMessage.model_validate_json(json.dumps(latest.payload))
    claim = await claim_task(engine, event, "worker")
    assert isinstance(claim, Claim)

    @asynccontextmanager
    async def rollback():
        async with engine.begin() as conn:
            yield conn
            raise RuntimeError("fixture rollback before commit")

    with pytest.raises(RuntimeError, match="fixture rollback before commit"):
        await finish(SimpleNamespace(begin=rollback), claim, "{}", None, False)
    assert sample("flowforge_task_attempts_total", {"outcome": "succeeded"}) is None
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_runs SET lease_until=clock_timestamp() WHERE id=:task"),
            {"task": claim.task_id},
        )
    assert not await finish(engine, claim, "{}", None, False)
    assert await recover(engine) == 1
    assert sample("flowforge_lease_expirations_total", {"reason": "lease"}) == 1
    assert sample("flowforge_task_attempts_total", {"outcome": "lost"}) == 1
    assert sample("flowforge_task_retries_total", {"reason": "worker_lost"}) == 1
    assert sample("flowforge_task_attempts_total", {"outcome": "succeeded"}) is None
    spans = exporter.get_finished_spans()
    create = next(item for item in spans if item.name == "run.create")
    worker = next(item for item in spans if item.name == "worker.delivery")
    publish = next(item for item in spans if item.name == "publisher.publish")
    scheduler = next(item for item in spans if item.name == "scheduler.dispatch")
    assert worker.parent.span_id == publish.context.span_id
    assert publish.parent.span_id == scheduler.context.span_id
    assert scheduler.parent.span_id == create.context.span_id
    assert worker.context.trace_id == create.context.trace_id
    assert all("private handler exception" not in item.to_json() for item in spans)
