"""Временные источники данных, пока не подключены адаптеры Мосбиржи и курсов ЦБ

Реализуют протоколы из contracts.py: расчёт берёт данные только из БД, а за недостающими
«идёт» сюда и получает понятную ошибку строки. Подключаются в app/api/deps.py
"""

from datetime import date

from app.contracts import FxRate, InstrumentInfo, MarketDataError, MarketSnapshot
from app.enums import Provider, RowErrorCode


class NoMarketDataSource:
    provider: Provider | None = None  # своих снимков нет: подходит любой сохранённый

    async def get_instrument(self, isin: str) -> InstrumentInfo:
        raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, "Мосбиржа ещё не подключена")

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, "Мосбиржа ещё не подключена")


class NoFxRateSource:
    source: str | None = None

    async def get_rate_to_rub(self, currency: str, on: date) -> FxRate:
        raise MarketDataError(
            RowErrorCode.FX_PROVIDER_UNAVAILABLE, "источник курсов ЦБ ещё не подключён"
        )
