import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from flowforge.config import Settings
from flowforge.observability import configure_logging
from flowforge.persistence import check_database


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        settings = Settings()
        app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="FlowForge", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("api").info(
            "request_id=%s method=%s path=%s status=%s",
            request_id, request.method, request.url.path, response.status_code,
        )
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        try:
            healthy = await asyncio.wait_for(
                check_database(request.app.state.engine), timeout=5
            )
        except (SQLAlchemyError, OSError, TimeoutError):
            logging.getLogger("api").warning("database readiness failed")
            healthy = False
        return JSONResponse(
            {"status": "ready" if healthy else "unavailable"},
            status_code=200 if healthy else 503,
        )

    return app


app = create_app()
