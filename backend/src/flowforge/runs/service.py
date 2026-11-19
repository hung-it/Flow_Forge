import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flowforge.auth.service import Principal
from flowforge.contracts import WorkflowSpec
from flowforge.errors import ApiError
from flowforge.projects.service import visible
from flowforge.workflows.service import EDITORS

from .input import fingerprint, resolve_pointer


async def create(
    engine: AsyncEngine,
    actor: Principal,
    workflow_id: UUID,
    version: int,
    value: dict[str, JsonValue],
    key: str | None,
) -> dict[str, Any]:
    digest = fingerprint(workflow_id, version, value)
    async with engine.begin() as conn:
        project_id = await conn.scalar(
            text("SELECT project_id FROM workflow_definitions WHERE id=:id"), {"id": workflow_id}
        )
        if project_id is None:
            raise ApiError(404, "NOT_FOUND", "Workflow not found")
        await visible(conn, actor, project_id, write=True, roles=EDITORS)
        published = (
            (
                await conn.execute(
                    text("""SELECT id, CAST(dag AS text) AS dag FROM workflow_versions
                        WHERE workflow_id=:workflow AND version=:number"""),
                    {"workflow": workflow_id, "number": version},
                )
            )
            .mappings()
            .first()
        )
        if published is None:
            raise ApiError(404, "NOT_FOUND", "Workflow version not found")
        spec = WorkflowSpec.model_validate_json(published["dag"])
        for node in spec.nodes:
            for binding in node.inputs.values():
                if binding.source == "run_input":
                    try:
                        resolve_pointer(value, binding.pointer)
                    except ValueError as error:
                        raise ApiError(
                            422,
                            "INPUT_BINDING_INVALID",
                            f"Run input does not resolve a binding for task '{node.key}'",
                        ) from error

        run_id = uuid4()
        created = (
            (
                await conn.execute(
                    text("""INSERT INTO workflow_runs
                        (id, project_id, workflow_id, workflow_version_id, created_by,
                         input_json, input_hash, idempotency_key)
                        VALUES (:id,:project,:workflow,:version,:actor,CAST(:input AS jsonb),
                                :hash,:key)
                        ON CONFLICT (project_id,idempotency_key)
                        WHERE idempotency_key IS NOT NULL DO NOTHING
                        RETURNING id AS run_id, status"""),
                    {
                        "id": run_id,
                        "project": project_id,
                        "workflow": workflow_id,
                        "version": published["id"],
                        "actor": actor.user_id,
                        "input": json.dumps(value, ensure_ascii=False, allow_nan=False),
                        "hash": digest,
                        "key": key,
                    },
                )
            )
            .mappings()
            .first()
        )
        if created is None:
            # READ COMMITTED takes a fresh snapshot for this statement.
            original = (
                (
                    await conn.execute(
                        text("""SELECT id AS run_id,status,input_hash FROM workflow_runs
                            WHERE project_id=:project AND idempotency_key=:key"""),
                        {"project": project_id, "key": key},
                    )
                )
                .mappings()
                .one()
            )
            if original["input_hash"] != digest:
                raise ApiError(
                    409, "IDEMPOTENCY_KEY_CONFLICT", "Key already used for a different run"
                )
            return {"run_id": original["run_id"], "status": original["status"]}

        task_ids = {node.key: uuid4() for node in spec.nodes}
        await conn.execute(
            text("INSERT INTO task_runs(id,run_id,node_key) VALUES (:id,:run,:node)"),
            [{"id": task_id, "run": run_id, "node": node} for node, task_id in task_ids.items()],
        )
        if spec.edges:
            await conn.execute(
                text("""INSERT INTO task_dependencies(run_id,predecessor_task_id,successor_task_id)
                        VALUES (:run,:before,:after)"""),
                [
                    {"run": run_id, "before": task_ids[before], "after": task_ids[after]}
                    for before, after in spec.edges
                ],
            )
        return dict(created)


RUN_METADATA = """SELECT r.id AS run_id,r.project_id,r.workflow_id,r.workflow_version_id,
       v.version,r.created_by,r.status,r.created_at,r.started_at,r.finished_at
FROM workflow_runs r
JOIN workflow_versions v ON v.id=r.workflow_version_id AND v.workflow_id=r.workflow_id
JOIN project_members m ON m.project_id=r.project_id AND m.user_id=:user_id"""


async def list_runs(
    engine: AsyncEngine, actor: Principal, project_id: UUID | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        query = RUN_METADATA
        if project_id is not None:
            await visible(conn, actor, project_id)
            query += " WHERE r.project_id=:project"
        rows = await conn.execute(
            text(query + " ORDER BY r.created_at DESC,r.id DESC LIMIT :limit OFFSET :offset"),
            {"user_id": actor.user_id, "project": project_id, "limit": limit, "offset": offset},
        )
        return [dict(row) for row in rows.mappings()]


async def _visible_run(
    conn: AsyncConnection, actor: Principal, run_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    query = RUN_METADATA + " WHERE r.id=:id" + (" FOR UPDATE OF r" if lock else "")
    row = (
        (
            await conn.execute(
                text(query),
                {"user_id": actor.user_id, "id": run_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Run not found")
    return dict(row)


async def detail(engine: AsyncEngine, actor: Principal, run_id: UUID) -> dict[str, Any]:
    async with engine.connect() as conn:
        return await _visible_run(conn, actor, run_id)


async def cancel(engine: AsyncEngine, actor: Principal, run_id: UUID) -> dict[str, Any]:
    params = {"id": run_id}
    async with engine.begin() as conn:
        project = await conn.scalar(
            text("SELECT project_id FROM workflow_runs WHERE id=:id"), params
        )
        if project is None:
            raise ApiError(404, "NOT_FOUND", "Run not found")
        membership = await visible(conn, actor, project, write=True, roles=EDITORS)
        run = await _visible_run(conn, actor, run_id, lock=True)
        if membership["role"] == "DEVELOPER" and run["created_by"] != actor.user_id:
            raise ApiError(403, "FORBIDDEN", "Only the project owner or run creator can cancel")
        if run["status"] not in ("PENDING", "RUNNING"):
            return run

        # Scheduler, publisher preflight and worker operations also take the run lock first.
        await conn.execute(
            text("SELECT id FROM task_runs WHERE run_id=:id ORDER BY id FOR UPDATE"), params
        )
        finished = await conn.scalar(text("SELECT clock_timestamp()"))
        await conn.execute(
            text("""UPDATE task_attempts a SET status='CANCELED',finished_at=:finished
            FROM task_runs t WHERE t.id=a.task_run_id AND t.run_id=:id AND a.status='RUNNING'"""),
            {**params, "finished": finished},
        )
        await conn.execute(
            text("""UPDATE task_runs SET status='CANCELED',lease_token=NULL,lease_until=NULL,
                hard_deadline=NULL,next_run_at=NULL
            WHERE run_id=:id AND status IN ('PENDING','READY','QUEUED','RUNNING','RETRY_WAIT')"""),
            params,
        )
        await conn.execute(
            text("""UPDATE outbox_events e SET status='OBSOLETE',claim_token=NULL,claim_until=NULL
            FROM task_runs t WHERE t.id=e.task_run_id AND t.run_id=:id
            AND e.status IN ('PENDING','CLAIMED')"""),
            params,
        )
        await conn.execute(
            text("UPDATE workflow_runs SET status='CANCELED',finished_at=:finished WHERE id=:id"),
            {**params, "finished": finished},
        )
        return {**run, "status": "CANCELED", "finished_at": finished}


async def tasks(
    engine: AsyncEngine, actor: Principal, run_id: UUID, limit: int, offset: int
) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        await _visible_run(conn, actor, run_id)
        rows = await conn.execute(
            text("""SELECT t.id AS task_id,t.node_key,t.status,t.attempt_count,
                    ARRAY(SELECT d.predecessor_task_id FROM task_dependencies d
                          WHERE d.run_id=t.run_id AND d.successor_task_id=t.id
                          ORDER BY d.predecessor_task_id) AS predecessor_task_ids
                    FROM task_runs t WHERE t.run_id=:id
                    ORDER BY t.node_key LIMIT :limit OFFSET :offset"""),
            {"id": run_id, "limit": limit, "offset": offset},
        )
        return [dict(row) for row in rows.mappings()]
