import logging
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flowforge.auth.service import Principal, principal
from flowforge.errors import ApiError

logger = logging.getLogger(__name__)


async def visible(
    conn: AsyncConnection, actor: Principal, project: UUID, *, write: bool = False
) -> RowMapping:
    if write:
        await principal(conn, actor.user_id, actor.session_id, lock=True)
        await conn.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), dict(p=project))
    # A separate statement after obtaining the project lock sees current memberships.
    row = (
        (
            await conn.execute(
                text("""SELECT p.id,p.name,p.owner_id,p.created_at,m.role
        FROM projects p JOIN project_members m ON m.project_id=p.id
        WHERE p.id=:p AND m.user_id=:u"""),
                dict(p=project, u=actor.user_id),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Project not found")
    if write and row["role"] != "OWNER":
        raise ApiError(403, "FORBIDDEN", "OWNER role required")
    return row


async def create(engine: AsyncEngine, actor: Principal, name: str) -> RowMapping:
    async with engine.begin() as conn:
        await principal(conn, actor.user_id, actor.session_id, lock=True)
        project = uuid4()
        await conn.execute(
            text("INSERT INTO projects(id,owner_id,name) VALUES(:p,:u,:n)"),
            dict(p=project, u=actor.user_id, n=name),
        )
        await conn.execute(
            text("""INSERT INTO project_members(project_id,user_id,role)
            VALUES(:p,:u,'OWNER')"""),
            dict(p=project, u=actor.user_id),
        )
        result = await visible(conn, actor, project)
    logger.info("project_created project_id=%s actor_id=%s", project, actor.user_id)
    return result


async def listing(
    engine: AsyncEngine, actor: Principal, limit: int, offset: int
) -> list[RowMapping]:
    async with engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    text("""SELECT p.id,p.name,p.owner_id,p.created_at,m.role
            FROM projects p JOIN project_members m ON m.project_id=p.id WHERE m.user_id=:u
            ORDER BY p.created_at,p.id LIMIT :lim OFFSET :off"""),
                    dict(u=actor.user_id, lim=limit, off=offset),
                )
            ).mappings()
        )


async def detail(engine: AsyncEngine, actor: Principal, project: UUID) -> RowMapping:
    async with engine.connect() as conn:
        return await visible(conn, actor, project)


async def rename(engine: AsyncEngine, actor: Principal, project: UUID, name: str) -> RowMapping:
    async with engine.begin() as conn:
        await visible(conn, actor, project, write=True)
        await conn.execute(text("UPDATE projects SET name=:n WHERE id=:p"), dict(p=project, n=name))
        return await visible(conn, actor, project)


async def members(
    engine: AsyncEngine, actor: Principal, project: UUID, limit: int, offset: int
) -> list[RowMapping]:
    async with engine.connect() as conn:
        await visible(conn, actor, project)
        return list(
            (
                await conn.execute(
                    text("""SELECT m.user_id,u.email,m.role
            FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=:p
            ORDER BY m.user_id LIMIT :lim OFFSET :off"""),
                    dict(p=project, lim=limit, off=offset),
                )
            ).mappings()
        )


async def add_member(
    engine: AsyncEngine, actor: Principal, project: UUID, email: str, role: str
) -> RowMapping:
    async with engine.begin() as conn:
        await visible(conn, actor, project, write=True)
        user = (
            await conn.execute(text("SELECT id FROM users WHERE email=:e"), dict(e=email))
        ).scalar_one_or_none()
        if user is None:
            raise ApiError(404, "NOT_FOUND", "User not found")
        added = (
            (
                await conn.execute(
                    text("""INSERT INTO project_members(project_id,user_id,role)
            VALUES(:p,:u,:r) ON CONFLICT DO NOTHING RETURNING user_id,role"""),
                    dict(p=project, u=user, r=role),
                )
            )
            .mappings()
            .first()
        )
        if added is None:
            raise ApiError(409, "MEMBER_EXISTS", "User is already a member")
        return added


async def change_member(
    engine: AsyncEngine, actor: Principal, project: UUID, user: UUID, role: str | None
) -> RowMapping | None:
    async with engine.begin() as conn:
        await visible(conn, actor, project, write=True)
        current = (
            await conn.execute(
                text("""SELECT role FROM project_members
            WHERE project_id=:p AND user_id=:u"""),
                dict(p=project, u=user),
            )
        ).scalar_one_or_none()
        if current is None:
            raise ApiError(404, "NOT_FOUND", "Member not found")
        if current == "OWNER":
            raise ApiError(409, "OWNER_REQUIRED", "Transfer ownership before changing OWNER")
        if role is None:
            await conn.execute(
                text("DELETE FROM project_members WHERE project_id=:p AND user_id=:u"),
                dict(p=project, u=user),
            )
            return None
        return (
            (
                await conn.execute(
                    text("""UPDATE project_members SET role=:r
            WHERE project_id=:p AND user_id=:u RETURNING user_id,role"""),
                    dict(p=project, u=user, r=role),
                )
            )
            .mappings()
            .one()
        )


async def transfer(
    engine: AsyncEngine, actor: Principal, project: UUID, target: UUID
) -> RowMapping:
    async with engine.begin() as conn:
        await visible(conn, actor, project, write=True)
        if target == actor.user_id:
            raise ApiError(409, "ALREADY_OWNER", "User is already OWNER")
        member = (
            await conn.execute(
                text("""SELECT user_id FROM project_members
            WHERE project_id=:p AND user_id=:u"""),
                dict(p=project, u=target),
            )
        ).scalar_one_or_none()
        if member is None:
            raise ApiError(404, "NOT_FOUND", "Target must be an existing member")
        await conn.execute(
            text("""UPDATE project_members SET role='DEVELOPER'
            WHERE project_id=:p AND user_id=:u"""),
            dict(p=project, u=actor.user_id),
        )
        await conn.execute(
            text("""UPDATE project_members SET role='OWNER'
            WHERE project_id=:p AND user_id=:u"""),
            dict(p=project, u=target),
        )
        await conn.execute(
            text("UPDATE projects SET owner_id=:u WHERE id=:p"), dict(p=project, u=target)
        )
        result = await visible(conn, actor, project)
    logger.info("project_owner_transferred project_id=%s actor_id=%s", project, actor.user_id)
    return result
