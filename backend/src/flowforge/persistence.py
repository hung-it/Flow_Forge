from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


async def check_database(engine: AsyncEngine) -> bool:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    expected = ScriptDirectory.from_config(config).get_current_head()
    async with engine.connect() as connection:
        current = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    return bool(current == expected)
