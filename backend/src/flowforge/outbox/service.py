import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from aio_pika.exceptions import AMQPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowforge.contracts import DispatchMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    event_id: UUID
    token: UUID
    attempts: int


async def claim_next(engine: AsyncEngine) -> Claim | None:
    token = uuid4()
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("""WITH next AS (
            SELECT id FROM outbox_events
            WHERE (status='PENDING' AND available_at<=clock_timestamp())
               OR (status='CLAIMED' AND claim_until<=clock_timestamp())
            ORDER BY CASE WHEN status='CLAIMED' THEN claim_until ELSE available_at END,id
            LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        UPDATE outbox_events e SET status='CLAIMED',claim_token=:token,
            claim_until=clock_timestamp()+interval '30 seconds',
            publish_attempts=e.publish_attempts+1
        FROM next WHERE e.id=next.id RETURNING e.id,e.publish_attempts"""),
                {"token": token},
            )
        ).one_or_none()
        return Claim(row.id, token, row.publish_attempts) if row else None


CURRENT = "id=:id AND status='CLAIMED' AND claim_token=:token AND claim_until>clock_timestamp()"


async def preflight(engine: AsyncEngine, claim: Claim) -> DispatchMessage | None:
    params = {"id": claim.event_id, "token": claim.token}
    async with engine.begin() as conn:
        identity = (
            await conn.execute(
                text("""SELECT t.id,t.run_id FROM outbox_events e
            JOIN task_runs t ON t.id=e.task_run_id WHERE e.id=:id"""),
                params,
            )
        ).one()
        parent = {"run": identity.run_id, "task": identity.id}
        run = await conn.scalar(
            text("SELECT status FROM workflow_runs WHERE id=:run FOR UPDATE"), parent
        )
        task = (
            await conn.execute(
                text("""SELECT status,dispatch_generation
            FROM task_runs WHERE id=:task FOR UPDATE"""),
                parent,
            )
        ).one()
        row = (
            await conn.execute(
                text(f"""SELECT dispatch_generation,CAST(payload AS text) AS body
            FROM outbox_events WHERE {CURRENT} FOR UPDATE"""),
                params,
            )
        ).one_or_none()
        if row is None:
            return None
        if (
            run not in ("PENDING", "RUNNING")
            or task.status != "QUEUED"
            or task.dispatch_generation != row.dispatch_generation
        ):
            await conn.execute(
                text(f"""UPDATE outbox_events SET status='OBSOLETE',
                claim_token=NULL,claim_until=NULL WHERE {CURRENT}"""),
                params,
            )
            return None
        return DispatchMessage.model_validate_json(row.body)


def retry_delay(attempts: int) -> float:
    base = min(30.0, 2.0 ** min(attempts - 1, 5))
    return random.uniform(min(base, 25.0), min(30.0, base * 1.2))


async def finish(engine: AsyncEngine, claim: Claim, confirmed: bool) -> bool:
    params: dict[str, UUID | float] = {"id": claim.event_id, "token": claim.token}
    if confirmed:
        change = "status='PUBLISHED',published_at=clock_timestamp()"
    else:
        change = """status='PENDING',
                    available_at=clock_timestamp()
                                 + CAST(:delay AS double precision)*interval '1 second'"""
        params["delay"] = retry_delay(claim.attempts)
    async with engine.begin() as conn:
        result = await conn.scalar(
            text(f"""UPDATE outbox_events SET {change},
            claim_token=NULL,claim_until=NULL WHERE {CURRENT} RETURNING id"""),
            params,
        )
    return result is not None


async def publish_next(
    engine: AsyncEngine,
    send: Callable[[DispatchMessage], Awaitable[bool]],
) -> bool:
    claim = await claim_next(engine)
    if claim is None:
        return False
    event = await preflight(engine, claim)
    if event is None:
        return True
    try:
        confirmed = await asyncio.wait_for(send(event), timeout=5)
    except (AMQPException, OSError, TimeoutError):
        confirmed = False
    recorded = await finish(engine, claim, confirmed)
    logger.info(
        "publish run=%s task=%s event=%s generation=%s confirmed=%s recorded=%s",
        event.run_id,
        event.task_run_id,
        event.event_id,
        event.dispatch_generation,
        confirmed,
        recorded,
    )
    return True
