"""История расчётов набора и восстановление его состава из прошлого расчёта

Сам расчёт ничем не меняется: восстановление создаёт новые сделки по его копии входов
(input_isin, input_quantity), а сохранённые результаты остаются как были
"""

import uuid
from datetime import timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import CalculationResult, CalculationRun, Instrument, Trade, Workspace
from app.enums import RowStatus
from app.services.calculation import get_calculation
from app.services.workspaces import _bump_revision, _commit


class CalculationNotInWorkspace(Exception):
    """Расчёта нет или он относится к другому набору"""


async def list_calculations(
    db: AsyncSession, workspace: Workspace, limit: int, offset: int
) -> tuple[list[dict], int]:
    """Расчёты набора, новые сверху, с числом позиций и ошибок; плюс общее количество"""
    counts = (
        select(
            CalculationResult.run_id,
            func.count().label("positions"),
            func.sum((CalculationResult.status == RowStatus.FAILED).cast(Integer)).label("failed"),
        )
        .join(CalculationRun, CalculationRun.id == CalculationResult.run_id)
        .where(CalculationRun.workspace_id == workspace.id)  # считаем только строки этого набора
        .group_by(CalculationResult.run_id)
        .subquery()
    )
    query = (
        select(CalculationRun, counts.c.positions, counts.c.failed)
        .outerjoin(counts, counts.c.run_id == CalculationRun.id)
        .where(CalculationRun.workspace_id == workspace.id)
        .order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(query)).all()
    total = await db.scalar(
        select(func.count())
        .select_from(CalculationRun)
        .where(CalculationRun.workspace_id == workspace.id)
    )
    items = [
        {
            "run": run,
            "positions": positions or 0,
            "failed_positions": failed or 0,
            "is_current": run.workspace_revision == workspace.revision,
        }
        for run, positions, failed in rows
    ]
    return items, total or 0


async def restore_from_calculation(
    db: AsyncSession, workspace: Workspace, calculation_id: uuid.UUID, expected_revision: int
) -> None:
    """Заменить сделки набора входами прошлого расчёта одной транзакцией, revision + 1"""
    found = await get_calculation(db, calculation_id)
    if found is None or found[0].workspace_id != workspace.id:
        raise CalculationNotInWorkspace
    # порядок как в ответе расчёта; читаем до удаления сделок, пока он ещё известен
    inputs = [(r.input_isin, r.input_quantity) for r in found[1]]

    await _bump_revision(db, workspace, expected_revision)
    current = await db.scalars(select(Trade).where(Trade.workspace_id == workspace.id))
    for trade in current:
        # в результатах прошлых расчётов trade_id станет NULL, их значения не меняются
        await db.delete(trade)
    await db.flush()

    isins = {isin for isin, _ in inputs}
    instruments = {
        i.isin: i.id for i in await db.scalars(select(Instrument).where(Instrument.isin.in_(isins)))
    }
    for position, (isin, quantity) in enumerate(inputs):
        db.add(
            Trade(
                workspace_id=workspace.id,
                instrument_id=instruments.get(isin),
                isin=isin,
                quantity=quantity,
                # now() одинаков для всей транзакции: сдвиг сохраняет порядок сделок в списке
                created_at=func.now() + timedelta(microseconds=position),
            )
        )
    await _commit(db, workspace)
