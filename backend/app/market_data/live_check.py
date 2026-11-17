"""Реальный запрос к Мосбирже и ЦБ и нормализованный результат (проверка для #65, #66, #67)

Из папки backend, нужен интернет:
  python -m app.market_data.live_check                              ОФЗ 26238 и ГазКЗ-34Д
  python -m app.market_data.live_check RU000A1038V6 [2026-11-13]    одна бумага на дату

Печатает справочные данные, снимок (что сохранит backend) и курс ЦБ для валюты номинала
Код выхода 0: все данные получены; 1: хотя бы одна ошибка источника
"""

import asyncio
import sys
from dataclasses import asdict
from datetime import date

from app.contracts import MarketDataError
from app.market_data import CbrFxRates, MoexMarketData

DEFAULT_ISINS = ("RU000A1038V6", "RU000A105A95")  # ОФЗ 26238 (RUB), ГазКЗ-34Д (USD)


async def check(isin: str, as_of: date) -> bool:
    market, fx = MoexMarketData(), CbrFxRates()
    print(f"\nISIN {isin}, дата оценки {as_of}")
    try:
        info = await market.get_instrument(isin)
        print(f"  {info.name} ({info.currency})")
        snapshot = await market.get_snapshot(isin, as_of)
        for field, value in asdict(snapshot).items():
            print(f"  {field:20} {value}")
        rate = await fx.get_rate_to_rub(snapshot.currency, as_of)
        print(f"  курс ЦБ {rate.currency} на {rate.rate_date}: {rate.rate_to_rub} ₽ за 1")
    except MarketDataError as exc:
        print(f"  ошибка источника: {exc.code.value}: {exc}")
        return False
    return True


async def main(args: list[str]) -> int:
    isins = args[:1] or list(DEFAULT_ISINS)
    as_of = date.fromisoformat(args[1]) if len(args) > 1 else date.today()
    results = [await check(isin, as_of) for isin in isins]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
