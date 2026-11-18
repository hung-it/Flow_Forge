import logging
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowforge.contracts import DispatchMessage

logger = logging.getLogger(__name__)
BATCH = 50
ACTIVE = "('PENDING','RUNNING')"
TERMINAL = "('SUCCEEDED','FAILED','SKIPPED','CANCELED')"
PREDECESSORS = """FROM task_dependencies d
    JOIN task_runs p ON p.run_id=d.run_id AND p.id=d.predecessor_task_id
    WHERE d.run_id=t.run_id AND d.successor_task_id=t.id"""
BLOCKED = f"EXISTS(SELECT 1 {PREDECESSORS} AND p.status IN ('FAILED','SKIPPED','CANCELED'))"
WAITING = f"EXISTS(SELECT 1 {PREDECESSORS} AND p.status<>'SUCCEEDED')"
ACTIONABLE = f"(t.status='READY' OR (t.status='PENDING' AND ({BLOCKED} OR NOT {WAITING})))"


async def reconcile(engine: AsyncEngine) -> int:
    """Reconcile one actionable run under its row lock."""
    dispatched: list[DispatchMessage] = []
    async with engine.begin() as conn:
        run = await conn.scalar(
            text(f"""SELECT r.id FROM workflow_runs r
            WHERE r.status IN {ACTIVE} AND (
              EXISTS(SELECT 1 FROM task_runs t WHERE t.run_id=r.id AND {ACTIONABLE})
              OR (r.status='RUNNING' AND NOT EXISTS(SELECT 1 FROM task_runs t
                            WHERE t.run_id=r.id AND t.status NOT IN {TERMINAL})))
            ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED""")
        )
        if run is None:
            return 0

        params = {"run": run, "limit": BATCH}
        candidates = (
            (
                await conn.execute(
                    text(f"""SELECT t.id,t.status,{BLOCKED} AS blocked
            FROM task_runs t WHERE t.run_id=:run AND {ACTIONABLE}
            ORDER BY t.id LIMIT :limit FOR UPDATE OF t"""),
                    params,
                )
            )
            .mappings()
            .all()
        )
        for task in candidates:
            ident = {"id": task["id"]}
            if task["status"] == "PENDING":
                status = "SKIPPED" if task["blocked"] else "READY"
                await conn.execute(
                    text("UPDATE task_runs SET status=:status WHERE id=:id AND status='PENDING'"),
                    {**ident, "status": status},
                )
                if status == "SKIPPED":
                    continue
            generation = (
                await conn.execute(
                    text("""UPDATE task_runs
                SET status='QUEUED',dispatch_generation=dispatch_generation+1
                WHERE id=:id AND status='READY' RETURNING dispatch_generation"""),
                    ident,
                )
            ).scalar_one()
            event = DispatchMessage(
                schema_version=1,
                event_id=uuid4(),
                run_id=run,
                task_run_id=task["id"],
                dispatch_generation=generation,
            )
            await conn.execute(
                text("""INSERT INTO outbox_events
                (id,task_run_id,dispatch_generation,event_type,payload)
                VALUES(:id,:task,:generation,'task.execute',CAST(:payload AS jsonb))"""),
                {
                    "id": event.event_id,
                    "task": event.task_run_id,
                    "generation": generation,
                    "payload": event.model_dump_json(exclude_none=True),
                },
            )
            dispatched.append(event)
        if dispatched:
            await conn.execute(
                text("""UPDATE workflow_runs
                SET status='RUNNING',started_at=clock_timestamp()
                WHERE id=:run AND status='PENDING'"""),
                params,
            )
        completed = await conn.scalar(
            text(f"""UPDATE workflow_runs r SET
            status=CASE WHEN EXISTS(SELECT 1 FROM task_runs t
                WHERE t.run_id=r.id AND t.status<>'SUCCEEDED') THEN 'FAILED' ELSE 'SUCCEEDED' END,
            finished_at=clock_timestamp()
            WHERE r.id=:run AND r.status='RUNNING' AND NOT EXISTS(
                SELECT 1 FROM task_runs t WHERE t.run_id=r.id AND t.status NOT IN {TERMINAL})
            RETURNING status"""),
            params,
        )
    for event in dispatched:
        logger.info(
            "dispatched run=%s task=%s event=%s generation=%s",
            event.run_id,
            event.task_run_id,
            event.event_id,
            event.dispatch_generation,
        )
    if completed:
        logger.info("completed run=%s status=%s", run, completed)
    return len(candidates) + int(completed is not None)


DUE = """e.status='PUBLISHED' AND e.dispatch_generation=t.dispatch_generation
    AND t.status='QUEUED' AND e.published_at <= clock_timestamp()-interval '60 seconds'
    AND (e.last_redriven_at IS NULL OR
         e.last_redriven_at <= clock_timestamp()-interval '60 seconds')"""


async def redrive(engine: AsyncEngine) -> int:
    """Reschedule already-published identities for tasks still waiting in the queue."""
    async with engine.begin() as conn:
        run = await conn.scalar(
            text(f"""SELECT r.id FROM workflow_runs r
            WHERE r.status IN {ACTIVE} AND EXISTS(SELECT 1 FROM task_runs t
              JOIN outbox_events e ON e.task_run_id=t.id WHERE t.run_id=r.id AND {DUE})
            ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED""")
        )
        if run is None:
            return 0
        tasks = (
            await conn.execute(
                text(f"""SELECT t.id FROM task_runs t WHERE t.run_id=:run
            AND EXISTS(SELECT 1 FROM outbox_events e WHERE e.task_run_id=t.id AND {DUE})
            ORDER BY t.id LIMIT :limit FOR UPDATE OF t"""),
                {"run": run, "limit": BATCH},
            )
        ).scalars()
        changed = (
            (
                await conn.execute(
                    text(f"""UPDATE outbox_events e SET status='PENDING',
            available_at=clock_timestamp(),last_redriven_at=clock_timestamp()
            FROM task_runs t WHERE t.id=e.task_run_id AND t.id=ANY(CAST(:tasks AS uuid[]))
            AND {DUE} RETURNING e.id"""),
                    {"tasks": list(tasks)},
                )
            )
            .scalars()
            .all()
        )
    for event in changed:
        logger.info("redriven run=%s event=%s", run, event)
    return len(changed)
