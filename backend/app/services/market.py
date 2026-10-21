"""Чтение справочника и рыночных данных из БД: общее для карточек инструментов и расчёта"""

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import FxRate, Instrument, MarketDataSnapshot


async def find_instrument(db: AsyncSession, isin: str) -> Instrument | None:
    return await db.scalar(select(Instrument).where(Instrument.isin == isin))


async def latest_snapshots(
    db: AsyncSession, instrument_ids: Iterable[uuid.UUID], on: date | None = None
) -> dict[uuid.UUID, MarketDataSnapshot]:
    """Последний снимок каждого инструмента; с датой on берутся только данные не позже неё"""
    ids = set(instrument_ids)
    if not ids:
        return {}
    query = select(MarketDataSnapshot).where(MarketDataSnapshot.instrument_id.in_(ids))
    if on is not None:
        query = query.where(MarketDataSnapshot.data_as_of <= on)
    # DISTINCT ON оставляет по одной строке на инструмент: самую свежую по дате данных
    query = query.order_by(
        MarketDataSnapshot.instrument_id,
        MarketDataSnapshot.data_as_of.desc(),
        MarketDataSnapshot.retrieved_at.desc(),
    ).ext(distinct_on(MarketDataSnapshot.instrument_id))
    return {snap.instrument_id: snap for snap in await db.scalars(query)}


async def latest_rates(db: AsyncSession, currencies: set[str], on: date) -> dict[str, FxRate]:
    """Последний курс каждой валюты не позже даты on"""
    if not currencies:
        return {}
    query = (
        select(FxRate)
        .where(FxRate.currency.in_(currencies), FxRate.rate_date <= on)
        .order_by(FxRate.currency, FxRate.rate_date.desc(), FxRate.retrieved_at.desc())
        .ext(distinct_on(FxRate.currency))
    )
    return {rate.currency: rate for rate in await db.scalars(query)}
