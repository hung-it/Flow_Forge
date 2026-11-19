"""Create disposable browser test accounts and write credentials for the test runner's stdin."""

import asyncio
import json
import secrets

from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.auth.service import bootstrap
from flowforge.config import Settings


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        accounts = {}
        suffix = secrets.token_hex(8)
        for role in ("owner", "developer", "viewer"):
            email = f"dashboard-{role}-{suffix}@example.com"
            password = secrets.token_urlsafe(24)
            await bootstrap(engine, email, password)
            accounts[role] = {"email": email, "password": password}
        print(json.dumps(accounts))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
