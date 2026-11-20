"""Seed and reconcile a disposable FlowForge benchmark through API and PostgreSQL."""

import asyncio
import json
import secrets
import sys
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from telemetry_acceptance import API, JAEGER, PROM, eventually, trace_rows

from flowforge.auth.service import bootstrap
from flowforge.config import Settings


async def seed(client, engine, nodes):
    email = f"benchmark-{secrets.token_hex(6)}@example.com"
    password = secrets.token_urlsafe(24)
    await bootstrap(engine, email, password)
    login = await client.post(API + "/auth/login", headers={"X-FlowForge-CSRF": "1"},
                              json={"email": email, "password": password})
    token = login.raise_for_status().json()["access_token"]
    client.headers["Authorization"] = "Bearer " + token

    async def post(path, payload):
        return (await client.post(API + path, json=payload)).raise_for_status().json()

    project = await post("/projects", {"name": "FlowForge benchmark"})
    workflow = await post(f"/projects/{project['id']}/workflows", {"name": "Validation workload"})
    path = "/workflows/" + workflow["id"]
    draft = {"schema_version": 1, "name": "benchmark", "edges": [], "nodes": [
        {"key": f"validate_{index:03d}", "handler": "validate_document", "timeout_seconds": 90,
         "inputs": {"document": {"source": "run_input", "pointer": "/document"}},
         "retry": {"max_attempts": 2}}
        for index in range(nodes)
    ]}
    saved = await client.put(API + path + "/draft",
                             json={"expected_revision": 1, "dag": draft})
    saved.raise_for_status()
    await post(path + "/versions", {"draft_revision": 2})
    warm = await client.post(API + path + "/runs", headers={"Idempotency-Key": "warm-up"},
                             json={"version": 1, "input": {"document": {
                                 "title": "Warm-up", "content": "Synthetic warm-up.",
                             }}})
    warm.raise_for_status()
    await wait_for_runs(engine, UUID(workflow["id"]), ["warm-up"], 90)
    return {"workflow_id": workflow["id"], "token": token}


async def wait_for_runs(engine, workflow, keys, seconds):
    async def finished():
        async with engine.connect() as conn:
            rows = (await conn.execute(text("""
                SELECT id, idempotency_key, status, created_at, finished_at, traceparent
                FROM workflow_runs WHERE workflow_id=:workflow
            """), {"workflow": workflow})).mappings().all()
        selected = {row["idempotency_key"]: row for row in rows}
        if keys != ["warm-up"]:
            assert selected.keys() <= {*keys, "warm-up"}, "Unexpected run in benchmark workflow"
        if any(key in selected and selected[key]["status"] in {"FAILED", "CANCELED"}
               for key in keys):
            raise AssertionError("A benchmark workflow failed or was canceled")
        if all(key in selected and selected[key]["status"] == "SUCCEEDED" for key in keys):
            return [selected[key] for key in keys]
        return None

    return await eventually(finished, "all benchmark runs to finish", seconds)


async def fault_at(engine, workflow):
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT t.id, a.attempt_no, a.dispatch_generation,
                   EXTRACT(EPOCH FROM clock_timestamp()) AS observed_at
            FROM workflow_runs r JOIN task_runs t ON t.run_id=r.id
            JOIN task_attempts a ON a.task_run_id=t.id
            WHERE r.workflow_id=:workflow AND r.idempotency_key LIKE 'bench-%'
            AND a.worker_id='crash-fixture' AND a.status='RUNNING' AND t.status='RUNNING'
        """), {"workflow": workflow})).one()
    return {"task_id": str(row.id), "attempt": row.attempt_no,
            "generation": row.dispatch_generation, "observed_at": float(row.observed_at)}


async def collect(client, engine, workflow, runs, nodes, seconds, fault):
    outcomes = await wait_for_runs(engine, workflow, [f"bench-{i}" for i in range(runs)], seconds)
    run_ids = [row["id"] for row in outcomes]
    async with engine.connect() as conn:
        params = {"runs": run_ids}
        tasks = (await conn.execute(text("""
            SELECT t.id, t.run_id, t.node_key, t.status, t.attempt_count,
                   t.result_json IS NOT NULL AS has_result
            FROM task_runs t WHERE t.run_id=ANY(CAST(:runs AS uuid[]))
        """), params)).mappings().all()
        attempts = (await conn.execute(text("""
            SELECT a.task_run_id, a.attempt_no, a.status, a.error_code, a.finished_at,
                   a.dispatch_generation,
                   EXTRACT(EPOCH FROM (a.finished_at-a.started_at)) AS duration,
                   EXTRACT(EPOCH FROM (a.started_at-e.created_at)) AS dispatch
            FROM task_attempts a JOIN task_runs t ON t.id=a.task_run_id
            JOIN outbox_events e ON e.task_run_id=t.id
                AND e.dispatch_generation=a.dispatch_generation
            WHERE t.run_id=ANY(CAST(:runs AS uuid[]))
        """), params)).mappings().all()
        pending = await conn.scalar(text("""
            SELECT count(*) FROM outbox_events e JOIN task_runs t ON t.id=e.task_run_id
            WHERE t.run_id=ANY(CAST(:runs AS uuid[])) AND e.status IN ('PENDING','CLAIMED')
        """), params)

    expected = runs * nodes
    assert len(tasks) == expected and len({task["id"] for task in tasks}) == expected
    assert all(task["status"] == "SUCCEEDED" and task["has_result"] for task in tasks)
    assert pending == 0, "Outbox has unfinished work"
    success = {a["task_run_id"]: a for a in attempts if a["status"] == "SUCCEEDED"}
    assert set(success) == {task["id"] for task in tasks}, "Missing successful attempts"
    assert len(attempts) == expected + int(fault is not None), "Unexpected extra attempts"
    assert all(task["attempt_count"] == 1 + int(fault is not None and
               str(task["id"]) == fault["task_id"]) for task in tasks)
    recovery = None
    trace_run = outcomes[0]
    if fault:
        target = UUID(fault["task_id"])
        (lost,) = [a for a in attempts if a["status"] == "LOST"]
        assert lost["task_run_id"] == target and lost["error_code"] == "WORKER_LOST"
        assert lost["attempt_no"] == fault["attempt"]
        assert lost["dispatch_generation"] == fault["generation"]
        assert success[target]["attempt_no"] == fault["attempt"] + 1
        assert success[target]["dispatch_generation"] == fault["generation"] + 1
        recovery = success[target]["finished_at"].timestamp() - fault["observed_at"]
        assert 0 <= recovery <= 90, f"Worker recovery exceeded its bound: {recovery:.3f}s"
        run = next(task["run_id"] for task in tasks if task["id"] == target)
        trace_run = next(row for row in outcomes if row["id"] == run)

    trace_id = trace_run["traceparent"].split("-")[1]

    async def get_trace():
        spans = await trace_rows(client, trace_id)
        names = {span["name"] for span, _ in spans}
        return {"run.create", "scheduler.dispatch", "publisher.publish", "worker.ack"} <= names

    await eventually(get_trace, "benchmark trace", 20)
    trace = (await client.get(JAEGER + trace_id)).raise_for_status()
    snapshot = {}
    for expression in (
        'count(up{job="worker"} == 1)', "flowforge_outbox_pending_events",
        'sum(rabbitmq_detailed_queue_messages_ready{queue="flowforge.tasks.v1"})',
        'sum(rabbitmq_detailed_queue_messages_unacked{queue="flowforge.tasks.v1"})',
        "sum(flowforge_task_attempts_total) by (outcome)", "sum(flowforge_lease_expirations_total)",
    ):
        response = await client.get(PROM, params={"query": expression})
        snapshot[expression] = response.raise_for_status().json()["data"]["result"]
    return {
        "run_count": len(outcomes), "task_count": len(tasks), "attempt_count": len(attempts),
        "finished_at": max(row["finished_at"].timestamp() for row in outcomes),
        "run_seconds": [
            (row["finished_at"] - row["created_at"]).total_seconds() for row in outcomes
        ],
        "task_seconds": [float(a["duration"]) for a in success.values()],
        "dispatch_seconds": [float(a["dispatch"]) for a in success.values()],
        "recovery_seconds": recovery,
        "pending_outbox": pending,
        "fault": fault, "trace_id": trace_id, "trace": trace.text, "prometheus": snapshot,
    }


async def main():
    engine = create_async_engine(Settings().database_url)
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            phase, *args = sys.argv[1:]
            if phase == "seed":
                output = await seed(client, engine, int(args[0]))
            elif phase == "fault":
                output = await fault_at(engine, UUID(args[0]))
            elif phase == "collect":
                workflow, runs, nodes, seconds = args
                fault = json.load(sys.stdin)
                output = await collect(client, engine, UUID(workflow), int(runs), int(nodes),
                                       float(seconds), fault)
            else:
                raise ValueError("Unknown benchmark phase")
        print(json.dumps(output))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
