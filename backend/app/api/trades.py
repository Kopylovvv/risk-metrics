"""Сделки набора: добавить, изменить, удалить. В ответе весь набор с новым revision"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.convert import workspace_out
from app.api.deps import Db, OwnWorkspaceForChange
from app.errors import error_responses
from app.schemas import TradeCreate, TradeUpdate, WorkspaceOut
from app.services import workspaces as service

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/trades", tags=["Сделки"])

ERRORS = error_responses(401, 403, 404, 409, 422, 503)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceOut,
    summary="Добавить сделку: ISIN и количество бумаг",
    responses=ERRORS,
)
async def add_trade(body: TradeCreate, workspace: OwnWorkspaceForChange, db: Db):
    await service.add_trade(db, workspace, body.isin, body.quantity, body.expected_revision)
    await db.commit()
    await db.refresh(workspace)
    return await workspace_out(db, workspace)


@router.patch(
    "/{trade_id}",
    response_model=WorkspaceOut,
    summary="Изменить ISIN и/или количество",
    responses=ERRORS,
)
async def update_trade(
    trade_id: uuid.UUID, body: TradeUpdate, workspace: OwnWorkspaceForChange, db: Db
):
    await service.update_trade(
        db, workspace, trade_id, body.expected_revision, isin=body.isin, quantity=body.quantity
    )
    await db.commit()
    await db.refresh(workspace)
    return await workspace_out(db, workspace)


@router.delete(
    "/{trade_id}",
    response_model=WorkspaceOut,
    summary="Удалить сделку",
    responses=ERRORS,
)
async def delete_trade(
    trade_id: uuid.UUID,
    workspace: OwnWorkspaceForChange,
    db: Db,
    # у DELETE нет тела, поэтому revision передаётся в строке запроса
    expected_revision: Annotated[int, Query(ge=0)],
):
    await service.delete_trade(db, workspace, trade_id, expected_revision)
    await db.commit()
    await db.refresh(workspace)
    return await workspace_out(db, workspace)
