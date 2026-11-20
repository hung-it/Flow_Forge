from uuid import uuid4

from sqlalchemy import text
from test_dispatch import dispatch as dispatch
from test_dispatch import make_run
from test_execution_worker import event_for
from test_runs import post
from test_workflows import resource, sign_in
from test_workflows import setup as setup

from flowforge.scheduler.service import reconcile
from flowforge.worker.service import Claim, claim_task, finish, recover


async def test_task_attempts_are_paginated_and_project_scoped(dispatch, setup):
    client, engine, owner, workflow = dispatch
    _, _, _, second, *_ = setup
    run = await make_run(dispatch)
    assert await reconcile(engine) == 1
    first = await event_for(engine, run)
    path = f"/api/v1/tasks/{first.task_run_id}/attempts"
    assert (await client.get(path)).status_code == 401
    viewer = await sign_in(client, second)
    assert (await client.get(path, headers=viewer)).status_code == 404
    assert (await client.get(f"/api/v1/tasks/{uuid4()}/attempts", headers=owner)).status_code == 404
    project = (await client.get(workflow, headers=owner)).json()["project_id"]
    added = await client.post(f"/api/v1/projects/{project}/members", headers=owner,
                              json={"email": second, "role": "VIEWER"})
    assert added.status_code == 201
    empty = await client.get(path, headers=viewer)
    assert empty.status_code == 200 and empty.json() == []
    claim = await claim_task(engine, first, "test-worker-1")
    assert isinstance(claim, Claim)
    assert await finish(engine, claim, None, "HANDLER_FAILED", True)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE task_runs SET next_run_at=now() WHERE id=:id"),
                           {"id": first.task_run_id})
    assert await recover(engine) == await reconcile(engine) == 1
    next_event = await event_for(engine, run)
    assert isinstance(await claim_task(engine, next_event, "test-worker-2"), Claim)
    result = await client.get(path, headers=viewer)
    assert result.status_code == 200
    current, previous = result.json()
    assert set(current) == {
        "attempt_no", "dispatch_generation", "worker_id", "status", "error_code",
        "started_at", "finished_at",
    }
    assert current["attempt_no"] == current["dispatch_generation"] == 2
    assert current["worker_id"] == "test-worker-2" and current["status"] == "RUNNING"
    assert current["finished_at"] is None
    assert previous["attempt_no"] == 1 and previous["status"] == "FAILED"
    assert previous["error_code"] == "HANDLER_FAILED" and previous["finished_at"]
    assert (await client.get(path + "?limit=1&offset=1", headers=viewer)).json() == [previous]
    assert (await client.get(path + "?offset=9", headers=viewer)).json() == []


async def test_dashboard_filters(dispatch, setup):
    client, _, owner, workflow = dispatch
    _, _, _, second, *_ = setup
    run = await make_run(dispatch)
    other = (await post(client, owner, workflow, key=None)).json()["run_id"]
    assert (await client.post(f"/api/v1/runs/{run}/cancel", headers=owner)).status_code == 200
    info = (await client.get(f"/api/v1/runs/{run}", headers=owner)).json()
    project, workflow_id = info["project_id"], info["workflow_id"]
    filters = {"project_id": project, "workflow_id": workflow_id, "status": "CANCELED",
               "created_from": info["created_at"], "limit": 1}
    found = await client.get("/api/v1/runs", headers=owner, params=filters)
    assert found.status_code == 200 and [item["run_id"] for item in found.json()] == [str(run)]
    filters["created_to"] = info["created_at"]
    assert (await client.get("/api/v1/runs", headers=owner, params=filters)).status_code == 422
    del filters["created_from"]
    assert (await client.get("/api/v1/runs", headers=owner, params=filters)).json() == []
    assert (await client.get("/api/v1/runs", headers=owner,
                             params={"created_from": "2026-01-01T00:00:00"})).status_code == 422
    found = await client.get("/api/v1/runs", headers=owner,
                             params={"workflow_id": workflow_id, "status": "PENDING"})
    assert [item["run_id"] for item in found.json()] == [other]
    outsider, other_project, foreign_workflow = await resource(client, second)
    for auth, project_filter, wf in (
        (outsider, None, workflow_id),
        (owner, project, foreign_workflow.rsplit("/", 1)[1]),
        (outsider, other_project.rsplit("/", 1)[1], str(uuid4())),
    ):
        query = {"workflow_id": wf, **({"project_id": project_filter} if project_filter else {})}
        assert (await client.get("/api/v1/runs", headers=auth, params=query)).status_code == 404
    path = f"/api/v1/projects/{project}/workflows"
    assert (await client.post(path, headers=owner, json={"name": "Report_50%"})).status_code == 201
    assert (await client.post(path, headers=owner, json={"name": "ReportX50X"})).status_code == 201
    result = await client.get(path, headers=owner, params={"q": " rT_50% "})
    assert result.status_code == 200 and [item["name"] for item in result.json()] == ["Report_50%"]
    assert (await client.get(path, headers=outsider, params={"q": "Report"})).status_code == 404
