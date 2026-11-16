import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.config import Settings


def apply(connection):
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def migrate():
    engine = create_async_engine(Settings().database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(apply)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(migrate())
