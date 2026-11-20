"""Child process for the crash drill. The parent kills it at a known worker boundary."""

import asyncio
import sys

import aio_pika
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.config import Settings
from flowforge.worker import consumer
from flowforge.worker.service import finish


async def pause() -> None:
    print("fault-boundary", flush=True)
    await asyncio.Event().wait()


async def before_commit(*args) -> str:
    output = await consumer.run_handler(*args)
    await pause()
    return output


async def after_commit(*args) -> bool:
    assert await finish(*args)
    await pause()
    return True


async def main(queue_name: str, boundary: str) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    handler = consumer.run_handler
    if boundary == "before_commit":
        handler = before_commit
    elif boundary == "after_commit":
        consumer.finish = after_commit
    else:
        raise ValueError(f"Unknown worker boundary: {boundary}")

    try:
        connection = await aio_pika.connect(settings.rabbitmq_url, timeout=5)
        async with connection:
            channel = await connection.channel()
            queue = await channel.get_queue(queue_name)
            async with asyncio.timeout(20), queue.iterator() as messages:
                message = await anext(messages)
                await consumer.handle(
                    engine, "crash-fixture", message, asyncio.Event(), lambda: None, handler
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
