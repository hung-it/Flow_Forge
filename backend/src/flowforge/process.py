"""Role entry point and lightweight process health checks."""

import argparse
import asyncio
import json
import logging
import signal
import tempfile
import time
from pathlib import Path

HEALTH_DIR = Path(tempfile.gettempdir())


def health_path(role: str) -> Path:
    return HEALTH_DIR / f"flowforge-{role}-health.json"


def healthy_loop(role: str) -> bool:
    """A dependency probe alone cannot detect a stalled background loop."""
    try:
        age = time.time() - float(json.loads(health_path(role).read_text())["updated_at"])
        return 0 <= age < 20
    except (OSError, ValueError, KeyError, TypeError):
        return False


async def probe(role: str) -> bool:
    import aio_pika
    from sqlalchemy.ext.asyncio import create_async_engine

    from flowforge.config import Settings
    from flowforge.persistence import check_database

    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        if not await check_database(engine):
            return False
        if role in {"publisher", "worker"}:
            connection = await aio_pika.connect(settings.rabbitmq_url, timeout=5)
            await connection.close()
        return True
    finally:
        await engine.dispose()


async def run(role: str) -> None:
    from aio_pika.exceptions import AMQPException
    from sqlalchemy.exc import SQLAlchemyError

    from flowforge.observability import configure_logging

    configure_logging()
    logger = logging.getLogger(role)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    logger.info("process starting")
    health_path(role).unlink(missing_ok=True)
    if role != "worker":
        try:
            await run_dispatch_role(role, stop)
        finally:
            health_path(role).unlink(missing_ok=True)
        logger.info("shutdown complete")
        return
    while not stop.is_set():
        try:
            healthy = await asyncio.wait_for(probe(role), timeout=10)
        except (SQLAlchemyError, AMQPException, OSError, TimeoutError):
            healthy = False
        logger.info("dependency_status=%s", "ready" if healthy else "unavailable")
        if healthy:
            mark_healthy(role)
        else:
            health_path(role).unlink(missing_ok=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
    health_path(role).unlink(missing_ok=True)
    logger.info("shutdown complete")


def mark_healthy(role: str) -> None:
    target = health_path(role)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"updated_at": time.time()}))
    temporary.replace(target)


async def run_dispatch_role(role: str, stop: asyncio.Event) -> None:
    from aio_pika.exceptions import AMQPException
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import create_async_engine

    from flowforge.config import Settings
    from flowforge.outbox.broker import Broker
    from flowforge.outbox.service import publish_next
    from flowforge.persistence import check_database
    from flowforge.scheduler.service import BATCH, reconcile, redrive

    settings = Settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    broker = Broker(settings.rabbitmq_url) if role == "publisher" else None
    logger = logging.getLogger(role)
    try:
        while not stop.is_set():
            try:
                if not await asyncio.wait_for(check_database(engine), timeout=5):
                    raise RuntimeError("Database schema is not current")
                if broker is None:
                    await reconcile(engine)
                    if not stop.is_set():
                        await redrive(engine)
                else:
                    await broker.ensure()
                    deadline = time.monotonic() + 1
                    for _ in range(BATCH):
                        if stop.is_set() or time.monotonic() >= deadline:
                            break
                        if not await publish_next(engine, broker.send):
                            break
                mark_healthy(role)
            except (SQLAlchemyError, AMQPException, OSError, TimeoutError, RuntimeError) as error:
                health_path(role).unlink(missing_ok=True)
                logger.warning("dispatch loop unavailable error=%s", type(error).__name__)
                if broker is not None:
                    await broker.close()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        if broker is not None:
            await broker.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["scheduler", "publisher", "worker"])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        raise SystemExit(0 if healthy_loop(args.role) else 1)
    if args.probe:
        from aio_pika.exceptions import AMQPException
        from sqlalchemy.exc import SQLAlchemyError

        try:
            healthy = asyncio.run(asyncio.wait_for(probe(args.role), timeout=10))
        except (SQLAlchemyError, AMQPException, OSError, TimeoutError):
            healthy = False
        raise SystemExit(0 if healthy else 1)
    asyncio.run(run(args.role))


if __name__ == "__main__":
    main()
