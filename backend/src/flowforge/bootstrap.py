"""Create a local user; never put passwords in command-line arguments."""

import argparse
import asyncio
import getpass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from flowforge.auth.service import bootstrap
from flowforge.config import Settings
from flowforge.errors import ApiError


async def create(email: str, password: str) -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        user = await bootstrap(engine, email, password)
        print(f"Created user {user}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a FlowForge user")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Password (12–128 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match\n")
    try:
        asyncio.run(create(args.email, password))
    except (ApiError, ValueError) as error:
        parser.exit(1, (error.message if isinstance(error, ApiError) else str(error)) + "\n")
    except SQLAlchemyError:
        parser.exit(1, "Database unavailable; user was not created\n")


if __name__ == "__main__":
    main()
