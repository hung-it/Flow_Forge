import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field, JsonValue

from flowforge.auth.routes import CurrentUser
from flowforge.contracts import Contract
from flowforge.errors import ApiError
from flowforge.projects.routes import Limit, Offset

from . import service

router = APIRouter(prefix="/api/v1", tags=["runs"])

RunStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
TaskStatus = Literal[
    "PENDING",
    "READY",
    "QUEUED",
    "RUNNING",
    "RETRY_WAIT",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "CANCELED",
]


class RunInput(Contract):
    version: int = Field(ge=1, le=2147483647)
    input: dict[str, JsonValue]


class RunAccepted(BaseModel):
    run_id: UUID
    status: RunStatus


class RunOutput(RunAccepted):
    project_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    version: int
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TaskOutput(BaseModel):
    task_id: UUID
    node_key: str
    status: TaskStatus
    attempt_count: int
    predecessor_task_ids: list[UUID]


class AttemptOutput(BaseModel):
    attempt_no: int
    dispatch_generation: int
    worker_id: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "LOST", "CANCELED"]
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None


def timestamp_filter(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed
    except ValueError:
        pass
    raise ApiError(422, "INVALID_TIME_RANGE", "Use ISO 8601 timestamps with a time zone")


@router.post("/workflows/{workflow_id}/runs", status_code=202, response_model=RunAccepted)
async def create(
    workflow_id: UUID,
    body: RunInput,
    request: Request,
    response: Response,
    actor: CurrentUser,
    idempotency_key: Annotated[list[str] | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    key = None
    if idempotency_key is not None:
        if len(idempotency_key) != 1 or not re.fullmatch(
            r"[A-Za-z0-9._~-]{1,128}", idempotency_key[0]
        ):
            raise ApiError(422, "INVALID_IDEMPOTENCY_KEY", "Invalid Idempotency-Key")
        key = idempotency_key[0]
    result = await service.create(
        request.app.state.engine, actor, workflow_id, body.version, body.input, key
    )
    response.headers["Location"] = f"/api/v1/runs/{result['run_id']}"
    return result


@router.get("/runs", response_model=list[RunOutput])
async def listing(
    request: Request,
    actor: CurrentUser,
    project_id: UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
    workflow_id: UUID | None = None,
    status: RunStatus | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> list[dict[str, Any]]:
    start, end = timestamp_filter(created_from), timestamp_filter(created_to)
    if start is not None and end is not None and start >= end:
        raise ApiError(422, "INVALID_TIME_RANGE", "End must be after start")
    return await service.list_runs(
        request.app.state.engine, actor, project_id, limit, offset,
        workflow_id=workflow_id, status=status, created_from=start, created_to=end,
    )


@router.get("/runs/{run_id}", response_model=RunOutput)
async def detail(run_id: UUID, request: Request, actor: CurrentUser) -> dict[str, Any]:
    return await service.detail(request.app.state.engine, actor, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunOutput)
async def cancel(run_id: UUID, request: Request, actor: CurrentUser) -> dict[str, Any]:
    return await service.cancel(request.app.state.engine, actor, run_id)


@router.get("/runs/{run_id}/tasks", response_model=list[TaskOutput])
async def tasks(
    run_id: UUID,
    request: Request,
    actor: CurrentUser,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await service.tasks(request.app.state.engine, actor, run_id, limit, offset)


@router.get("/tasks/{task_id}/attempts", response_model=list[AttemptOutput])
async def attempts(
    task_id: UUID, request: Request, actor: CurrentUser, limit: Limit = 20, offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await service.attempts(request.app.state.engine, actor, task_id, limit, offset)
