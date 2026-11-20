import asyncio
import copy
import json
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_workflows import example, resource, sign_in
from test_workflows import setup as setup

from flowforge.api import create_app

BODY = {"version": 1, "input": {"document": {"title": "FlowForge", "content": "Example"}}}
KEY = "run-test-key"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_run_data(setup):
    _, engine, _, _, owner, member = setup
    try:
        yield
    finally:
        # These API-only fixtures must not leave pending work for scheduler tests.
        async with engine.begin() as conn:
            params = {"owner": owner, "member": member}
            for table in ("task_dependencies", "task_runs"):
                await conn.execute(
                    text(f"""DELETE FROM {table} t USING workflow_runs r
                        WHERE t.run_id=r.id AND r.created_by IN (:owner,:member)"""),
                    params,
                )
            await conn.execute(
                text("DELETE FROM workflow_runs WHERE created_by IN (:owner,:member)"), params
            )


async def publish(client, auth, workflow, dag, revision=1):
    saved = await client.put(
        workflow + "/draft", headers=auth, json={"expected_revision": revision, "dag": dag}
    )
    assert saved.status_code == 200, saved.text
    version = await client.post(
        workflow + "/versions", headers=auth, json={"draft_revision": revision + 1}
    )
    assert version.status_code == 201, version.text
    return version.json()


async def post(client, auth, workflow, body=BODY, key=KEY):
    headers = {**auth, "Idempotency-Key": key} if key is not None else auth
    return await client.post(workflow + "/runs", headers=headers, json=body)


async def test_snapshot_and_direct_graph(setup):
    client, engine, first, *_ = setup
    auth, project, workflow = await resource(client, first)
    assert (await post(client, auth, workflow)).status_code == 404
    dag = example()
    original = await publish(client, auth, workflow, dag)
    linear = {**dag, "nodes": dag["nodes"][:2], "edges": [["validate", "extract"]]}
    await publish(client, auth, workflow, linear, revision=2)
    # Give version 3 multiple independent roots so neither gets a synthetic dependency.
    independent = copy.deepcopy(linear)
    independent["edges"] = []
    independent["nodes"][1]["inputs"] = independent["nodes"][0]["inputs"]
    await publish(client, auth, workflow, independent, revision=3)
    for number, expected in ((1, dag), (2, linear), (3, independent)):
        created = await post(client, auth, workflow, {**BODY, "version": number}, f"v{number}")
        assert created.status_code == 202, created.text
        run = created.json()
        assert run["status"] == "PENDING"
        location = created.headers["Location"]
        assert location == "/api/v1/runs/" + run["run_id"]
        metadata = (await client.get(location, headers=auth)).json()
        assert metadata["version"] == number
        assert "input" not in metadata and "idempotency_key" not in metadata
        if number == 1:
            assert metadata["workflow_version_id"] == original["id"]
        tasks = (await client.get(location + "/tasks", headers=auth)).json()
        assert [t["node_key"] for t in tasks] == sorted(n["key"] for n in expected["nodes"])
        assert all(t["status"] == "PENDING" and t["attempt_count"] == 0 for t in tasks)
        by_id = {t["task_id"]: t["node_key"] for t in tasks}
        assert {(by_id[p], t["node_key"]) for t in tasks for p in t["predecessor_task_ids"]} == {
            tuple(e) for e in expected["edges"]
        }
        page = (await client.get(location + "/tasks?limit=1&offset=1", headers=auth)).json()
        assert page == tasks[1:2]
        async with engine.connect() as conn:
            stored = await conn.scalar(
                text("SELECT CAST(input_json AS text) FROM workflow_runs WHERE id=:id"),
                {"id": UUID(run["run_id"])},
            )
            assert json.loads(stored) == BODY["input"]
    runs = (
        await client.get("/api/v1/runs?project_id=" + project.rsplit("/", 1)[1], headers=auth)
    ).json()
    assert [r["version"] for r in runs] == [3, 2, 1]


async def test_100_simultaneous_requests_across_app_instances(setup):
    client, engine, first, second, *_ = setup
    auth, project, workflow = await resource(client, first)
    await publish(client, auth, workflow, example())
    member = await client.post(
        project + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"}
    )
    assert member.status_code == 201
    other = await sign_in(client, second)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as replica:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        post(
                            client if index % 2 == 0 else replica,
                            auth if index % 4 < 2 else other,
                            workflow,
                        )
                        for index in range(100)
                    )
                ),
                timeout=20,
            )
            assert all(r.status_code == 202 for r in responses), [
                r.text for r in responses if r.status_code != 202
            ]
            assert len({r.json()["run_id"] for r in responses}) == 1
            assert len({r.headers["Location"] for r in responses}) == 1

    async with engine.connect() as conn:
        counts = (
            await conn.execute(
                text("""SELECT
            (SELECT count(*) FROM workflow_runs WHERE workflow_id=:w),
            (SELECT count(*) FROM task_runs WHERE run_id=:r),
            (SELECT count(*) FROM task_dependencies WHERE run_id=:r)"""),
                {"w": UUID(workflow.rsplit("/", 1)[1]), "r": UUID(responses[0].json()["run_id"])},
            )
        ).one()
        assert tuple(counts) == (1, 5, 5)

    replay = await post(
        client,
        auth,
        workflow,
        {"input": {"document": {"content": "Example", "title": "FlowForge"}}, "version": 1},
    )
    assert replay.json() == responses[0].json()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE workflow_runs SET status='RUNNING',started_at=now() WHERE id=:id"),
            {"id": UUID(replay.json()["run_id"])},
        )
    assert (await post(client, auth, workflow)).json() == {**replay.json(), "status": "RUNNING"}
    assert (await post(client, auth, workflow, {"version": 1, "input": {}})).status_code == 422
    changed = await post(
        client, auth, workflow, {"version": 1, "input": {**BODY["input"], "extra": True}}
    )
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    await publish(client, auth, workflow, example(), revision=2)
    assert (await post(client, auth, workflow, {**BODY, "version": 2})).status_code == 409

    new = await client.post(project + "/workflows", headers=auth, json={"name": "Another"})
    assert new.status_code == 201
    other_workflow = "/api/v1/workflows/" + new.json()["id"]
    await publish(client, auth, other_workflow, example())
    assert (await post(client, auth, other_workflow)).status_code == 409
    _, _, separate = await resource(client, second)
    await publish(client, other, separate, example())
    isolated = await post(client, other, separate)
    assert isolated.status_code == 202 and isolated.json()["run_id"] != replay.json()["run_id"]
    no_key = [await post(client, auth, workflow, key=None) for _ in range(2)]
    assert all(r.status_code == 202 for r in no_key)
    assert len({r.json()["run_id"] for r in no_key}) == 2

    location = replay.headers["Location"]
    member_url = project + "/members/" + member.json()["user_id"]
    assert (
        await client.patch(member_url, headers=auth, json={"role": "VIEWER"})
    ).status_code == 200
    assert (await post(client, other, workflow)).status_code == 403
    assert (await client.get(location + "/tasks", headers=other)).status_code == 200
    assert (await client.delete(member_url, headers=auth)).status_code == 204
    assert (await post(client, other, workflow)).status_code == 404
    assert (await client.get(location, headers=other)).status_code == 404
    assert (await client.get(location + "/tasks", headers=other)).status_code == 404
    assert (await client.get(location)).status_code == 401
    assert (await client.get("/api/v1/runs", headers=other)).json() == [
        (await client.get(isolated.headers["Location"], headers=other)).json()
    ]
    outside = await client.get(
        "/api/v1/runs?project_id=" + project.rsplit("/", 1)[1], headers=other
    )
    assert outside.status_code == 404


async def test_failed_dependency_insert_releases_key(setup):
    client, engine, first, *_ = setup
    auth, _, workflow = await resource(client, first)
    await publish(client, auth, workflow, example())
    async with engine.begin() as conn:
        await conn.execute(
            text("""CREATE FUNCTION fail_test_dependency() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture insert error'; END $$""")
        )
        await conn.execute(
            text("""CREATE TRIGGER fail_test_dependency BEFORE INSERT
            ON task_dependencies FOR EACH ROW EXECUTE FUNCTION fail_test_dependency()""")
        )
    try:
        assert (await post(client, auth, workflow)).status_code == 503
        assert (await client.get("/api/v1/runs", headers=auth)).json() == []
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER fail_test_dependency ON task_dependencies"))
            await conn.execute(text("DROP FUNCTION fail_test_dependency()"))
    assert (await post(client, auth, workflow)).status_code == 202


async def test_replay_waiting_for_project_lock_uses_current_role(setup):
    client, engine, first, second, *_ = setup
    auth, project, workflow = await resource(client, first)
    await publish(client, auth, workflow, example())
    member = await client.post(
        project + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"}
    )
    other = await sign_in(client, second)
    assert (await post(client, other, workflow)).status_code == 202
    params = {"p": UUID(project.rsplit("/", 1)[1]), "u": UUID(member.json()["user_id"])}

    async def waits_for_project_lock():
        while True:
            async with engine.connect() as observer:
                waiting = await observer.scalar(
                    text("""SELECT EXISTS (
                    SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                    AND wait_event_type='Lock'
                    AND query LIKE 'SELECT id FROM projects WHERE id=%FOR UPDATE%')""")
                )
            if waiting:
                return
            await asyncio.sleep(0.01)

    async with engine.begin() as conn:
        await conn.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), params)
        pending = asyncio.create_task(post(client, other, workflow))
        await asyncio.wait_for(waits_for_project_lock(), 5)
        await conn.execute(
            text("""UPDATE project_members SET role='VIEWER'
                                   WHERE project_id=:p AND user_id=:u"""),
            params,
        )
    assert (await asyncio.wait_for(pending, 5)).status_code == 403
    assert len((await client.get("/api/v1/runs", headers=auth)).json()) == 1


async def test_db_constraints_reject_cross_graph_references(setup):
    client, engine, first, *_ = setup
    auth, _, workflow = await resource(client, first)
    _, _, other = await resource(client, first)
    await publish(client, auth, workflow, example())
    await publish(client, auth, other, example())
    first_run = await post(client, auth, workflow)
    second_run = await post(client, auth, other, key="separate-graph")
    assert first_run.status_code == second_run.status_code == 202
    a, b = [UUID(r.json()["run_id"]) for r in (first_run, second_run)]
    checks = (
        """UPDATE workflow_runs SET project_id=(SELECT project_id FROM workflow_runs WHERE id=:b)
           WHERE id=:a""",
        """UPDATE workflow_runs SET workflow_version_id=(
           SELECT workflow_version_id FROM workflow_runs WHERE id=:b) WHERE id=:a""",
        """INSERT INTO task_dependencies(run_id,predecessor_task_id,successor_task_id) VALUES
           (:a,(SELECT id FROM task_runs WHERE run_id=:a LIMIT 1),
            (SELECT id FROM task_runs WHERE run_id=:b LIMIT 1))""",
    )
    for sql in checks:
        with pytest.raises(IntegrityError) as error:
            async with engine.begin() as conn:
                await conn.execute(text(sql), {"a": a, "b": b})
        assert error.value.orig.sqlstate == "23503"


async def test_run_input_and_headers(setup):
    client, _, first, *_ = setup
    auth, _, workflow = await resource(client, first)
    dag = example()
    # A downstream node also has a run_input binding.
    dag["nodes"][-1]["inputs"]["summary"] = {"source": "run_input", "pointer": "/a~1b/~0/0"}
    await publish(client, auth, workflow, dag)
    missing = await post(client, auth, workflow)
    assert missing.status_code == 422 and missing.json()["code"] == "INPUT_BINDING_INVALID"
    body = {"version": 1, "input": {**BODY["input"], "a/b": {"~": [None]}}}
    for extra in (
        (("Idempotency-Key", ""),),
        (("Idempotency-Key", "bad key"),),
        (("Idempotency-Key", "a" * 129),),
        (("Idempotency-Key", "a"), ("idempotency-key", "a")),
    ):
        invalid = await client.post(workflow + "/runs", json=body, headers=[*auth.items(), *extra])
        assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_IDEMPOTENCY_KEY"
    first_run = await post(client, auth, workflow, body)
    assert first_run.status_code == 202, first_run.text
    for invalid_version in (0, True, "1", 2147483648):
        assert (
            await post(client, auth, workflow, {**body, "version": invalid_version})
        ).status_code == 422

    encoded = json.dumps(
        {**body, "input": {**body["input"], "label": "語" * 4100}}, ensure_ascii=False
    ).encode("utf-8")
    encoded = encoded.ljust(64 * 1024, b" ")

    async def chunks(value):
        for index in range(0, len(value), 997):
            yield value[index : index + 997]

    headers = {**auth, "Content-Type": "application/json"}
    assert (
        await client.post(workflow + "/runs", headers=headers, content=chunks(encoded))
    ).status_code == 202
    assert (
        await client.post(workflow + "/runs", headers=headers, content=chunks(encoded + b" "))
    ).status_code == 413
    assert (await client.post(workflow + "/runs", headers=auth, content=b"{}")).status_code == 415
    assert (await client.post(workflow + "/runs", headers=headers, content=b"{")).status_code == 422
