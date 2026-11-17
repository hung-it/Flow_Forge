from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowforge.errors import ApiError, unauthorized

from . import service
from .security import COOKIE_NAME, COOKIE_PATH, issue_access, normalize_email, read_access

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(max_length=260)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return normalize_email(value)


class UserOutput(BaseModel):
    id: UUID
    email: str


class TokenOutput(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def current_user(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> service.Principal:
    if credentials is None:
        raise unauthorized()
    try:
        user, session = read_access(request.app.state.auth, credentials.credentials)
    except ValueError as error:
        raise unauthorized() from error
    async with request.app.state.engine.connect() as conn:
        return await service.principal(conn, user, session)


CurrentUser = Annotated[service.Principal, Depends(current_user)]


async def csrf(
    request: Request,
    x_flowforge_csrf: Annotated[
        str | None,
        Header(
            alias="X-FlowForge-CSRF", description="Required value: 1 for cookie-changing requests"
        ),
    ] = None,
) -> None:
    if x_flowforge_csrf != "1":
        raise ApiError(403, "CSRF_REJECTED", "CSRF header required")
    origin = request.headers.get("origin")
    if origin is not None and origin not in request.app.state.auth.allowed_origins:
        raise ApiError(403, "CSRF_REJECTED", "Origin not allowed")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ApiError(415, "JSON_REQUIRED", "Content-Type application/json required")


def token_response(
    request: Request, response: Response, user: UUID, session: UUID, raw: str, expiry: datetime
) -> TokenOutput:
    response.set_cookie(
        COOKIE_NAME,
        raw,
        httponly=True,
        secure=request.app.state.auth.cookie_secure,
        samesite="lax",
        path=COOKIE_PATH,
        max_age=max(0, int((expiry - datetime.now(UTC)).total_seconds())),
    )
    return TokenOutput(access_token=issue_access(request.app.state.auth, user, session))


@router.post("/login", response_model=TokenOutput, dependencies=[Depends(csrf)])
async def login(body: LoginInput, request: Request, response: Response) -> TokenOutput:
    result = await service.login(
        request.app.state.engine,
        body.email,
        body.password,
        request.client.host if request.client else "unknown",
    )
    return token_response(request, response, *result)


@router.get("/me", response_model=UserOutput)
async def me(actor: CurrentUser) -> UserOutput:
    return UserOutput(id=actor.user_id, email=actor.email)


@router.post("/refresh", response_model=TokenOutput, dependencies=[Depends(csrf)])
async def refresh(body: EmptyInput, request: Request, response: Response) -> TokenOutput:
    raw = request.cookies.get(COOKIE_NAME)
    if raw is None or len(raw) > 256:
        raise unauthorized()
    result = await service.refresh(request.app.state.engine, raw)
    return token_response(request, response, *result)


@router.post("/logout", status_code=204, dependencies=[Depends(csrf)])
async def logout(body: EmptyInput, request: Request) -> Response:
    raw = request.cookies.get(COOKIE_NAME)
    if raw is not None and len(raw) <= 256:
        await service.logout(request.app.state.engine, raw)
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        secure=request.app.state.auth.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
