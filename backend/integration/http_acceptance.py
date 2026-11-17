"""Real TCP smoke; only invoked by the isolated Compose harness."""

import asyncio
import http.cookiejar
import json
import secrets
import urllib.error
import urllib.request

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

    def call(client, method, path, body=None, token=None):
        headers = {"Content-Type": "application/json", "X-FlowForge-CSRF": "1"}
        if token:
            headers["Authorization"] = "Bearer " + token
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
    status, payload = call(owner, "POST", "/auth/refresh", {})
    assert status == 200
    new_token = payload["access_token"]
    assert call(owner, "POST", "/auth/logout", {})[0] == 204
    assert call(owner, "GET", "/auth/me", token=new_token)[0] == 401
    assert call(member, "GET", "/auth/me", token=member_token)[0] == 200
    print("PASS: real HTTP login/me/refresh/logout, session isolation and project RBAC")


if __name__ == "__main__":
    main()
