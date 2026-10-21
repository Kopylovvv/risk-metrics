"""Работа с наборами и сделками в БД. Транзакцией управляет вызывающий код (роутер)"""

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import Instrument, MarketDataSnapshot, Trade, Workspace


class RevisionConflict(Exception):
    """Набор уже изменён: expected_revision не совпал с текущим"""


class TradeNotFound(Exception):
    """Сделки нет в этом наборе"""


def hash_session_key(session_key: str) -> str:
    # в БД храним только хэш: утечка таблицы не даёт войти в чужую сессию
    return hashlib.sha256(session_key.encode()).hexdigest()


async def create_workspace(db: AsyncSession, session_key: str) -> Workspace:
    workspace = Workspace(session_key_hash=hash_session_key(session_key))
    db.add(workspace)
    await db.flush()
    return workspace


async def find_workspace_by_session(db: AsyncSession, session_key: str) -> Workspace | None:
    query = select(Workspace).where(Workspace.session_key_hash == hash_session_key(session_key))
    return await db.scalar(query)


async def list_trades(db: AsyncSession, workspace_id: uuid.UUID) -> list[Trade]:
    query = (
        select(Trade)
        .where(Trade.workspace_id == workspace_id)
        .options(selectinload(Trade.instrument))
        .order_by(Trade.created_at, Trade.id)
        # свежие значения из БД (updated_at ставит БД), даже если объект уже в сессии
        .execution_options(populate_existing=True)
    )
    return list(await db.scalars(query))


async def latest_snapshots(
    db: AsyncSession, instrument_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, MarketDataSnapshot]:
    """Последний снимок по каждому инструменту (для карточки бумаги в ответе)"""
    ids = set(instrument_ids)
    if not ids:
        return {}
    # DISTINCT ON оставляет по одной строке на инструмент: самую свежую по дате данных
    query = (
        select(MarketDataSnapshot)
        .where(MarketDataSnapshot.instrument_id.in_(ids))
        .order_by(
            MarketDataSnapshot.instrument_id,
            MarketDataSnapshot.data_as_of.desc(),
            MarketDataSnapshot.retrieved_at.desc(),
        )
        .ext(distinct_on(MarketDataSnapshot.instrument_id))
    )
    return {snap.instrument_id: snap for snap in await db.scalars(query)}


async def find_instrument(db: AsyncSession, isin: str) -> Instrument | None:
    return await db.scalar(select(Instrument).where(Instrument.isin == isin))


async def _bump_revision(db: AsyncSession, workspace: Workspace, expected: int) -> None:
    """revision + 1 одним UPDATE с проверкой ожидаемого значения

    UPDATE блокирует строку набора до конца транзакции, поэтому два одновременных
    изменения не пройдут оба: второе увидит новый revision и получит 409
    """
    query = (
        update(Workspace)
        .where(Workspace.id == workspace.id, Workspace.revision == expected)
        .values(revision=Workspace.revision + 1, updated_at=func.now())
        .returning(Workspace.revision)
    )
    new_revision = await db.scalar(query)
    if new_revision is None:
        raise RevisionConflict
    workspace.revision = new_revision


async def _get_trade(db: AsyncSession, workspace: Workspace, trade_id: uuid.UUID) -> Trade:
    trade = await db.get(Trade, trade_id)
    if trade is None or trade.workspace_id != workspace.id:  # чужую сделку считаем несуществующей
        raise TradeNotFound
    return trade


async def add_trade(
    db: AsyncSession, workspace: Workspace, isin: str, quantity: int, expected_revision: int
) -> Trade:
    await _bump_revision(db, workspace, expected_revision)
    instrument = await find_instrument(db, isin)
    trade = Trade(
        workspace_id=workspace.id,
        instrument_id=instrument.id
        if instrument
        else None,  # бумаги нет в справочнике: сделку всё равно сохраняем
        isin=isin,
        quantity=quantity,
    )
    db.add(trade)
    await db.flush()
    return trade


async def update_trade(
    db: AsyncSession,
    workspace: Workspace,
    trade_id: uuid.UUID,
    expected_revision: int,
    isin: str | None = None,
    quantity: int | None = None,
) -> Trade:
    trade = await _get_trade(db, workspace, trade_id)
    await _bump_revision(db, workspace, expected_revision)
    if isin is not None and isin != trade.isin:
        instrument = await find_instrument(db, isin)
        trade.isin = isin
        trade.instrument_id = instrument.id if instrument else None
    if quantity is not None:
        trade.quantity = quantity
    await db.flush()
    return trade


async def delete_trade(
    db: AsyncSession, workspace: Workspace, trade_id: uuid.UUID, expected_revision: int
) -> None:
    trade = await _get_trade(db, workspace, trade_id)
    await _bump_revision(db, workspace, expected_revision)
    # результаты прошлых расчётов остаются: trade_id в них станет NULL (ON DELETE SET NULL)
    await db.delete(trade)
    await db.flush()
