import asyncio
import json
import logging
import math
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flowforge.auth.routes import router as auth_router
from flowforge.auth.security import COOKIE_NAME, COOKIE_PATH, AuthSettings
from flowforge.config import Settings
from flowforge.errors import ApiError
from flowforge.observability import configure_logging
from flowforge.persistence import check_database
from flowforge.projects.routes import router as projects_router
from flowforge.workflows.routes import router as workflows_router


class AuthBodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        auth = path.startswith("/api/v1/auth/")
        workflow = path.startswith("/api/v1/workflows/") or (
            path.startswith("/api/v1/projects/") and path.endswith("/workflows")
        )
        if not auth and not workflow:
            await self.app(scope, receive, send)
            return
        cap = 8192 if auth else 256 * 1024

        def invalid_constant(value: str) -> None:
            raise ValueError("Non-finite JSON")

        def finite_float(value: str) -> float:
            result = float(value)
            if not math.isfinite(result):
                raise ValueError("Non-finite JSON")
            return result

        def valid_text(value: object) -> None:
            if isinstance(value, str):
                if "\x00" in value:
                    raise ValueError("NUL is unsupported by PostgreSQL JSONB")
                value.encode("utf-8")
            elif isinstance(value, dict):
                for key, item in value.items():
                    valid_text(key)
                    valid_text(item)
            elif isinstance(value, list):
                for item in value:
                    valid_text(item)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > cap:
                await JSONResponse(
                    dict(
                        code="PAYLOAD_TOO_LARGE",
                        message="Request body exceeds limit",
                        details=[],
                        request_id=scope.get("state", {}).get("request_id"),
                    ),
                    status_code=413,
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        if workflow and scope["method"] in {"POST", "PUT", "PATCH"}:
            headers = dict(scope.get("headers", []))
            content_type = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
            status, code = 415, "UNSUPPORTED_MEDIA_TYPE"
            if content_type == b"application/json":
                status, code = 422, "VALIDATION_ERROR"
                try:
                    valid_text(
                        json.loads(body, parse_constant=invalid_constant, parse_float=finite_float)
                    )
                except (ValueError, RecursionError):
                    pass
                else:
                    status = 0
            if status:
                await JSONResponse(
                    dict(
                        code=code,
                        message="Invalid JSON request",
                        details=[],
                        request_id=scope.get("state", {}).get("request_id"),
                    ),
                    status_code=status,
                )(scope, receive, send)
                return
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        settings = Settings()
        app.state.auth = AuthSettings()
        app.state.engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=0,
            pool_timeout=5,
            connect_args={"timeout": 5, "command_timeout": 10},
        )
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="FlowForge", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthBodyLimit)
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(workflows_router)

    @app.exception_handler(ApiError)
    async def business_error(request: Request, error: ApiError) -> JSONResponse:
        response = JSONResponse(
            dict(
                code=error.code,
                message=error.message,
                details=[],
                request_id=request.state.request_id,
            ),
            status_code=error.status,
        )
        if error.status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
            if request.url.path == "/api/v1/auth/refresh":
                response.delete_cookie(
                    COOKIE_NAME,
                    path=COOKIE_PATH,
                    httponly=True,
                    secure=request.app.state.auth.cookie_secure,
                    samesite="lax",
                )
        if error.status == 429:
            response.headers["Retry-After"] = "60"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            dict(
                code="VALIDATION_ERROR",
                message="Invalid request",
                details=[dict(loc=e["loc"], type=e["type"]) for e in error.errors()],
                request_id=request.state.request_id,
            ),
            status_code=422,
        )

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    @app.exception_handler(TimeoutError)
    async def database_error(request: Request, error: Exception) -> JSONResponse:
        logging.getLogger("api").error(
            "database operation failed request_id=%s", request.state.request_id
        )
        return JSONResponse(
            dict(
                code="DATABASE_UNAVAILABLE",
                message="Database unavailable",
                details=[],
                request_id=request.state.request_id,
            ),
            status_code=503,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            dict(
                code="HTTP_ERROR",
                message="Request failed",
                details=[],
                request_id=request.state.request_id,
            ),
            status_code=error.status_code,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/v1/auth/"):
            response.headers["Cache-Control"] = "no-store"
        logging.getLogger("api").info(
            "request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        try:
            healthy = await asyncio.wait_for(check_database(request.app.state.engine), timeout=5)
        except (SQLAlchemyError, OSError, TimeoutError):
            logging.getLogger("api").warning("database readiness failed")
            healthy = False
        return JSONResponse(
            {"status": "ready" if healthy else "unavailable"},
            status_code=200 if healthy else 503,
        )

    return app


app = create_app()
