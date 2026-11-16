"""Infrastructure lifecycle only: no workflow execution is implemented yet."""
import argparse
import asyncio
import json
import logging
import signal
import tempfile
import time
from pathlib import Path

import aio_pika
from aio_pika.exceptions import AMQPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.config import Settings
from flowforge.observability import configure_logging
from flowforge.persistence import check_database

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
    configure_logging()
    logger = logging.getLogger(role)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    logger.info("scaffold process starting; execution disabled")
    health_path(role).unlink(missing_ok=True)
    while not stop.is_set():
        try:
            healthy = await asyncio.wait_for(probe(role), timeout=10)
        except (SQLAlchemyError, AMQPException, OSError, TimeoutError):
            healthy = False
        logger.info("dependency_status=%s", "ready" if healthy else "unavailable")
        if healthy:
            target = health_path(role)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"updated_at": time.time()}))
            temporary.replace(target)
        else:
            health_path(role).unlink(missing_ok=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
    health_path(role).unlink(missing_ok=True)
    logger.info("shutdown complete")


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
        try:
            healthy = asyncio.run(asyncio.wait_for(probe(args.role), timeout=10))
        except (SQLAlchemyError, AMQPException, OSError, TimeoutError):
            healthy = False
        raise SystemExit(0 if healthy else 1)
    asyncio.run(run(args.role))


if __name__ == "__main__":
    main()
