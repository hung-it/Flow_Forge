"""Check exported signals from the disposable observability Compose stack."""

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.auth.service import bootstrap
from flowforge.config import Settings

STATE = Path("/tmp/flowforge-telemetry-smoke.json")
API = "http://localhost:8000/api/v1"
PROM = "http://prometheus:9090/api/v1/query"
JAEGER = "http://jaeger:16686/api/v3/traces/"
PRIVATE = "flowforge-observability-smoke-private-input"
QUEUE = '{queue="flowforge.tasks.v1"}'


async def eventually(check, label, seconds=90):
    try:
        async with asyncio.timeout(seconds):
            while not (value := await check()):
                await asyncio.sleep(1)
            return value
    except TimeoutError as error:
        raise AssertionError(f"Timed out waiting for {label}") from error


async def prom(client, expression):
    try:
        response = await client.get(PROM, params={"query": expression})
        response.raise_for_status()
        values = response.json()["data"]["result"]
        return float(values[0]["value"][1]) if values else None
    except httpx.TransportError:
        return None


async def seed(client, engine, *, cancel=False):
    email = f"metrics-{secrets.token_hex(8)}@example.com"
    password = secrets.token_urlsafe(24)
    await bootstrap(engine, email, password)

    async def call(method, path, **kwargs):
        return (await client.request(method, API + path, **kwargs)).raise_for_status().json()

    auth = await call("POST", "/auth/login", headers={"X-FlowForge-CSRF": "1"},
                      json={"email": email, "password": password})
    headers = {"Authorization": "Bearer " + auth["access_token"]}
    project = await call("POST", "/projects", headers=headers, json={"name": "Telemetry smoke"})
    workflow = await call("POST", f"/projects/{project['id']}/workflows", headers=headers,
                          json={"name": "Document trace"})
    path = "/workflows/" + workflow["id"]
    dag = json.loads((Path(__file__).parents[2] / "examples/document-workflow.json").read_text())
    await call("PUT", path + "/draft", headers=headers,
               json={"expected_revision": 1, "dag": dag})
    await call("POST", path + "/versions", headers=headers, json={"draft_revision": 2})
    payload = {"version": 1, "input": {"document": {"title": PRIVATE, "content": "Example"}}}
    key = secrets.token_hex(16)
    first = await call("POST", path + "/runs", headers={**headers, "Idempotency-Key": key},
                       json=payload)
    replay = await call("POST", path + "/runs", headers={**headers, "Idempotency-Key": key},
                        json=payload)
    assert first["run_id"] == replay["run_id"]
    if cancel:
        second = await call("POST", path + "/runs", headers=headers, json=payload)
        for _ in range(2):
            final = await call("POST", "/runs/" + second["run_id"] + "/cancel", headers=headers)
            assert final["status"] == "CANCELED"
    return first["run_id"]


async def row(engine, run_id):
    async with engine.connect() as conn:
        return (await conn.execute(text("""
            SELECT status,traceparent FROM workflow_runs WHERE id=:run
        """), {"run": UUID(run_id)})).one()


async def trace_rows(client, trace_id):
    try:
        response = await client.get(JAEGER + trace_id)
        if response.status_code == 404:
            return []
        response.raise_for_status()
    except httpx.TransportError:
        return []
    assert PRIVATE not in response.text
    return [
        (span, service)
        for envelope in response.text.splitlines() if envelope.strip()
        for resource in json.loads(envelope)["result"]["resourceSpans"]
        for service in [
            next(attribute["value"]["stringValue"]
                 for attribute in resource["resource"]["attributes"]
                 if attribute["key"] == "service.name")
        ]
        for scope in resource["scopeSpans"] for span in scope["spans"]
    ]


async def check_trace(client, engine, run_id):
    stored = await row(engine, run_id)
    assert stored.traceparent is not None
    trace_id = stored.traceparent.split("-")[1]
    expected = {
        "run.create": "flowforge-api", "scheduler.dispatch": "flowforge-scheduler",
        "scheduler.complete": "flowforge-scheduler", "publisher.publish": "flowforge-publisher",
        "worker.delivery": "flowforge-worker", "worker.claim": "flowforge-worker",
        "worker.execute": "flowforge-worker", "worker.commit": "flowforge-worker",
        "worker.ack": "flowforge-worker",
    }

    async def complete_trace():
        spans = await trace_rows(client, trace_id)
        names = {(span["name"], service) for span, service in spans}
        acknowledged = sum(span["name"] == "worker.ack" for span, _ in spans)
        return spans if expected.items() <= names and acknowledged >= 5 else None

    spans = await eventually(complete_trace, "Jaeger spans from all four roles")
    by_id = {span["spanId"]: span for span, _ in spans}
    for delivery in (span for span, _ in spans if span["name"] == "worker.delivery"):
        publish = by_id[delivery["parentSpanId"]]
        dispatch = by_id[publish["parentSpanId"]]
        create = by_id[dispatch["parentSpanId"]]
        assert [publish["name"], dispatch["name"], create["name"]] == [
            "publisher.publish", "scheduler.dispatch", "run.create",
        ]
        assert delivery["traceId"] == publish["traceId"] == dispatch["traceId"] == create["traceId"]
        children = {
            span["name"]: span
            for span, _ in spans if span.get("parentSpanId") == delivery["spanId"]
        }
        if "worker.commit" in children:
            assert int(children["worker.commit"]["endTimeUnixNano"]) <= int(
                children["worker.ack"]["startTimeUnixNano"]
            )
    print("PASS: Jaeger parent chain API → Scheduler → Publisher → Worker")
    print(json.dumps({"run_id": run_id, "trace_id": trace_id, "spans": sorted(expected)}))


async def completed(client, engine, run_id):
    async def succeeded():
        return (await row(engine, run_id)).status == "SUCCEEDED"

    await eventually(succeeded, "successful workflow")
    health = await client.get("http://localhost:8000/health/ready")
    assert health.status_code == 200 and health.json()["status"] == "ready"


async def main(phase):
    engine = create_async_engine(Settings().database_url)
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            if phase == "queued":
                async def grafana():
                    try:
                        response = await client.get(
                            "http://grafana:3000/api/dashboards/uid/flowforge-operations",
                            auth=("admin", os.environ["FLOWFORGE_GRAFANA_ADMIN_PASSWORD"]),
                        )
                        return response.status_code == 200 and len(
                            response.json()["dashboard"]["panels"]
                        ) == 9
                    except httpx.TransportError:
                        return False

                await eventually(grafana, "provisioned Grafana dashboard")
                print("PASS: Grafana provisioned the FlowForge dashboard")
                run_id = await seed(client, engine, cancel=True)
                STATE.write_text(run_id, encoding="utf-8")
                await eventually(lambda: prom(client, "flowforge_outbox_pending_events > 0"),
                                 "growing outbox backlog")
                print("PASS: Prometheus observes pending outbox while Publisher is stopped")
            elif phase == "broker":
                await eventually(lambda: prom(
                    client, f"rabbitmq_detailed_queue_messages_ready{QUEUE} > 0"
                ), "ready RabbitMQ delivery")
                await eventually(lambda: prom(
                    client, "flowforge_outbox_pending_events == bool 0"
                ), "outbox drain")
                print("PASS: outbox drains and RabbitMQ backlog grows with Worker stopped")
            elif phase == "completed":
                run_id = STATE.read_text(encoding="utf-8")
                await completed(client, engine, run_id)
                for expression in (
                    'count(up{job="worker"} == 1) == 2',
                    f"rabbitmq_detailed_queue_consumers{QUEUE} >= 2",
                    f"rabbitmq_detailed_queue_messages_ready{QUEUE} == bool 0",
                    'sum(flowforge_workflow_runs_total{outcome="created"}) == 2',
                    'sum(flowforge_workflow_runs_total{outcome="canceled"}) == 1',
                    'sum(flowforge_workflow_runs_total{outcome="succeeded"}) == 1',
                    'sum(flowforge_task_attempt_duration_seconds_count{outcome="succeeded"}) == 5',
                ):
                    await eventually(lambda: prom(client, expression), expression)
                print("PASS: two Worker targets, drained queue, one cancel and one successful run")
                await check_trace(client, engine, run_id)
            elif phase in {"offline", "restored"}:
                run_id = await seed(client, engine)
                await completed(client, engine, run_id)
                if phase == "restored":
                    await check_trace(client, engine, run_id)
                else:
                    print("PASS: workflow and readiness survive Jaeger and Prometheus outage")
            else:
                raise ValueError("Unknown telemetry smoke phase")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
