from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from flowforge.auth.routes import CurrentUser
from flowforge.contracts import Contract
from flowforge.projects.routes import Limit, Offset, ProjectInput

from . import service

router = APIRouter(prefix="/api/v1", tags=["workflows"])


class DraftInput(Contract):
    expected_revision: int = Field(ge=1)
    dag: dict[str, Any]


class PublishInput(Contract):
    draft_revision: int = Field(ge=1)


class WorkflowOutput(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    latest_version: int | None


class DraftOutput(BaseModel):
    workflow_id: UUID
    dag: dict[str, Any]
    revision: int
    updated_by: UUID
    updated_at: datetime


class VersionMetadata(BaseModel):
    id: UUID
    workflow_id: UUID
    version: int
    checksum: str
    source_draft_revision: int
    created_by: UUID
    created_at: datetime


class VersionOutput(VersionMetadata):
    dag: dict[str, Any]
    handler_contracts: dict[str, Any]


@router.post("/projects/{project_id}/workflows", status_code=201, response_model=WorkflowOutput)
async def create(
    project_id: UUID, body: ProjectInput, request: Request, actor: CurrentUser
) -> dict[str, Any]:
    return await service.create(request.app.state.engine, actor, project_id, body.name)


@router.get("/projects/{project_id}/workflows", response_model=list[WorkflowOutput])
async def listing(
    project_id: UUID, request: Request, actor: CurrentUser, limit: Limit = 20, offset: Offset = 0,
    q: Annotated[str, Query(max_length=100)] = "",
) -> list[dict[str, Any]]:
    return await service.listing(request.app.state.engine, actor, project_id, limit, offset, q)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOutput)
async def detail(workflow_id: UUID, request: Request, actor: CurrentUser) -> dict[str, Any]:
    return await service.detail(request.app.state.engine, actor, workflow_id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowOutput)
async def rename(
    workflow_id: UUID, body: ProjectInput, request: Request, actor: CurrentUser
) -> dict[str, Any]:
    return await service.detail(request.app.state.engine, actor, workflow_id, body.name)


@router.get("/workflows/{workflow_id}/draft", response_model=DraftOutput)
async def draft(workflow_id: UUID, request: Request, actor: CurrentUser) -> dict[str, Any]:
    return await service.draft(request.app.state.engine, actor, workflow_id)


@router.put("/workflows/{workflow_id}/draft", response_model=DraftOutput)
async def save(
    workflow_id: UUID, body: DraftInput, request: Request, actor: CurrentUser
) -> dict[str, Any]:
    return await service.draft(
        request.app.state.engine, actor, workflow_id, body.dag, body.expected_revision
    )


@router.post("/workflows/{workflow_id}/versions", status_code=201, response_model=VersionOutput)
async def publish(
    workflow_id: UUID, body: PublishInput, request: Request, actor: CurrentUser
) -> dict[str, Any]:
    return await service.publish(request.app.state.engine, actor, workflow_id, body.draft_revision)


@router.get("/workflows/{workflow_id}/versions", response_model=list[VersionMetadata])
async def versions(
    workflow_id: UUID, request: Request, actor: CurrentUser, limit: Limit = 20, offset: Offset = 0
) -> list[dict[str, Any]]:
    return await service.versions(request.app.state.engine, actor, workflow_id, limit, offset)


@router.get("/workflows/{workflow_id}/versions/{version}", response_model=VersionOutput)
async def version_detail(
    workflow_id: UUID, request: Request, actor: CurrentUser, version: int = Path(ge=1)
) -> dict[str, Any]:
    return await service.version_detail(request.app.state.engine, actor, workflow_id, version)
