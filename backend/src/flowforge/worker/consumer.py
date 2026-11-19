import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.exceptions import AMQPException
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from flowforge import observability as obs
from flowforge.contracts import DispatchMessage
from flowforge.outbox.broker import TASKS, declare_topology
from flowforge.persistence import check_database
from flowforge.workflows.publication import CATALOG

from .handlers import TaskContext, execute
from .service import Claim, Disposition, InvalidBinding, claim_task, finish, load_inputs, renew

logger = logging.getLogger(__name__)
MAX_MESSAGE = 16 * 1024
MAX_OUTPUT = 64 * 1024
HEARTBEAT = 10


class Reconnect(RuntimeError):
    """Close the connection to leave the delivery unsettled."""


class InvalidOutput(ValueError):
    pass


class HandlerFailure(ValueError):
    pass


class HandlerTimeout(ValueError):
    pass


async def run_handler(engine: AsyncEngine, claim: Claim, cancel: asyncio.Event) -> str:
    raw = await load_inputs(engine, claim)
    input_type, output_type = CATALOG[claim.spec.handler]
    try:
        inputs = input_type.model_validate(raw)
    except ValidationError as error:
        raise InvalidBinding("Task inputs do not match the published contract") from error
    context = TaskContext(claim.run_id, claim.task_id, claim.attempt, cancel)
    try:
        output = execute(claim.spec.handler, inputs, context)
    except Exception as error:
        raise HandlerFailure("Task handler failed") from error
    try:
        encoded = output_type.model_validate(output).model_dump_json()
    except ValidationError as error:
        raise InvalidOutput("Task output does not match the published contract") from error
    if len(encoded.encode("utf-8")) > MAX_OUTPUT:
        raise InvalidOutput("Task output exceeds the configured byte limit")
    return encoded


async def owned_work(
    engine: AsyncEngine,
    claim: Claim,
    stop: asyncio.Event,
    progress: Callable[[], None],
    handler: Callable[[AsyncEngine, Claim, asyncio.Event], Awaitable[str]],
) -> str:
    cancelled = asyncio.Event()

    async def execute_until_deadline() -> str:
        deadline = asyncio.timeout(claim.spec.timeout_seconds)
        try:
            async with deadline:
                return await handler(engine, claim, cancelled)
        except TimeoutError as error:
            if deadline.expired():
                raise HandlerTimeout("Handler deadline reached") from error
            raise

    work = asyncio.create_task(execute_until_deadline())
    shutting_down = asyncio.create_task(stop.wait())
    try:
        while True:
            await asyncio.wait(
                {work, shutting_down}, timeout=HEARTBEAT, return_when=asyncio.FIRST_COMPLETED
            )
            if stop.is_set():
                raise Reconnect("Worker stopping with an unfinished delivery")
            if work.done():
                return await work
            if await renew(engine, claim) is None:
                raise Reconnect("Worker lease expired")
            progress()
    finally:
        cancelled.set()
        for pending in (work, shutting_down):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(work, shutting_down, return_exceptions=True)


async def handle(
    engine: AsyncEngine,
    worker: str,
    message: AbstractIncomingMessage,
    stop: asyncio.Event,
    progress: Callable[[], None],
    handler: Callable[[AsyncEngine, Claim, asyncio.Event], Awaitable[str]] = run_handler,
) -> None:
    if len(message.body) > MAX_MESSAGE:
        await message.reject(requeue=False)
        return
    try:
        event = DispatchMessage.model_validate_json(message.body)
    except ValidationError:
        await message.reject(requeue=False)
        return
    header = (getattr(message, "headers", None) or {}).get("traceparent")
    parent = header if obs.trace_parent(header) is not None else event.traceparent
    with obs.span("worker.delivery", parent=parent, root=True, kind=SpanKind.CONSUMER, attributes={
        "flowforge.run.id": str(event.run_id), "flowforge.task.id": str(event.task_run_id),
        "flowforge.event.id": str(event.event_id), "flowforge.dispatch.generation":
        event.dispatch_generation, "flowforge.worker.id": worker,
    }) as delivery:
        with obs.span("worker.claim"):
            claim = await claim_task(engine, event, worker)
        if claim is Disposition.REJECT:
            await message.reject(requeue=False)
            delivery.set_attribute("flowforge.outcome", "rejected")
            return
        if claim is Disposition.ACK:
            await message.ack()
            delivery.set_attribute("flowforge.outcome", "duplicate_or_stale")
            return
        if claim is Disposition.DEFER:
            raise Reconnect("Delivery belongs to an expired attempt")
        delivery.set_attribute("flowforge.attempt", claim.attempt)
        delivery.set_attribute("flowforge.handler", claim.spec.handler)
        progress()
        retryable = False
        result, code = None, None
        try:
            with obs.span("worker.execute"):
                result = await owned_work(engine, claim, stop, progress, handler)
        except InvalidBinding:
            code = "INPUT_BINDING_INVALID"
        except InvalidOutput:
            code = "OUTPUT_INVALID"
        except HandlerTimeout:
            code, retryable = "TASK_TIMEOUT", True
        except HandlerFailure:
            code, retryable = "HANDLER_FAILED", True
        if code:
            delivery.set_attribute("error.type", code)
            delivery.set_status(StatusCode.ERROR)
        with obs.span("worker.commit"):
            if not await finish(engine, claim, result, code, retryable):
                raise Reconnect("Worker no longer owns the attempt")
        with obs.span("worker.ack"):
            await message.ack()
        obs.log_event(logger, "worker.committed_and_acked", run_id=str(event.run_id),
                      task_id=str(event.task_run_id), attempt=claim.attempt, error_code=code,
                      generation=event.dispatch_generation, worker_id=worker)


async def consume(
    engine: AsyncEngine,
    broker_url: str,
    worker: str,
    stop: asyncio.Event,
    progress: Callable[[bool], None],
) -> None:
    backoff = 1
    while not stop.is_set():
        try:
            if not await asyncio.wait_for(check_database(engine), timeout=5):
                raise Reconnect("Database schema is not current")
            connection = await aio_pika.connect(broker_url, timeout=5)
            async with connection:
                async with asyncio.timeout(5):
                    channel = await connection.channel()
                    await channel.set_qos(prefetch_count=1)
                    await declare_topology(channel)
                    queue = await channel.get_queue(TASKS)
                async with queue.iterator() as messages:
                    delivery = asyncio.create_task(anext(messages))
                    try:
                        while not stop.is_set():
                            done, _ = await asyncio.wait({delivery}, timeout=1)
                            if stop.is_set():
                                break
                            if not done:
                                if not await asyncio.wait_for(check_database(engine), timeout=5):
                                    raise Reconnect("Database schema is not current")
                            else:
                                await handle(
                                    engine, worker, delivery.result(), stop, lambda: progress(True)
                                )
                                delivery = asyncio.create_task(anext(messages))
                            progress(True)
                            backoff = 1
                    finally:
                        if not delivery.done():
                            delivery.cancel()
                        await asyncio.gather(delivery, return_exceptions=True)
        except (
            AMQPException,
            SQLAlchemyError,
            OSError,
            TimeoutError,
            Reconnect,
            StopAsyncIteration,
        ) as error:
            progress(False)
            logger.warning("worker reconnect error=%s", type(error).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(30, backoff * 2)
