"""Real PostgreSQL/HTTP acceptance; run only against a disposable migrated DB."""

import asyncio
import secrets
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.api import create_app
from flowforge.auth import service as auth_service
from flowforge.auth.security import COOKIE_NAME, read_access, token_hash
from flowforge.auth.service import bootstrap
from flowforge.config import Settings
from flowforge.errors import ApiError
from flowforge.projects import service as project_service

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


async def test_login_rotation_logout(setup):
    client, engine, first, *_ = setup
    auth = await sign_in(client, " " + first.upper() + " ")
    assert (await client.get("/api/v1/auth/me", headers=auth)).json()["email"] == first
    refreshed = await client.post("/api/v1/auth/refresh", headers=CSRF, json={})
    assert refreshed.status_code == 200
    assert "refresh_token" not in refreshed.json()
    assert "HttpOnly" in refreshed.headers["set-cookie"]
    assert (await client.post("/api/v1/auth/logout", headers=CSRF, json={})).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401
    assert (await client.post("/api/v1/auth/logout", headers=CSRF, json={})).status_code == 204


async def test_project_permissions_and_transfer(setup):
    client, _, first, second, owner, member = setup
    owner_auth = await sign_in(client, first)
    member_auth = await sign_in(client, second)
    created = await client.post("/api/v1/projects", headers=owner_auth, json={"name": "Demo"})
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    path = f"/api/v1/projects/{pid}"
    assert (await client.get(path, headers=member_auth)).status_code == 404
    assert (
        await client.post(
            path + "/members", headers=owner_auth, json={"email": second, "role": "VIEWER"}
        )
    ).status_code == 201
    assert (
        await client.patch(path, headers=member_auth, json={"name": "Hacked"})
    ).status_code == 403
    assert (await client.delete(path + f"/members/{owner}", headers=owner_auth)).status_code == 409
    assert (
        await client.post(
            path + "/transfer-ownership", headers=owner_auth, json={"user_id": str(member)}
        )
    ).status_code == 200
    assert (
        await client.patch(path, headers=owner_auth, json={"name": "Denied"})
    ).status_code == 403
    assert (
        await client.patch(path, headers=member_auth, json={"name": "Updated"})
    ).status_code == 200
    assert (await client.delete(path + f"/members/{owner}", headers=member_auth)).status_code == 204
    assert (await client.get(path, headers=owner_auth)).status_code == 404


async def test_replay_commits_revocation_but_preserves_other_session(setup):
    client, _, first, *_ = setup
    first_auth = await sign_in(client, first)
    old = client.cookies.get(COOKIE_NAME)
    new_session_auth = await sign_in(client, first)
    other = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    headers = {**CSRF, "Cookie": f"{COOKIE_NAME}={old}"}
    rotated = await client.post("/api/v1/auth/refresh", headers=headers, json={})
    assert rotated.status_code == 200
    rotated_auth = {"Authorization": "Bearer " + rotated.json()["access_token"]}
    assert (await client.post("/api/v1/auth/refresh", headers=headers, json={})).status_code == 401
    for revoked in (first_auth, rotated_auth):
        assert (await client.get("/api/v1/auth/me", headers=revoked)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=new_session_auth)).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/refresh", headers={**CSRF, "Cookie": f"{COOKIE_NAME}={other}"}, json={}
        )
    ).status_code == 200


async def test_concurrent_refresh_revokes_winner_session(setup):
    client, engine, first, *_ = setup
    auth = await sign_in(client, first)
    raw = client.cookies.get(COOKIE_NAME)
    results = await asyncio.gather(
        auth_service.refresh(engine, raw), auth_service.refresh(engine, raw), return_exceptions=True
    )
    assert sum(isinstance(result, ApiError) and result.status == 401 for result in results) == 1
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401


async def test_unknown_token_does_not_revoke_and_logout_is_session_scoped(setup):
    client, _, first, *_ = setup
    auth1 = await sign_in(client, first)
    raw1 = client.cookies.get(COOKIE_NAME)
    auth2 = await sign_in(client, first)
    assert (
        await client.post(
            "/api/v1/auth/refresh",
            headers={**CSRF, "Cookie": f"{COOKIE_NAME}=random-unknown"},
            json={},
        )
    ).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=auth1)).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={**CSRF, "Cookie": f"{COOKIE_NAME}={raw1}"}, json={}
        )
    ).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=auth1)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=auth2)).status_code == 200


async def test_expired_refresh_and_logout_with_expired_access(setup):
    client, engine, first, *_ = setup
    auth = await sign_in(client, first)
    user, sid = read_access(client_transport_auth(client), auth["Authorization"].split()[1])
    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE auth_sessions SET created_at=now()-interval '8 days',
            expires_at=now()-interval '1 day' WHERE id=:s"""),
            dict(s=sid),
        )
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF, json={})).status_code == 401
    auth = await sign_in(client, first)
    # Logout relies on the refresh cookie, not on an access token still being valid.
    config = client_transport_auth(client)
    claims = jwt.decode(auth["Authorization"].split()[1], options={"verify_signature": False})
    claims.update(iat=0, exp=1)
    expired = jwt.encode(claims, config.jwt_secret.get_secret_value(), algorithm="HS256")
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={**CSRF, "Authorization": "Bearer " + expired}, json={}
        )
    ).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401


def client_transport_auth(client):
    return client._transport.app.state.auth


async def test_secure_cookie_and_tokens_do_not_leak(setup, caplog):
    client, engine, first, *_ = setup
    client._transport.app.state.auth.cookie_secure = True
    client.base_url = httpx.URL("https://localhost")
    response = await client.post(
        "/api/v1/auth/login", headers=CSRF, json={"email": first, "password": PASSWORD}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert all(
        value in cookie for value in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth")
    )
    assert "Domain=" not in cookie
    raw = client.cookies.get(COOKIE_NAME)
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF, json={})).status_code == 200
    async with engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM refresh_tokens WHERE token_hash=:h"),
                dict(h=token_hash(raw)),
            )
            == 1
        )
    assert PASSWORD not in caplog.text and raw not in caplog.text


async def test_concurrent_logins_share_atomic_limit(setup):
    _, engine, first, _, owner, _ = setup
    ip = "rate-race-" + secrets.token_hex(8)
    async with engine.begin() as conn:
        for delta in (0, 1):
            await conn.execute(
                text("""INSERT INTO login_counters(ip,window_start,attempts)
                VALUES(:ip,date_trunc('minute',clock_timestamp())
                + make_interval(mins => :d),9)"""),
                dict(ip=ip, d=delta),
            )
    results = await asyncio.gather(
        auth_service.login(engine, first, PASSWORD, ip),
        auth_service.login(engine, first, PASSWORD, ip),
        return_exceptions=True,
    )
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, ApiError) and result.status == 429 for result in results) == 1
    async with engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM auth_sessions WHERE user_id=:u"), dict(u=owner)
            )
            == 1
        )


async def test_logout_racing_refresh_always_revokes_session(setup):
    client, engine, first, *_ = setup
    auth = await sign_in(client, first)
    raw = client.cookies.get(COOKIE_NAME)
    refreshed, logged_out = await asyncio.gather(
        auth_service.refresh(engine, raw), auth_service.logout(engine, raw), return_exceptions=True
    )
    assert logged_out is None
    assert isinstance(refreshed, tuple) or isinstance(refreshed, ApiError)
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401
    if isinstance(refreshed, tuple):
        with pytest.raises(ApiError) as error:
            await auth_service.refresh(engine, refreshed[2])
        assert error.value.status == 401


@pytest.mark.parametrize(
    "kind,expected",
    [("missing", 403), ("origin", 403), ("type", 415), ("body", 422), ("oversize", 413)],
)
async def test_request_boundary(setup, kind, expected):
    client, _, first, *_ = setup
    headers = dict(CSRF)
    payload = {"email": first, "password": PASSWORD}
    if kind == "missing":
        headers.clear()
    elif kind == "origin":
        headers["Origin"] = "https://attacker.example"
    elif kind == "body":
        payload["admin"] = True
    elif kind == "oversize":
        payload["password"] = "SECRET" * 2000
    if kind == "type":
        response = await client.post("/api/v1/auth/refresh", headers=headers, content="{}")
    else:
        response = await client.post("/api/v1/auth/login", headers=headers, json=payload)
    assert response.status_code == expected, response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert PASSWORD not in response.text


async def test_bootstrap_duplicate_concurrent_and_password_unchanged(setup):
    client, engine, first, *_ = setup
    with pytest.raises(ApiError) as error:
        await bootstrap(engine, first.upper(), "Different password!!")
    assert error.value.status == 409
    assert (await sign_in(client, first))["Authorization"]
    email = f"race-{uuid4()}@example.com"
    results = await asyncio.gather(
        bootstrap(engine, email, PASSWORD),
        bootstrap(engine, email, PASSWORD),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApiError) for result in results) == 1
    async with engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT count(*) FROM users WHERE email=:e"), dict(e=email))
        ) == 1


async def test_bad_credentials_and_rate_limit(setup):
    client, engine, first, *_ = setup
    for email in (first, "missing@example.com"):
        response = await client.post(
            "/api/v1/auth/login", headers=CSRF, json={"email": email, "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"
    # Deterministically fill the current DB bucket instead of sleeping near a minute boundary.
    ip = client._transport.client[0]
    async with engine.begin() as conn:
        await conn.execute(
            text("""INSERT INTO login_counters(ip,window_start,attempts)
            VALUES(:ip,date_trunc('minute',clock_timestamp()),10)
            ON CONFLICT(ip,window_start) DO UPDATE SET attempts=10"""),
            dict(ip=ip),
        )
    limited = await client.post(
        "/api/v1/auth/login", headers=CSRF, json={"email": first, "password": PASSWORD}
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


async def test_project_listing_membership_errors_and_roles(setup):
    client, _, first, second, owner, member = setup
    auth = await sign_in(client, first)
    outsider = await sign_in(client, second)
    project = (await client.post("/api/v1/projects", headers=auth, json={"name": " Test "})).json()
    pid, path = project["id"], "/api/v1/projects/" + project["id"]
    assert project["name"] == "Test" and project["owner_id"] == str(owner)
    assert (await client.get("/api/v1/projects", headers=outsider)).json() == []
    assert (await client.get("/api/v1/projects?limit=1&offset=0", headers=auth)).json()[0][
        "id"
    ] == pid
    assert (await client.get("/api/v1/projects?limit=101", headers=auth)).status_code == 422
    assert (await client.get(path + "/members", headers=outsider)).status_code == 404
    assert (
        await client.post(
            path + "/members", headers=auth, json={"email": "missing@example.com", "role": "VIEWER"}
        )
    ).status_code == 404
    assert (
        await client.post(path + "/members", headers=auth, json={"email": second, "role": "OWNER"})
    ).status_code == 422
    assert (
        await client.post(
            path + "/members", headers=auth, json={"email": second, "role": "DEVELOPER"}
        )
    ).status_code == 201
    assert (
        await client.post(path + "/members", headers=auth, json={"email": second, "role": "VIEWER"})
    ).status_code == 409
    rows = (await client.get(path + "/members", headers=outsider)).json()
    assert len(rows) == 2
    assert (
        await client.post(
            path + "/members", headers=outsider, json={"email": first, "role": "VIEWER"}
        )
    ).status_code == 403
    assert (
        await client.patch(path + f"/members/{member}", headers=auth, json={"role": "VIEWER"})
    ).status_code == 200
    assert (
        await client.patch(path + f"/members/{owner}", headers=auth, json={"role": "VIEWER"})
    ).status_code == 409
    assert (await client.delete(path + f"/members/{uuid4()}", headers=auth)).status_code == 404
    assert (
        await client.post(
            path + "/transfer-ownership", headers=auth, json={"user_id": str(uuid4())}
        )
    ).status_code == 404
    assert (
        await client.post(path + "/transfer-ownership", headers=auth, json={"user_id": str(owner)})
    ).status_code == 409
    assert (await client.patch(path, headers=auth, json={"name": " "})).status_code == 422


async def test_concurrent_transfer_and_membership_updates(setup):
    client, engine, first, second, _, member = setup
    auth = await sign_in(client, first)
    user, sid = read_access(client_transport_auth(client), auth["Authorization"].split()[1])
    actor = auth_service.Principal(user, sid, first)
    pid = (await project_service.create(engine, actor, "Concurrent"))["id"]
    results = await asyncio.gather(
        project_service.add_member(engine, actor, pid, second, "VIEWER"),
        project_service.add_member(engine, actor, pid, second, "VIEWER"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApiError) and result.status == 409 for result in results) == 1
    transfers = await asyncio.gather(
        project_service.transfer(engine, actor, pid, member),
        project_service.transfer(engine, actor, pid, member),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApiError) and result.status == 403 for result in transfers) == 1
    async with engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT owner_id FROM projects WHERE id=:p"), dict(p=pid))
            == member
        )
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM project_members WHERE project_id=:p AND role='OWNER'"),
                dict(p=pid),
            )
            == 1
        )


async def test_database_outage_does_not_become_invalid_credentials(setup):
    client, _, first, *_ = setup
    auth = await sign_in(client, first)
    app = client._transport.app
    original = app.state.engine
    unavailable = create_async_engine("postgresql+asyncpg://unused:unused@127.0.0.1:1/unavailable")
    app.state.engine = unavailable
    try:
        response = await client.get("/api/v1/auth/me", headers=auth)
        assert response.status_code == 503
        assert "unused" not in response.text
    finally:
        app.state.engine = original
        await unavailable.dispose()
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 200


async def test_creation_rolls_back_if_membership_insert_fails(setup):
    client, engine, first, *_ = setup
    auth = await sign_in(client, first)
    name = "reject-" + secrets.token_hex(8)
    async with engine.begin() as conn:
        await conn.execute(
            text("""CREATE OR REPLACE FUNCTION reject_test_membership()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF EXISTS(SELECT 1 FROM projects WHERE id=NEW.project_id AND name LIKE 'reject-%')
            THEN RAISE EXCEPTION 'injected membership failure'; END IF; RETURN NEW; END $$""")
        )
        await conn.execute(
            text("""CREATE TRIGGER reject_test_membership BEFORE INSERT
            ON project_members FOR EACH ROW EXECUTE FUNCTION reject_test_membership()""")
        )
    try:
        response = await client.post("/api/v1/projects", headers=auth, json={"name": name})
        assert response.status_code == 503
        async with engine.connect() as conn:
            assert (
                await conn.scalar(text("SELECT count(*) FROM projects WHERE name=:n"), dict(n=name))
                == 0
            )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER reject_test_membership ON project_members"))
            await conn.execute(text("DROP FUNCTION reject_test_membership()"))


async def test_permissions_are_rechecked_after_waiting_for_project_lock(setup):
    client, engine, first, second, _, member = setup
    owner_auth = await sign_in(client, first)
    member_auth = await sign_in(client, second)
    user, sid = read_access(client_transport_auth(client), member_auth["Authorization"].split()[1])
    member_actor = auth_service.Principal(user, sid, second)
    pid = (await client.post("/api/v1/projects", headers=owner_auth, json={"name": "Race"})).json()[
        "id"
    ]
    await client.post(
        f"/api/v1/projects/{pid}/members",
        headers=owner_auth,
        json={"email": second, "role": "DEVELOPER"},
    )
    # Transfer under a held project lock. The old owner request must see the new role after waiting.
    owner_id, owner_sid = read_access(
        client_transport_auth(client), owner_auth["Authorization"].split()[1]
    )
    owner_actor = auth_service.Principal(owner_id, owner_sid, first)
    async with engine.begin() as conn:
        await conn.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), dict(p=pid))
        pending = asyncio.create_task(
            project_service.rename(engine, owner_actor, pid, "Must not apply")
        )

        async def waiting_on_lock():
            while True:
                async with engine.connect() as observer:
                    waiting = await observer.scalar(
                        text("""SELECT count(*) FROM pg_stat_activity
                        WHERE datname=current_database() AND wait_event_type='Lock'
                        AND query LIKE '%FROM projects WHERE id=%FOR UPDATE%'""")
                    )
                if waiting:
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(waiting_on_lock(), timeout=10)
        await conn.execute(
            text(
                "UPDATE project_members SET role='DEVELOPER' WHERE project_id=:p AND role='OWNER'"
            ),
            dict(p=pid),
        )
        await conn.execute(
            text("UPDATE project_members SET role='OWNER' WHERE project_id=:p AND user_id=:u"),
            dict(p=pid, u=member),
        )
        await conn.execute(
            text("UPDATE projects SET owner_id=:u WHERE id=:p"), dict(p=pid, u=member)
        )
    with pytest.raises(ApiError) as error:
        await asyncio.wait_for(pending, timeout=10)
    assert error.value.status == 403
    assert (await project_service.detail(engine, member_actor, UUID(pid)))["name"] == "Race"
