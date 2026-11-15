"""Чтение справочника и рыночных данных из БД: общее для карточек инструментов и расчёта"""

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import FxRate, Instrument, MarketDataSnapshot
from app.enums import ACTIVE_FX_SOURCES, ACTIVE_PROVIDERS

# при снимках нескольких источников за одну дату берётся первый по списку (Мосбиржа)
_PROVIDER_RANK = case(
    {p: i for i, p in enumerate(ACTIVE_PROVIDERS)},
    value=MarketDataSnapshot.provider,
    else_=len(ACTIVE_PROVIDERS),
)
_FX_SOURCE_RANK = case(
    {s.value: i for i, s in enumerate(ACTIVE_FX_SOURCES)},
    value=FxRate.source,
    else_=len(ACTIVE_FX_SOURCES),
)


async def find_instrument(db: AsyncSession, isin: str) -> Instrument | None:
    return await db.scalar(select(Instrument).where(Instrument.isin == isin))


async def latest_snapshots(
    db: AsyncSession, instrument_ids: Iterable[uuid.UUID], on: date | None = None
) -> dict[uuid.UUID, MarketDataSnapshot]:
    """Последний снимок каждого инструмента; с датой on берутся только данные не позже неё

    Учитываются только действующие источники (Мосбиржа, контрольный набор): сохранённые
    снимки Cbonds и RuData остаются в БД для старых расчётов, но в новые не попадают
    """
    ids = set(instrument_ids)
    if not ids:
        return {}
    query = select(MarketDataSnapshot).where(
        MarketDataSnapshot.instrument_id.in_(ids),
        MarketDataSnapshot.provider.in_(ACTIVE_PROVIDERS),
    )
    if on is not None:
        query = query.where(MarketDataSnapshot.data_as_of <= on)
    # DISTINCT ON оставляет по одной строке на инструмент: самую свежую по дате данных,
    # а за одну дату снимок более приоритетного источника
    query = query.order_by(
        MarketDataSnapshot.instrument_id,
        MarketDataSnapshot.data_as_of.desc(),
        _PROVIDER_RANK,
        MarketDataSnapshot.retrieved_at.desc(),
    ).ext(distinct_on(MarketDataSnapshot.instrument_id))
    return {snap.instrument_id: snap for snap in await db.scalars(query)}


async def latest_rates(db: AsyncSession, currencies: set[str], on: date) -> dict[str, FxRate]:
    """Последний курс каждой валюты не позже даты on; за одну дату курс ЦБ важнее демо"""
    if not currencies:
        return {}
    query = (
        select(FxRate)
        .where(FxRate.currency.in_(currencies), FxRate.rate_date <= on)
        .order_by(
            FxRate.currency, FxRate.rate_date.desc(), _FX_SOURCE_RANK, FxRate.retrieved_at.desc()
        )
        .ext(distinct_on(FxRate.currency))
    )
    return {rate.currency: rate for rate in await db.scalars(query)}
