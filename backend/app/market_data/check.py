"""Реальный запрос к Cbonds и нормализованный результат (подтверждение для #29)

Из папки backend:
  python -m app.market_data.check RU000A1038V6 [2026-10-24]   снимок по бумаге на дату
  python -m app.market_data.check --list                      какие бумаги видит логин

Логин и пароль берутся из CBONDS_LOGIN / CBONDS_PASSWORD (.env в корне) и не печатаются.
Код выхода 0: данные получены; 1: ошибка источника; 2: доступ не настроен
"""

import asyncio
import sys
from dataclasses import asdict
from datetime import date

from app.config import cbonds_credentials
from app.contracts import MarketDataError
from app.market_data import CbondsMarketData

# сырые поля, по которым видно реальный формат ответа (валюта, флаги, единицы дюрации)
EMISSION_FIELDS = ("isin_code", "currency_name", "nominal_price", "floating_rate", "cupon_period")
TRADING_FIELDS = (
    "isin_code",
    "date",
    "trading_ground_id",
    "indicative_price",
    "aci",
    "dur_mod",
    "convexity",
)


def source_or_exit() -> CbondsMarketData:
    credentials = cbonds_credentials()
    if credentials is None:
        print("CBONDS_LOGIN и CBONDS_PASSWORD не заданы в .env")
        sys.exit(2)
    print(f"Cbonds {credentials[2]}")
    return CbondsMarketData(*credentials)


async def check(isin: str, as_of: date) -> int:
    source = source_or_exit()
    print(f"ISIN {isin}, дата оценки {as_of}")
    try:
        instrument = await source.get_instrument(isin)
        print(f"Инструмент: {instrument.name} ({instrument.currency})")
        snapshot = await source.get_snapshot(isin, as_of)
    except MarketDataError as exc:
        print(f"Ошибка источника: {exc.code.value}: {exc}")
        return 1
    for field, value in asdict(snapshot).items():
        print(f"  {field:17} {value}")
    return 0


async def list_available() -> int:
    """Бумаги, у которых логину видны и параметры выпуска, и итоги торгов

    Демо-доступ открывает выпуски и торги по разным наборам бумаг, поэтому ищем пересечение
    """
    source = source_or_exit()
    try:
        # служебный скрипт того же модуля: сырой запрос без нормализации
        emissions = await source._post("get_emissions", filters=[], limit=100)
        isins = [row["isin_code"] for row in emissions if row.get("isin_code")]
        tradings = await source._post(
            "get_tradings_new",
            # in: значения через точку с запятой (спецификация Cbonds)
            filters=[{"field": "isin_code", "operator": "in", "value": ";".join(isins)}],
            limit=100,
            sorting=[{"field": "date", "order": "desc"}],
        )
    except MarketDataError as exc:
        print(f"Ошибка источника: {exc.code.value}: {exc}")
        return 1
    print(f"Выпусков видно: {len(emissions)}, строк итогов торгов по ним: {len(tradings)}")
    latest: dict[str, dict] = {}
    for row in tradings:  # уже по убыванию даты: первая строка бумаги самая свежая
        if row.get("indicative_price") is not None:
            latest.setdefault(row.get("isin_code"), row)
    if not latest:
        print("Нет бумаг с ценой: демо-доступ не пересекает выпуски и торги")
        return 1
    print("\nГотовы к проверке (последние торги с ценой):")
    by_isin = {row["isin_code"]: row for row in emissions if row.get("isin_code")}
    for isin, row in latest.items():
        print("  " + " | ".join(f"{f}={row.get(f)!r}" for f in TRADING_FIELDS))
        print(
            "    "
            + " | ".join(f"{f}={by_isin.get(isin, {}).get(f)!r}" for f in EMISSION_FIELDS[1:])
        )
    first = next(iter(latest.values()))
    print(f"\nДальше: python -m app.market_data.check {first['isin_code']} {first['date']}")
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "--list":
        sys.exit(asyncio.run(list_available()))
    as_of = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    sys.exit(asyncio.run(check(sys.argv[1].strip().upper(), as_of)))


if __name__ == "__main__":
    main()
