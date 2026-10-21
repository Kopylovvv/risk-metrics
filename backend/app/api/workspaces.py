"""Набор сделок: открыть сессию и прочитать набор"""

from fastapi import APIRouter, Response, status

from app.api.convert import workspace_out
from app.api.deps import (
    SESSION_COOKIE,
    Db,
    OwnWorkspace,
    SessionKey,
    csrf_token_for,
    new_session_key,
)
from app.config import cookie_secure
from app.errors import error_responses
from app.schemas import WorkspaceCreated, WorkspaceOut
from app.services.workspaces import create_workspace, find_workspace_by_session

router = APIRouter(prefix="/api/v1/workspaces", tags=["Набор сделок"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceCreated,
    summary="Открыть модуль: создать сессию и пустой набор",
    description=(
        "Ставит HttpOnly-cookie `rm_session` и возвращает `csrf_token` для заголовка "
        "`X-CSRF-Token`. Если cookie уже есть и набор найден, возвращает его (200), "
        "поэтому фронт может вызывать этот метод при каждом открытии страницы"
    ),
    responses={200: {"model": WorkspaceCreated, "description": "Набор этой сессии уже есть"}},
)
async def open_workspace(response: Response, db: Db, session_key: SessionKey = None):
    workspace = await find_workspace_by_session(db, session_key) if session_key else None
    if workspace is not None:
        response.status_code = status.HTTP_200_OK
    else:
        session_key = new_session_key()
        workspace = await create_workspace(db, session_key)
        await db.commit()
        await db.refresh(workspace)  # created_at/updated_at проставила БД
        response.set_cookie(
            SESSION_COOKIE,
            session_key,
            httponly=True,  # JavaScript не видит ключ сессии
            samesite="lax",
            secure=cookie_secure(),
            path="/",
        )
    body = await workspace_out(db, workspace)
    return WorkspaceCreated(**body.model_dump(), csrf_token=csrf_token_for(session_key))


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Набор сделок и его revision",
    responses=error_responses(401, 404, 422, 503),
)
async def get_workspace(workspace: OwnWorkspace, db: Db):
    return await workspace_out(db, workspace)
