import asyncio
import json
import secrets
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.api import create_app
from flowforge.auth.service import bootstrap
from flowforge.config import Settings

PASSWORD = "Correct horse battery!"
CSRF = {"X-FlowForge-CSRF": "1"}


@pytest_asyncio.fixture
async def setup(monkeypatch):
    monkeypatch.setenv("FLOWFORGE_JWT_SECRET", secrets.token_urlsafe(48))
    engine = create_async_engine(Settings().database_url)
    suffix = secrets.token_hex(6)
    first, second = f"owner-{suffix}@example.com", f"member-{suffix}@example.com"
    owner = await bootstrap(engine, first, PASSWORD)
    member = await bootstrap(engine, second, PASSWORD)
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=(f"test-{suffix}", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client, engine, first, second, owner, member
    await engine.dispose()


async def sign_in(client, email):
    response = await client.post(
        "/api/v1/auth/login", headers=CSRF, json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def resource(client, first):
    auth = await sign_in(client, first)
    project = (await client.post("/api/v1/projects", headers=auth, json={"name": "P"})).json()
    root = "/api/v1/projects/" + project["id"]
    response = await client.post(root + "/workflows", headers=auth, json={"name": " W "})
    assert response.status_code == 201, response.text
    return auth, root, "/api/v1/workflows/" + response.json()["id"]


def example():
    return json.loads((Path(__file__).parents[2] / "examples/document-workflow.json").read_text())


async def test_workflow_lifecycle(setup):
    client, engine, first, *_ = setup
    auth, root, path = await resource(client, first)
    assert (await client.get(path, headers=auth)).json()["name"] == "W"
    assert len((await client.get(root + "/workflows", headers=auth)).json()) == 1
    assert (await client.get(path + "/draft", headers=auth)).json()["revision"] == 1
    assert (
        await client.post(path + "/versions", headers=auth, json={"draft_revision": 1})
    ).status_code == 422
    for dag in ({}, example()):
        rev = (await client.get(path + "/draft", headers=auth)).json()["revision"]
        r = await client.put(
            path + "/draft", headers=auth, json={"expected_revision": rev, "dag": dag}
        )
        assert r.status_code == 200, r.text
    versions = []
    for number in (1, 2):
        r = await client.post(path + "/versions", headers=auth, json={"draft_revision": 3})
        assert r.status_code == 201, r.text
        assert r.json()["version"] == number
        versions.append(r.json())
    assert versions[0]["checksum"] == versions[1]["checksum"]
    assert (await client.patch(path, headers=auth, json={"name": "Changed"})).status_code == 200
    assert (await client.get(path + "/versions/1", headers=auth)).json() == versions[0]
    rows = (await client.get(path + "/versions", headers=auth)).json()
    assert [x["version"] for x in rows] == [2, 1]
    assert "dag" not in rows[0]
    assert (await client.get(path + "/versions/99", headers=auth)).status_code == 404
    assert (await client.get(path + "/versions/0", headers=auth)).status_code == 422
    for sql in (
        "UPDATE workflow_versions SET version=99 WHERE id=:id",
        "DELETE FROM workflow_versions WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(text(sql), {"id": versions[0]["id"]})


async def test_workflow_rbac(setup):
    client, _, first, second, *_ = setup
    auth, root, path = await resource(client, first)
    other = await sign_in(client, second)
    assert (await client.get(path, headers=other)).status_code == 404
    assert (await client.get(root + "/workflows", headers=other)).status_code == 404
    assert (await client.get(path)).status_code == 401
    r = await client.post(root + "/members", headers=auth, json={"email": second, "role": "VIEWER"})
    user = r.json()["user_id"]
    assert (await client.get(path + "/draft", headers=other)).status_code == 200
    for role, expected in (("VIEWER", 403), ("DEVELOPER", 200)):
        await client.patch(root + "/members/" + user, headers=auth, json={"role": role})
        assert (
            await client.patch(path, headers=other, json={"name": role})
        ).status_code == expected
        r = await client.put(
            path + "/draft", headers=other, json={"expected_revision": 1, "dag": example()}
        )
        assert r.status_code == expected
    assert (
        await client.post(path + "/versions", headers=other, json={"draft_revision": 2})
    ).status_code == 201
    assert (
        await client.post(root + "/workflows", headers=other, json={"name": "Developer"})
    ).status_code == 201
    assert (await client.patch(root, headers=other, json={"name": "Denied"})).status_code == 403


async def test_draft_concurrency_and_publish(setup):
    client, _, first, second, *_ = setup
    auth, root, path = await resource(client, first)
    other = await sign_in(client, second)
    await client.post(root + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"})
    results = await asyncio.gather(
        *[
            client.put(path + "/draft", headers=a, json={"expected_revision": 1, "dag": example()})
            for a in (auth, other)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    results = await asyncio.gather(
        *[
            client.post(path + "/versions", headers=a, json={"draft_revision": 2})
            for a in (auth, other) * 5
        ]
    )
    assert all(r.status_code == 201 for r in results)
    assert sorted(r.json()["version"] for r in results) == list(range(1, 11))
    assert (
        await client.post(path + "/versions", headers=auth, json={"draft_revision": 1})
    ).status_code == 409


async def test_workflow_boundaries(setup):
    client, _, first, *_ = setup
    auth, _, path = await resource(client, first)
    for data, content_type, status in [
        ("{", "application/json", 422),
        ("NaN", "application/json", 422),
        ("{}", "text/plain", 415),
        ('{"expected_revision":1,"dag":[]}', "application/json", 422),
        ('{"dag":"' + "x" * 262144 + '"}', "application/json", 413),
    ]:
        r = await client.put(
            path + "/draft", headers={**auth, "Content-Type": content_type}, content=data
        )
        assert r.status_code == status, r.text
    r = await client.put(
        path + "/draft", headers=auth, json={"expected_revision": 1, "dag": {"unknown": True}}
    )
    assert r.status_code == 200
    assert (
        await client.post(path + "/versions", headers=auth, json={"draft_revision": 2})
    ).status_code == 422


async def test_save_racing_publish_is_revision_consistent(setup):
    client, _, first, second, *_ = setup
    auth, root, path = await resource(client, first)
    other = await sign_in(client, second)
    await client.post(root + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"})
    await client.put(path + "/draft", headers=auth, json={"expected_revision": 1, "dag": example()})
    saved, published = await asyncio.gather(
        client.put(path + "/draft", headers=other, json={"expected_revision": 2, "dag": {}}),
        client.post(path + "/versions", headers=auth, json={"draft_revision": 2}),
    )
    assert saved.status_code == 200
    assert published.status_code in (201, 409)
    if published.status_code == 201:
        assert published.json()["source_draft_revision"] == 2
        assert len(published.json()["dag"]["nodes"]) == 5
    assert (await client.get(path + "/draft", headers=auth)).json()["dag"] == {}


async def test_chunked_unicode_limits_and_unknown_workflow(setup):
    client, _, first, *_ = setup
    auth, _, path = await resource(client, first)
    from uuid import uuid4

    assert (await client.get("/api/v1/workflows/" + str(uuid4()), headers=auth)).status_code == 404
    prefix = b'{"expected_revision":1,"dag":{"text":"'
    suffix = b'"}}'
    payload = prefix + ("?" * 1000).encode() + suffix
    payload += b" " * (256 * 1024 - len(payload))

    async def chunks(data):
        for i in range(0, len(data), 12345):
            yield data[i : i + 12345]

    headers = {**auth, "Content-Type": "application/json"}
    assert (
        await client.put(path + "/draft", headers=headers, content=chunks(payload))
    ).status_code == 200
    assert (
        await client.put(path + "/draft", headers=headers, content=chunks(payload + b" "))
    ).status_code == 413


@pytest.mark.parametrize("value", ["1e999", '"\\u0000"', '"\\ud800"'])
async def test_jsonb_incompatible_values_are_validation_errors(setup, value):
    client, _, first, *_ = setup
    auth, _, path = await resource(client, first)
    body = '{"expected_revision":1,"dag":{"value":' + value + "}}"
    r = await client.put(
        path + "/draft", headers={**auth, "Content-Type": "application/json"}, content=body
    )
    assert r.status_code == 422, r.text
    assert (await client.get(path + "/draft", headers=auth)).json()["revision"] == 1
