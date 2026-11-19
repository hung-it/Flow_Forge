import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flowforge.contracts import DispatchMessage, TaskSpec, WorkflowSpec
from flowforge.runs.input import resolve_pointer

from .handlers import Output

logger = logging.getLogger(__name__)
ACTIVE = ("PENDING", "RUNNING")
LEASE = 30
BATCH = 50


class Disposition(Enum):
    ACK = "ack"
    REJECT = "reject"
    DEFER = "defer"


class InvalidBinding(ValueError):
    """A referenced runtime value is missing or invalid."""


@dataclass(frozen=True)
class Claim:
    run_id: UUID
    task_id: UUID
    generation: int
    token: UUID
    attempt: int
    spec: TaskSpec


def node_spec(dag: str, key: str) -> TaskSpec:
    return next(node for node in WorkflowSpec.model_validate_json(dag).nodes if node.key == key)


async def claim_task(
    engine: AsyncEngine,
    delivery: DispatchMessage,
    worker_id: str,
) -> Claim | Disposition:
    params = {"run": delivery.run_id, "task": delivery.task_run_id, "event": delivery.event_id}
    async with engine.begin() as conn:
        run = (
            await conn.execute(
                text("""SELECT r.status,CAST(v.dag AS text) AS dag
            FROM workflow_runs r JOIN workflow_versions v ON v.id=r.workflow_version_id
            WHERE r.id=:run FOR UPDATE OF r"""),
                params,
            )
        ).one_or_none()
        if run is None:
            return Disposition.REJECT
        task = (
            await conn.execute(
                text("""SELECT status,node_key,dispatch_generation,
            attempt_count,lease_until>clock_timestamp() AS live
            FROM task_runs WHERE id=:task AND run_id=:run FOR UPDATE"""),
                params,
            )
        ).one_or_none()
        event = (
            await conn.execute(
                text("""SELECT task_run_id,dispatch_generation
            FROM outbox_events WHERE id=:event AND event_type='task.execute'"""),
                params,
            )
        ).one_or_none()
        if (
            task is None
            or event is None
            or event.task_run_id != delivery.task_run_id
            or event.dispatch_generation != delivery.dispatch_generation
        ):
            return Disposition.REJECT
        if task.dispatch_generation != delivery.dispatch_generation or run.status not in ACTIVE:
            return Disposition.ACK
        if task.status == "RUNNING":
            return Disposition.ACK if task.live else Disposition.DEFER
        if task.status != "QUEUED":
            return Disposition.ACK
        spec = node_spec(run.dag, task.node_key)
        if task.attempt_count >= spec.retry.max_attempts:
            # A queued task without a remaining attempt violates the scheduler invariant.
            raise RuntimeError("Queued task has exhausted its attempt budget")
        token = uuid4()
        claim = Claim(
            delivery.run_id,
            delivery.task_run_id,
            delivery.dispatch_generation,
            token,
            task.attempt_count + 1,
            spec,
        )
        started = (
            await conn.execute(
                text("""WITH tick AS (SELECT clock_timestamp() AS ts)
            UPDATE task_runs t SET status='RUNNING', attempt_count=:attempt, lease_token=:token,
                lease_until=tick.ts+CAST(:lease AS integer)*interval '1 second',
                hard_deadline=tick.ts+CAST(:timeout AS integer)*interval '1 second'
            FROM tick WHERE t.id=:task RETURNING tick.ts"""),
                {
                    "task": claim.task_id,
                    "attempt": claim.attempt,
                    "token": token,
                    "lease": min(LEASE, spec.timeout_seconds),
                    "timeout": spec.timeout_seconds,
                },
            )
        ).scalar_one()
        await conn.execute(
            text("""INSERT INTO task_attempts
            (id,task_run_id,attempt_no,dispatch_generation,worker_id,lease_token,status,started_at)
            VALUES (:id,:task,:attempt,:generation,:worker,:token,'RUNNING',:started)"""),
            {
                "id": uuid4(),
                "task": claim.task_id,
                "attempt": claim.attempt,
                "generation": claim.generation,
                "worker": worker_id,
                "token": token,
                "started": started,
            },
        )
    logger.info(
        "claimed run=%s task=%s generation=%s attempt=%s",
        claim.run_id,
        claim.task_id,
        claim.generation,
        claim.attempt,
    )
    return claim


async def load_inputs(engine: AsyncEngine, claim: Claim) -> Output:
    sources = {binding.node for binding in claim.spec.inputs.values() if binding.node is not None}
    async with engine.connect() as conn:
        root = (
            await conn.execute(
                text("""SELECT CAST(input_json AS text)
            FROM workflow_runs WHERE id=:run"""),
                {"run": claim.run_id},
            )
        ).scalar_one()
        rows = await conn.execute(
            text("""SELECT node_key,CAST(result_json AS text) AS result
            FROM task_runs WHERE run_id=:run AND node_key=ANY(CAST(:keys AS text[]))
            AND status='SUCCEEDED' AND result_json IS NOT NULL"""),
            {"run": claim.run_id, "keys": sorted(sources)},
        )
        outputs = {row.node_key: json.loads(row.result) for row in rows}
    raw = json.loads(root)
    try:
        return {
            name: resolve_pointer(
                raw if binding.node is None else outputs[binding.node], binding.pointer
            )
            for name, binding in claim.spec.inputs.items()
        }
    except (ValueError, KeyError) as error:
        raise InvalidBinding("Task input could not be resolved") from error


IDENTITY = """id=:task AND run_id=:run AND status='RUNNING' AND lease_token=:token
            AND dispatch_generation=:generation AND attempt_count=:attempt"""
LIVE = " AND lease_until>clock_timestamp() AND hard_deadline>clock_timestamp()"


def identity(claim: Claim) -> dict[str, UUID | int]:
    return {
        "run": claim.run_id,
        "task": claim.task_id,
        "token": claim.token,
        "generation": claim.generation,
        "attempt": claim.attempt,
    }


async def lock_active_run(conn: AsyncConnection, run_id: UUID) -> bool:
    status = await conn.scalar(
        text("SELECT status FROM workflow_runs WHERE id=:run FOR UPDATE"), {"run": run_id}
    )
    return status in ACTIVE


async def renew(engine: AsyncEngine, claim: Claim) -> datetime | None:
    async with engine.begin() as conn:
        if not await lock_active_run(conn, claim.run_id):
            return None
        row = (
            await conn.execute(
                text(f"""UPDATE task_runs
            SET lease_until=LEAST(clock_timestamp()+interval '30 seconds',hard_deadline)
            WHERE {IDENTITY}{LIVE} RETURNING lease_until"""),
                identity(claim),
            )
        ).one_or_none()
        return cast(datetime, row.lease_until) if row else None


async def transition(
    conn: AsyncConnection,
    claim: Claim,
    result: str | None,
    error: str | None,
    retryable: bool,
    *,
    expired: bool = False,
) -> bool:
    retry = result is None and retryable and claim.attempt < claim.spec.retry.max_attempts
    status = "SUCCEEDED" if result is not None else ("RETRY_WAIT" if retry else "FAILED")
    policy = claim.spec.retry
    delay = min(
        policy.max_delay_seconds,
        policy.initial_delay_seconds * policy.backoff_multiplier ** (claim.attempt - 1),
    )
    predicate = (
        " AND (lease_until<=clock_timestamp() OR hard_deadline<=clock_timestamp())"
        if expired
        else LIVE
    )
    updated = (
        await conn.execute(
            text(f"""UPDATE task_runs SET status=:status,
        result_json=CAST(:result AS jsonb), lease_token=NULL,lease_until=NULL,hard_deadline=NULL,
        next_run_at=CASE WHEN :retry
            THEN clock_timestamp()+CAST(:delay AS integer)*interval '1 second' END
        WHERE {IDENTITY}{predicate} RETURNING id"""),
            {**identity(claim), "status": status, "result": result, "retry": retry, "delay": delay},
        )
    ).one_or_none()
    if updated is None:
        return False
    attempt_status = (
        "SUCCEEDED" if result is not None else ("LOST" if error == "WORKER_LOST" else "FAILED")
    )
    (
        await conn.execute(
            text("""UPDATE task_attempts SET status=:status,error_code=:error,
        finished_at=clock_timestamp() WHERE task_run_id=:task AND attempt_no=:attempt
        AND dispatch_generation=:generation AND lease_token=:token AND status='RUNNING'
        RETURNING id"""),
            {**identity(claim), "status": attempt_status, "error": error},
        )
    ).one()
    return True


async def finish(
    engine: AsyncEngine,
    claim: Claim,
    result: str | None,
    error: str | None,
    retryable: bool,
) -> bool:
    async with engine.begin() as conn:
        if not await lock_active_run(conn, claim.run_id):
            return False
        return await transition(conn, claim, result, error, retryable)


DUE = """((t.status='RUNNING' AND (t.lease_until<=clock_timestamp()
       OR t.hard_deadline<=clock_timestamp()))
       OR (t.status='RETRY_WAIT' AND t.next_run_at<=clock_timestamp()))"""


async def recover(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(f"""SELECT r.id,CAST(v.dag AS text) AS dag
            FROM workflow_runs r JOIN workflow_versions v ON v.id=r.workflow_version_id
            WHERE r.status='RUNNING' AND EXISTS(SELECT 1 FROM task_runs t
                WHERE t.run_id=r.id AND {DUE})
            ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED""")
            )
        ).one_or_none()
        if row is None:
            return 0
        params = {"run": row.id, "limit": BATCH}
        tasks = (
            (
                await conn.execute(
                    text(f"""SELECT t.id,t.status,t.node_key,t.dispatch_generation,
            t.lease_token,t.attempt_count,t.hard_deadline<=clock_timestamp() AS timed_out
            FROM task_runs t WHERE t.run_id=:run AND {DUE}
            ORDER BY t.id LIMIT :limit FOR UPDATE OF t"""),
                    params,
                )
            )
            .mappings()
            .all()
        )
        for task in tasks:
            await recover_task(conn, row.id, row.dag, task)
        return len(tasks)


async def recover_task(conn: AsyncConnection, run_id: UUID, dag: str, task: RowMapping) -> None:
    if task["status"] == "RETRY_WAIT":
        await conn.execute(
            text("""UPDATE task_runs SET status='READY',next_run_at=NULL
            WHERE id=:id AND status='RETRY_WAIT' AND next_run_at<=clock_timestamp()"""),
            {"id": task["id"]},
        )
        return
    claim = Claim(
        run_id,
        task["id"],
        task["dispatch_generation"],
        task["lease_token"],
        task["attempt_count"],
        node_spec(dag, task["node_key"]),
    )
    error = "TASK_TIMEOUT" if task["timed_out"] else "WORKER_LOST"
    await transition(conn, claim, None, error, True, expired=True)
