import asyncio

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractExchange
from aio_pika.exceptions import AMQPException
from pamqp.commands import Basic

from flowforge.contracts import DispatchMessage
from flowforge.observability import trace_header

TASKS = "flowforge.tasks.v1"
DEAD = "flowforge.dead.v1"
TASK_KEY = "task.execute"
DEAD_KEY = "task.invalid"


async def declare_topology(channel: AbstractChannel) -> AbstractExchange:
    exchange = await channel.declare_exchange(TASKS, aio_pika.ExchangeType.DIRECT, durable=True)
    dead_exchange = await channel.declare_exchange(DEAD, aio_pika.ExchangeType.DIRECT, durable=True)
    dead = await channel.declare_queue(
        DEAD,
        durable=True,
        arguments={
            "x-queue-type": "classic",
            "x-max-length": 1000,
            "x-message-ttl": 604800000,
        },
    )
    await dead.bind(dead_exchange, DEAD_KEY)
    tasks = await channel.declare_queue(
        TASKS,
        durable=True,
        arguments={
            "x-queue-type": "classic",
            "x-dead-letter-exchange": DEAD,
            "x-dead-letter-routing-key": DEAD_KEY,
        },
    )
    await tasks.bind(exchange, TASK_KEY)
    return exchange


class Broker:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connection: AbstractConnection | None = None
        self.channel: AbstractChannel | None = None
        self.exchange: AbstractExchange | None = None

    async def ensure(self) -> None:
        if self.exchange is not None and self.channel is not None and not self.channel.is_closed:
            return
        await self.close()
        try:
            async with asyncio.timeout(5):
                self.connection = await aio_pika.connect(self.url, timeout=5)
                self.channel = await self.connection.channel(
                    publisher_confirms=True,
                    on_return_raises=True,
                )
                self.exchange = await declare_topology(self.channel)
        except (AMQPException, OSError, TimeoutError):
            await self.close()
            raise

    async def send(self, event: DispatchMessage) -> bool:
        await self.ensure()
        assert self.exchange is not None
        payload = event.model_dump_json(exclude_none=True).encode("utf-8")
        context = trace_header()
        result = await self.exchange.publish(
            aio_pika.Message(
                payload,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(event.event_id),
                headers={"traceparent": context} if context else None,
            ),
            routing_key=TASK_KEY,
            mandatory=True,
            timeout=5,
        )
        return isinstance(result, Basic.Ack)

    async def close(self) -> None:
        connection, self.connection = self.connection, None
        self.channel = None
        self.exchange = None
        if connection is not None and not connection.is_closed:
            try:
                await asyncio.wait_for(connection.close(), timeout=2)
            except (AMQPException, OSError, TimeoutError):
                pass
