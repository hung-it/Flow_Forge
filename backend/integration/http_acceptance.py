"""Real TCP smoke; only invoked by the isolated Compose harness."""

import asyncio
import http.cookiejar
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.auth.service import bootstrap
from flowforge.config import Settings


async def seed(emails: list[str], password: str) -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        for email in emails:
            await bootstrap(engine, email, password)
    finally:
        await engine.dispose()


def main() -> None:
    suffix, password = secrets.token_hex(8), secrets.token_urlsafe(24)
    emails = [f"http-owner-{suffix}@example.com", f"http-member-{suffix}@example.com"]
    asyncio.run(seed(emails, password))
    owner, member = [
        urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        for _ in range(2)
    ]

    def call(client, method, path, body=None, token=None, key=None):
        headers = {"Content-Type": "application/json", "X-FlowForge-CSRF": "1"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if key is not None:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            "http://localhost:8000/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with client.open(request, timeout=10) as response:
                payload = response.read()
                return response.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    status, payload = call(owner, "POST", "/auth/login", {"email": emails[0], "password": password})
    assert status == 200
    token = payload["access_token"]
    status, payload = call(
        member, "POST", "/auth/login", {"email": emails[1], "password": password}
    )
    assert status == 200
    member_token = payload["access_token"]
    assert call(owner, "GET", "/auth/me", token=token)[0] == 200
    status, project = call(owner, "POST", "/projects", {"name": "HTTP acceptance"}, token)
    assert status == 201
    path = "/projects/" + project["id"]
    assert call(member, "GET", path, token=member_token)[0] == 404
    assert (
        call(owner, "POST", path + "/members", {"email": emails[1], "role": "VIEWER"}, token)[0]
        == 201
    )
    assert call(member, "GET", path, token=member_token)[0] == 200
    assert call(member, "PATCH", path, {"name": "Denied"}, member_token)[0] == 403
    status, workflow = call(owner, "POST", path + "/workflows", {"name": "HTTP workflow"}, token)
    assert status == 201
    workflow_path = "/workflows/" + workflow["id"]
    assert call(owner, "POST", workflow_path + "/versions", {"draft_revision": 1}, token)[0] == 422
    dag = json.loads((Path(__file__).parents[2] / "examples/document-workflow.json").read_text())
    assert (
        call(owner, "PUT", workflow_path + "/draft", {"expected_revision": 1, "dag": dag}, token)[0]
        == 200
    )
    status, version = call(owner, "POST", workflow_path + "/versions", {"draft_revision": 2}, token)
    assert status == 201 and version["version"] == 1
    assert call(owner, "POST", workflow_path + "/versions", {"draft_revision": 2}, token)[0] == 201
    assert (
        call(
            member,
            "PUT",
            workflow_path + "/draft",
            {"expected_revision": 2, "dag": {}},
            member_token,
        )[0]
        == 403
    )
    assert call(member, "GET", workflow_path + "/versions/1", token=member_token)[1] == version
    assert (
        call(owner, "PUT", workflow_path + "/draft", {"expected_revision": 2, "dag": {}}, token)[0]
        == 200
    )
    assert call(owner, "GET", workflow_path + "/versions/1", token=token)[1] == version
    runs_path = workflow_path + "/runs"
    key = "smoke-" + suffix
    assert call(owner, "POST", runs_path, {"version": 1, "input": {}}, token, key)[0] == 422
    run_body = {"version": 1, "input": {"document": {"title": "Smoke", "content": "Example"}}}
    status, created = call(owner, "POST", runs_path, run_body, token, key)
    assert status == 202 and created["status"] == "PENDING"
    assert call(owner, "POST", runs_path, run_body, token, key) == (202, created)
    conflict = {**run_body, "version": 2}
    assert call(owner, "POST", runs_path, conflict, token, key)[0] == 409
    assert call(member, "POST", runs_path, run_body, member_token, key)[0] == 403
    run_path = "/runs/" + created["run_id"]
    status, metadata = call(member, "GET", run_path, token=member_token)
    assert status == 200 and metadata["workflow_version_id"] == version["id"]
    assert metadata["run_id"] == UUID(created["run_id"]).int
    status, tasks = call(member, "GET", run_path + "/tasks", token=member_token)
    assert status == 200 and len(tasks) == 5
    assert all(task["status"] == "PENDING" and task["attempt_count"] == 0 for task in tasks)
    nodes = {task["task_id"]: task["node_key"] for task in tasks}
    edges = {(nodes[p], task["node_key"]) for task in tasks for p in task["predecessor_task_ids"]}
    assert edges == {tuple(edge) for edge in dag["edges"]}
    cancel_path = run_path + "/cancel"
    assert call(member, "POST", cancel_path, token=member_token)[0] == 403
    status, canceled = call(owner, "POST", cancel_path, token=token)
    assert status == 200 and canceled["status"] == "CANCELED"
    assert canceled["started_at"] is None and canceled["finished_at"] is not None
    assert call(owner, "POST", cancel_path, token=token) == (200, canceled)
    assert call(member, "GET", run_path, token=member_token) == (
        200,
        {**canceled, "run_id": UUID(created["run_id"]).int},
    )
    status, canceled_tasks = call(member, "GET", run_path + "/tasks", token=member_token)
    expected_tasks = [{**task, "status": "CANCELED"} for task in tasks]
    assert status == 200 and canceled_tasks == expected_tasks
    assert call(owner, "POST", runs_path, run_body, token, key) == (
        202, {**created, "status": "CANCELED"}
    )
    status, payload = call(owner, "POST", "/auth/refresh", {})
    assert status == 200
    new_token = payload["access_token"]
    assert call(owner, "POST", "/auth/logout", {})[0] == 204
    assert call(owner, "GET", "/auth/me", token=new_token)[0] == 401
    assert call(member, "GET", "/auth/me", token=member_token)[0] == 200
    print("PASS: HTTP auth/project RBAC, workflow publication, run idempotency and cancellation")


if __name__ == "__main__":
    main()
