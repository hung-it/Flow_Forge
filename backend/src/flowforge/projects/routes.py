from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowforge.auth.routes import CurrentUser
from flowforge.auth.security import normalize_email

from . import service

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
MemberRole = Literal["DEVELOPER", "VIEWER"]


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class RoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: MemberRole


class AddMemberInput(RoleInput):
    email: str = Field(max_length=260)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return normalize_email(value)


class TransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class ProjectOutput(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    role: Literal["OWNER", "DEVELOPER", "VIEWER"]


class MemberOutput(BaseModel):
    user_id: UUID
    role: Literal["OWNER", "DEVELOPER", "VIEWER"]


class MemberDetail(MemberOutput):
    email: str


@router.post("", status_code=201, response_model=ProjectOutput)
async def create(body: ProjectInput, request: Request, actor: CurrentUser) -> ProjectOutput:
    return ProjectOutput.model_validate(
        await service.create(request.app.state.engine, actor, body.name)
    )


@router.get("", response_model=list[ProjectOutput])
async def listing(
    request: Request, actor: CurrentUser, limit: Limit = 20, offset: Offset = 0
) -> list[ProjectOutput]:
    rows = await service.listing(request.app.state.engine, actor, limit, offset)
    return [ProjectOutput.model_validate(row) for row in rows]


@router.get("/{project_id}", response_model=ProjectOutput)
async def detail(project_id: UUID, request: Request, actor: CurrentUser) -> ProjectOutput:
    return ProjectOutput.model_validate(
        await service.detail(request.app.state.engine, actor, project_id)
    )


@router.patch("/{project_id}", response_model=ProjectOutput)
async def rename(
    project_id: UUID, body: ProjectInput, request: Request, actor: CurrentUser
) -> ProjectOutput:
    return ProjectOutput.model_validate(
        await service.rename(request.app.state.engine, actor, project_id, body.name)
    )


@router.get("/{project_id}/members", response_model=list[MemberDetail])
async def members(
    project_id: UUID, request: Request, actor: CurrentUser, limit: Limit = 20, offset: Offset = 0
) -> list[MemberDetail]:
    rows = await service.members(request.app.state.engine, actor, project_id, limit, offset)
    return [MemberDetail.model_validate(row) for row in rows]


@router.post("/{project_id}/members", status_code=201, response_model=MemberOutput)
async def add_member(
    project_id: UUID, body: AddMemberInput, request: Request, actor: CurrentUser
) -> MemberOutput:
    return MemberOutput.model_validate(
        await service.add_member(request.app.state.engine, actor, project_id, body.email, body.role)
    )


@router.patch("/{project_id}/members/{user_id}", response_model=MemberOutput)
async def change_member(
    project_id: UUID, user_id: UUID, body: RoleInput, request: Request, actor: CurrentUser
) -> MemberOutput:
    return MemberOutput.model_validate(
        await service.change_member(request.app.state.engine, actor, project_id, user_id, body.role)
    )


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: UUID, user_id: UUID, request: Request, actor: CurrentUser
) -> Response:
    await service.change_member(request.app.state.engine, actor, project_id, user_id, None)
    return Response(status_code=204)


@router.post("/{project_id}/transfer-ownership", response_model=ProjectOutput)
async def transfer(
    project_id: UUID, body: TransferInput, request: Request, actor: CurrentUser
) -> ProjectOutput:
    return ProjectOutput.model_validate(
        await service.transfer(request.app.state.engine, actor, project_id, body.user_id)
    )
