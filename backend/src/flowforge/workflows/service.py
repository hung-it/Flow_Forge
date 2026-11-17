import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flowforge.auth.service import Principal
from flowforge.errors import ApiError
from flowforge.projects.service import visible

from .publication import snapshot

EDITORS = ("OWNER", "DEVELOPER")


def decoded(row: RowMapping) -> dict[str, Any]:
    result = dict(row)
    for key in ("dag", "handler_contracts"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


async def workflow(
    conn: AsyncConnection, actor: Principal, ident: UUID, write: bool = False
) -> RowMapping:
    project = await conn.scalar(
        text("SELECT project_id FROM workflow_definitions WHERE id=:id"), {"id": ident}
    )
    if project is None:
        raise ApiError(404, "NOT_FOUND", "Workflow not found")
    await visible(conn, actor, project, write=write, roles=EDITORS)
    suffix = " FOR UPDATE" if write else ""
    return (
        (
            await conn.execute(
                text("SELECT * FROM workflow_definitions WHERE id=:id" + suffix), {"id": ident}
            )
        )
        .mappings()
        .one()
    )


async def create(engine: AsyncEngine, actor: Principal, project: UUID, name: str) -> dict[str, Any]:
    async with engine.begin() as conn:
        await visible(conn, actor, project, write=True, roles=EDITORS)
        ident = uuid4()
        row = (
            (
                await conn.execute(
                    text("""INSERT INTO workflow_definitions
          (id,project_id,name,created_by) VALUES(:id,:p,:n,:u) RETURNING *"""),
                    dict(id=ident, p=project, n=name, u=actor.user_id),
                )
            )
            .mappings()
            .one()
        )
        dag = dict(schema_version=1, name=name, nodes=[], edges=[])
        await conn.execute(
            text("""INSERT INTO workflow_drafts
          (workflow_id,dag,revision,updated_by) VALUES(:id,CAST(:dag AS jsonb),1,:u)"""),
            dict(id=ident, dag=json.dumps(dag), u=actor.user_id),
        )
        return {**row, "latest_version": None}


async def listing(
    engine: AsyncEngine, actor: Principal, project: UUID, limit: int, offset: int
) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        await visible(conn, actor, project)
        rows = (
            await conn.execute(
                text("""SELECT w.*,
          (SELECT max(version) FROM workflow_versions v WHERE v.workflow_id=w.id) AS latest_version
          FROM workflow_definitions w WHERE project_id=:p
          ORDER BY created_at,id LIMIT :lim OFFSET :off"""),
                dict(p=project, lim=limit, off=offset),
            )
        ).mappings()
        return [dict(row) for row in rows]


async def detail(
    engine: AsyncEngine, actor: Principal, ident: UUID, name: str | None = None
) -> dict[str, Any]:
    async with engine.begin() as conn:
        row = await workflow(conn, actor, ident, write=name is not None)
        if name is not None:
            row = (
                (
                    await conn.execute(
                        text("""UPDATE workflow_definitions
              SET name=:n,updated_at=clock_timestamp() WHERE id=:id RETURNING *"""),
                        dict(n=name, id=ident),
                    )
                )
                .mappings()
                .one()
            )
        latest = await conn.scalar(
            text("SELECT max(version) FROM workflow_versions WHERE workflow_id=:id"), dict(id=ident)
        )
        return {**row, "latest_version": latest}


async def draft(
    engine: AsyncEngine,
    actor: Principal,
    ident: UUID,
    dag: dict[str, Any] | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    async with engine.begin() as conn:
        await workflow(conn, actor, ident, write=dag is not None)
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM workflow_drafts WHERE workflow_id=:id"), dict(id=ident)
                )
            )
            .mappings()
            .one()
        )
        if dag is not None:
            if row["revision"] != revision:
                raise ApiError(409, "DRAFT_REVISION_CONFLICT", "Reload the current draft")
            row = (
                (
                    await conn.execute(
                        text("""UPDATE workflow_drafts
              SET dag=CAST(:dag AS jsonb),revision=revision+1,updated_by=:u,
              updated_at=clock_timestamp() WHERE workflow_id=:id RETURNING *"""),
                        dict(dag=json.dumps(dag, allow_nan=False), u=actor.user_id, id=ident),
                    )
                )
                .mappings()
                .one()
            )
        return decoded(row)


async def publish(
    engine: AsyncEngine, actor: Principal, ident: UUID, revision: int
) -> dict[str, Any]:
    async with engine.begin() as conn:
        await workflow(conn, actor, ident, write=True)
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM workflow_drafts WHERE workflow_id=:id"), dict(id=ident)
                )
            )
            .mappings()
            .one()
        )
        if row["revision"] != revision:
            raise ApiError(409, "DRAFT_REVISION_CONFLICT", "Reload the current draft")
        try:
            dag, contracts, checksum = snapshot(decoded(row)["dag"])
        except ValueError as exc:
            raise ApiError(422, "WORKFLOW_INVALID", "Draft cannot be published") from exc
        version = await conn.scalar(
            text("""SELECT coalesce(max(version),0)+1
          FROM workflow_versions WHERE workflow_id=:id"""),
            dict(id=ident),
        )
        result = (
            (
                await conn.execute(
                    text("""INSERT INTO workflow_versions
          (id,workflow_id,version,dag,checksum,handler_contracts,source_draft_revision,created_by)
          VALUES(:v,:id,:num,CAST(:dag AS jsonb),:hash,CAST(:contracts AS jsonb),:rev,:u)
          RETURNING *"""),
                    dict(
                        v=uuid4(),
                        id=ident,
                        num=version,
                        dag=json.dumps(dag),
                        hash=checksum,
                        contracts=json.dumps(contracts),
                        rev=revision,
                        u=actor.user_id,
                    ),
                )
            )
            .mappings()
            .one()
        )
        return decoded(result)


async def versions(
    engine: AsyncEngine, actor: Principal, ident: UUID, limit: int, offset: int
) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        await workflow(conn, actor, ident)
        rows = (
            await conn.execute(
                text("""SELECT id,workflow_id,version,checksum,
          source_draft_revision,created_by,created_at FROM workflow_versions
          WHERE workflow_id=:id ORDER BY version DESC LIMIT :lim OFFSET :off"""),
                dict(id=ident, lim=limit, off=offset),
            )
        ).mappings()
        return [dict(row) for row in rows]


async def version_detail(
    engine: AsyncEngine, actor: Principal, ident: UUID, number: int
) -> dict[str, Any]:
    async with engine.connect() as conn:
        await workflow(conn, actor, ident)
        row = (
            (
                await conn.execute(
                    text("""SELECT * FROM workflow_versions
          WHERE workflow_id=:id AND version=:n"""),
                    dict(id=ident, n=number),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Version not found")
        return decoded(row)
