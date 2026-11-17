"""Адаптер Московской биржи (ISS): параметры облигации на дату оценки → MarketSnapshot

Запросы публичные, без ключа (https://iss.moex.com/iss/reference/). Ответ ISS: блоки
{"columns": [...], "data": [[...], ...]}; длинные блоки постраничные (блок <имя>.cursor:
INDEX, TOTAL, PAGESIZE, следующая страница по параметру start). Сверено с живыми ответами
15.11.2026: ОФЗ 26238 (RU000A1038V6), ГазКЗ-34Д (RU000A105A95, номинал USD), ОФЗ 29021
(флоатер). Записи ответов и карта полей: tests/fixtures/moex, docs/market-data-map.md

Запросы:
1. securities.json?q=ISIN: SECID (у ОФЗ он не равен ISIN: SU26238RMFS4) и основной режим
2. securities/{SECID}.json, блок description: FACEUNIT, BOND_TYPE, COUPONFREQUENCY, NAME
3. history/engines/stock/markets/bonds/securities/{SECID}.json: итоги торгов по режимам
   за 14 дней до даты оценки: LEGALCLOSEPRICE, CLOSE (% от номинала), ACCINT, FACEVALUE
4. history/engines/stock/markets/bonds/yields/{SECID}.json: EFFECTIVEYIELD (%), DURATION
   (дюрация Маколея в днях), YIELDDATE (к какой дате считала биржа: погашение или оферта)
5. statistics/engines/stock/markets/bonds/bondization/{SECID}.json, только fixed и zero:
   график купонов и погашений номинала для дюрации и выпуклости

Нормализация:
* data_as_of: последняя дата торгов не позже as_of и не раньше as_of − 14 дней. Берутся
  итоги дня, а не текущие котировки: повторный запрос на ту же дату даёт те же числа
* currency: FACEUNIT, SUR → RUB. settlement_currency: CURRENCYID основного режима, SUR → RUB
* accrued_interest: ACCINT на дату торгов из режима, где CURRENCYID = валюте номинала.
  ACCINT дан в валюте расчётов режима: у ГазКЗ-34Д в TQOD 3.59 USD, в TQCB те же 3.59 USD,
  пересчитанные по курсу ЦБ (297.8993 ₽). Режима в валюте номинала нет:
  INCOMPATIBLE_MARKET_DATA, НКД сами не пересчитываем
* price_pct: LEGALCLOSEPRICE (официальная цена закрытия), если её нет, CLOSE; в любом режиме
  за ту же дату. ISS даёт цену в % от номинала (описание поля), множитель 1 (units.py)
* nominal: FACEVALUE (непогашенный долг на дату торгов, у амортизируемых меньше начального)
* duration_mod, convexity у fixed и zero: по графику выплат после даты расчётов
  (следующий рабочий день после торгов, праздники не учитываются) при доходности
  EFFECTIVEYIELD, см. bond_math.py. Дюрация по тем же потокам сверяется с DURATION из ISS;
  не совпала или у купонов нет сумм: оба None, строка получит MISSING_MARKET_DATA
* floating: D = 1/частота и C = 0 по правилу ТЗ подставляет risk_core, здесь оба None
* coupon_type: BOND_TYPE с «плавающим» или «Флоатер» → floating, «Дисконтная» → zero,
  иначе fixed; coupon_frequency: COUPONFREQUENCY, 0 или пусто → None
* нет значения у источника → None, не 0

Ошибки (MarketDataError):
* INSTRUMENT_NOT_FOUND: ISIN не найден или это не облигация
* MISSING_MARKET_DATA: нет итогов торгов с ценой за 14 дней до даты оценки
* INCOMPATIBLE_MARKET_DATA: чужой ISIN или SECID, дата позже as_of, валюта не ISO,
  номинал или цена ≤ 0, НКД < 0, не число, нет режима в валюте номинала
* PROVIDER_UNAVAILABLE: сеть, таймаут, HTTP-ошибка, не JSON, нет нужного блока
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

import httpx

from app.contracts import InstrumentInfo, MarketDataError, MarketSnapshot
from app.enums import CouponType, Provider, RowErrorCode
from app.market_data.bond_math import CashFlow, bond_risk_matching
from app.market_data.units import PRICE_PERCENT, price_to_pct

logger = logging.getLogger(__name__)

ISS_URL = "https://iss.moex.com/iss/"
TIMEOUT_SECONDS = 10.0
USER_AGENT = "risk-metrics/0.4 (+https://github.com/Kopylovvv/risk-metrics)"
# итоги торгов старше двух недель для оценки не берём: строка получит MISSING_MARKET_DATA
HISTORY_DAYS = 14
PAGE_LIMIT = 100
MAX_PAGES = 20  # защита от зацикливания на кривом cursor
BOND_GROUP = "stock_bonds"
# ISS: LEGALCLOSEPRICE «Цена закрытия, % от номинала», CLOSE «Цена последней сделки, %»
ISS_PRICE_UNIT = PRICE_PERCENT
ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")
# рубль в ISS пишется SUR (в FACEUNIT и CURRENCYID), в части ответов RUB
CURRENCY_ALIASES = {"SUR": "RUB", "RUR": "RUB"}

SEARCH_COLUMNS = "secid,isin,shortname,name,group,primary_boardid,is_traded"
HISTORY_COLUMNS = (
    "TRADEDATE,SECID,BOARDID,LEGALCLOSEPRICE,CLOSE,ACCINT,FACEVALUE,FACEUNIT,CURRENCYID"
)
YIELD_COLUMNS = (
    "TRADEDATE,SECID,BOARDID,YIELDDATE,YIELDDATETYPE,PRICE,ACCINT,EFFECTIVEYIELD,DURATION"
)

Row = dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Bond:
    """Справочные данные выпуска из поиска и description"""

    isin: str
    secid: str
    primary_board: str | None
    name: str | None
    currency: str  # валюта номинала, ISO
    bond_type: str
    coupon_frequency: int | None


@dataclass(frozen=True, slots=True)
class _Trading:
    """Итоги торгов, выбранные на одну дату"""

    trade_date: date
    face_row: Row  # режим в валюте номинала: НКД и номинал
    price_row: Row  # откуда взята цена
    settlement_currency: str | None


class MoexMarketData:
    """Реализует contracts.MarketDataProvider поверх ISS Московской биржи"""

    provider = Provider.MOEX

    def __init__(
        self,
        base_url: str = ISS_URL,
        transport: httpx.AsyncBaseTransport | None = None,  # подмена сети в тестах
    ):
        self._base_url = base_url.rstrip("/") + "/"
        self._transport = transport
        # справочник нужен и get_instrument, и get_snapshot: один раз на ISIN
        # объект живёт один HTTP-запрос к backend (создаётся в deps.py)
        self._bonds: dict[str, _Bond] = {}

    async def get_instrument(self, isin: str) -> InstrumentInfo:
        async with self._client() as client:
            bond = await self._bond(client, isin)
        return InstrumentInfo(
            isin=isin, name=bond.name, instrument_type="bond", currency=bond.currency
        )

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        async with self._client() as client:
            bond = await self._bond(client, isin)
            history = await self._table(
                client,
                f"history/engines/stock/markets/bonds/securities/{bond.secid}.json",
                "history",
                {
                    "from": (as_of - timedelta(days=HISTORY_DAYS)).isoformat(),
                    "till": as_of.isoformat(),
                    "history.columns": HISTORY_COLUMNS,
                },
            )
            trading = _pick_trading(history, bond, as_of)
            coupon_type = _coupon_type(bond.bond_type)
            duration_mod = convexity = None
            if coupon_type != CouponType.FLOATING:
                duration_mod, convexity = await self._duration_convexity(client, bond, trading)

        price_raw = _decimal(trading.price_row, "LEGALCLOSEPRICE")
        if price_raw is None:
            price_raw = _decimal(trading.price_row, "CLOSE")
        price = price_to_pct(price_raw, ISS_PRICE_UNIT) if price_raw is not None else None
        nominal = _decimal(trading.face_row, "FACEVALUE")
        accrued = _decimal(trading.face_row, "ACCINT")
        # в БД стоят CHECK nominal > 0 и price_pct > 0: отсекаем до записи
        if (nominal is not None and nominal <= 0) or (price is not None and price <= 0):
            _incompatible(f"{isin}: номинал или цена ≤ 0")
        if accrued is not None and accrued < 0:
            _incompatible(f"{isin}: НКД < 0")
        return MarketSnapshot(
            isin=isin,
            provider=Provider.MOEX,
            data_as_of=trading.trade_date,
            retrieved_at=datetime.now(UTC),
            currency=bond.currency,
            nominal=nominal,
            price_pct=price,
            accrued_interest=accrued,
            duration_mod=duration_mod,
            convexity=convexity,
            coupon_type=coupon_type,
            coupon_frequency=bond.coupon_frequency,
            settlement_currency=trading.settlement_currency,
        )

    # --- справочник --------------------------------------------------------------------

    async def _bond(self, client: httpx.AsyncClient, isin: str) -> _Bond:
        if isin in self._bonds:
            return self._bonds[isin]
        found = await self._table(
            client,
            "securities.json",
            "securities",
            {"q": isin, "securities.columns": SEARCH_COLUMNS},
        )
        same_isin = [row for row in found if row.get("isin") == isin]
        bonds = [row for row in same_isin if row.get("group") == BOND_GROUP]
        if not bonds:
            what = "не облигация" if same_isin else "не найден"
            raise MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND, f"Мосбиржа: {isin} {what}")
        # один ISIN бывает у нескольких SECID (перерегистрация): берём торгуемый
        best = max(bonds, key=lambda row: row.get("is_traded") == 1)
        secid = str(best.get("secid") or "")
        if not secid:
            _unavailable("Мосбиржа: в поиске нет SECID")
        description = {
            str(row.get("name")): row.get("value")
            for row in await self._table(
                client,
                f"securities/{secid}.json",
                "description",
                {"description.columns": "name,value"},
            )
        }
        if not description:
            raise MarketDataError(
                RowErrorCode.INSTRUMENT_NOT_FOUND, f"Мосбиржа: нет описания {secid}"
            )
        if description.get("ISIN") not in (None, isin):
            _incompatible(f"Мосбиржа вернула описание другого ISIN вместо {isin}")
        bond = _Bond(
            isin=isin,
            secid=secid,
            primary_board=best.get("primary_boardid"),
            name=description.get("NAME") or best.get("name") or best.get("shortname"),
            currency=_currency(description.get("FACEUNIT")),
            bond_type=str(description.get("BOND_TYPE") or ""),
            coupon_frequency=_frequency(description.get("COUPONFREQUENCY")),
        )
        self._bonds[isin] = bond
        return bond

    # --- дюрация и выпуклость ------------------------------------------------------------

    async def _duration_convexity(
        self, client: httpx.AsyncClient, bond: _Bond, trading: _Trading
    ) -> tuple[Decimal | None, Decimal | None]:
        """D_mod и C по графику выплат; (None, None), если надёжно посчитать нельзя"""
        day = trading.trade_date.isoformat()
        yields = await self._table(
            client,
            f"history/engines/stock/markets/bonds/yields/{bond.secid}.json",
            "history_yields",
            {"from": day, "till": day, "history_yields.columns": YIELD_COLUMNS},
        )
        row = _pick_yield(
            yields,
            trading.trade_date,
            prefer=(
                trading.face_row.get("BOARDID"),
                trading.price_row.get("BOARDID"),
                bond.primary_board,
            ),
        )
        if row is None:
            return None, None
        yield_pct = _decimal(row, "EFFECTIVEYIELD")
        iss_duration = _decimal(row, "DURATION")
        horizon = _date(row, "YIELDDATE")
        settle = _next_business_day(trading.trade_date)
        flows = await self._cash_flows(client, bond, settle, horizon)
        if flows is None or yield_pct is None or iss_duration is None:
            return None, None
        # грязная цена, по которой биржа считала доходность: PRICE % номинала + НКД на дату
        # расчётов; по ней выбирается база года (365 или 360), см. bond_math.py
        price, accrued, nominal = (
            _decimal(row, "PRICE"),
            _decimal(row, "ACCINT"),
            _decimal(trading.face_row, "FACEVALUE"),
        )
        dirty = (
            price / 100 * nominal + accrued
            if price is not None and accrued is not None and nominal is not None
            else None
        )
        risk = bond_risk_matching(flows, settle, yield_pct, iss_duration, dirty)
        if risk is None:
            logger.info(
                "Мосбиржа %s: дюрация по графику не совпала с ISS %s дн.", bond.secid, iss_duration
            )
            return None, None
        return risk.duration_mod, risk.convexity

    async def _cash_flows(
        self, client: httpx.AsyncClient, bond: _Bond, settle: date, horizon: date | None
    ) -> list[CashFlow] | None:
        """Купоны и погашения номинала после даты расчётов до даты, к которой считала биржа

        Если биржа считала к оферте (YIELDDATE раньше погашения), непогашенный к ней номинал
        выплачивается в дату оферты. None: у будущего купона нет суммы или нет погашений
        """
        blocks = await self._pages(
            client,
            f"statistics/engines/stock/markets/bonds/bondization/{bond.secid}.json",
            ("coupons", "amortizations"),
            {
                "limit": str(PAGE_LIMIT),
                "coupons.columns": "coupondate,value,faceunit",
                "amortizations.columns": "amortdate,value,faceunit",
            },
        )
        flows: list[CashFlow] = []
        outstanding_after_horizon = Decimal(0)
        for block, date_field in (("coupons", "coupondate"), ("amortizations", "amortdate")):
            for row in blocks[block]:
                on = _date(row, date_field)
                if on is None or on <= settle:  # выплачено до даты расчётов
                    continue
                amount = _decimal(row, "value")
                if row.get("faceunit") and _currency(row.get("faceunit")) != bond.currency:
                    _incompatible(f"{bond.isin}: график выплат не в валюте номинала")
                if horizon is not None and on > horizon:
                    if block == "amortizations" and amount is not None:
                        outstanding_after_horizon += amount
                    continue
                if amount is None:  # купон ещё не определён (переменный или неизвестный)
                    return None
                flows.append(CashFlow(on, amount))
        if not blocks["amortizations"]:
            return None
        if horizon is not None and outstanding_after_horizon > 0:
            flows.append(CashFlow(horizon, outstanding_after_horizon))
        return flows or None

    # --- HTTP и формат ISS ---------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=TIMEOUT_SECONDS,
            transport=self._transport,
            headers={"User-Agent": USER_AGENT},
        )

    async def _table(
        self, client: httpx.AsyncClient, path: str, block: str, params: dict[str, str]
    ) -> list[Row]:
        return (await self._pages(client, path, (block,), params))[block]

    async def _pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        blocks: Sequence[str],
        params: dict[str, str],
    ) -> dict[str, list[Row]]:
        """Все строки блоков со всех страниц; страница по cursor блока (INDEX, TOTAL, PAGESIZE)"""
        only = ",".join(name for block in blocks for name in (block, f"{block}.cursor"))
        rows: dict[str, list[Row]] = {block: [] for block in blocks}
        pending = set(blocks)
        start = 0
        for _ in range(MAX_PAGES):
            data = await self._get(
                client, path, {**params, "iss.meta": "off", "iss.only": only, "start": str(start)}
            )
            next_start = None
            for block in blocks:
                if block not in pending:
                    continue
                rows[block].extend(_block_rows(data, block, path))
                cursor = _cursor(data, block)
                if cursor is not None and cursor[0] + cursor[2] < cursor[1]:
                    next_start = max(next_start or 0, cursor[0] + cursor[2])
                else:
                    pending.discard(block)
            if next_start is None:
                return rows
            start = next_start
        _unavailable(f"Мосбиржа {path}: больше {MAX_PAGES} страниц")

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict[str, str]) -> Any:
        """GET к ISS; любой сбой → PROVIDER_UNAVAILABLE"""
        try:
            response = await client.get(path, params=params)
        except httpx.HTTPError as exc:  # таймаут, сеть, TLS
            logger.warning("Мосбиржа %s: %s", path, type(exc).__name__)
            _unavailable(f"Мосбиржа {path}: {type(exc).__name__}")
        if response.status_code != 200:
            logger.warning("Мосбиржа %s: HTTP %s", path, response.status_code)
            _unavailable(f"Мосбиржа {path}: HTTP {response.status_code}")
        try:
            # числа сразу в Decimal: float потерял бы знаки цены и доходности
            return response.json(parse_float=Decimal)
        except ValueError:
            _unavailable(f"Мосбиржа {path}: ответ не JSON")


# --- выбор строк -------------------------------------------------------------------------


def _pick_trading(rows: list[Row], bond: _Bond, as_of: date) -> _Trading:
    """Самая свежая дата торгов, где есть цена и режим в валюте номинала"""
    by_date: dict[date, list[Row]] = {}
    for row in rows:
        if row.get("SECID") not in (None, bond.secid):
            _incompatible(f"Мосбиржа вернула итоги торгов {row.get('SECID')} вместо {bond.secid}")
        trade_date = _date(row, "TRADEDATE")
        if trade_date is None:
            continue
        if trade_date > as_of:
            _incompatible(f"{bond.isin}: итоги торгов {trade_date} позже {as_of}")
        by_date.setdefault(trade_date, []).append(row)

    priced_without_face_board = False
    for trade_date in sorted(by_date, reverse=True):
        day = sorted(by_date[trade_date], key=lambda r: r.get("BOARDID") != bond.primary_board)
        face = [r for r in day if _currency_or_none(r.get("CURRENCYID")) == bond.currency]
        priced = [r for r in face + day if _has_price(r)]
        if not priced:
            continue
        if not face:
            priced_without_face_board = True
            continue
        primary = next((r for r in day if r.get("BOARDID") == bond.primary_board), face[0])
        return _Trading(
            trade_date=trade_date,
            face_row=face[0],
            price_row=priced[0],
            settlement_currency=_currency_or_none(primary.get("CURRENCYID")),
        )
    since = as_of - timedelta(days=HISTORY_DAYS)
    if priced_without_face_board:
        _incompatible(
            f"{bond.isin}: нет режима торгов в валюте номинала {bond.currency},"
            " НКД есть только в валюте расчётов"
        )
    raise MarketDataError(
        RowErrorCode.MISSING_MARKET_DATA,
        f"{bond.isin}: нет итогов торгов с ценой с {since} по {as_of}",
    )


def _pick_yield(rows: list[Row], trade_date: date, prefer: Sequence[str | None]) -> Row | None:
    """Строка доходности за дату торгов с доходностью и дюрацией; режимы по предпочтению"""
    usable = [
        r
        for r in rows
        if _date(r, "TRADEDATE") == trade_date
        and r.get("EFFECTIVEYIELD") is not None
        and r.get("DURATION") is not None
    ]
    for board in prefer:
        for row in usable:
            if board is not None and row.get("BOARDID") == board:
                return row
    return usable[0] if usable else None


def _has_price(row: Row) -> bool:
    return row.get("LEGALCLOSEPRICE") is not None or row.get("CLOSE") is not None


def _coupon_type(bond_type: str) -> CouponType:
    text = bond_type.lower()
    if "плавающ" in text or "флоатер" in text:
        return CouponType.FLOATING
    if "дисконтн" in text:
        return CouponType.ZERO
    return CouponType.FIXED


def _next_business_day(day: date) -> date:
    """Дата расчётов T+1: следующий будний день (праздники не учитываются)"""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


# --- разбор значений ---------------------------------------------------------------------


def _block_rows(data: Any, block: str, path: str) -> list[Row]:
    table = data.get(block) if isinstance(data, dict) else None
    columns = table.get("columns") if isinstance(table, dict) else None
    values = table.get("data") if isinstance(table, dict) else None
    if not isinstance(columns, list) or not isinstance(values, list):
        _unavailable(f"Мосбиржа {path}: в ответе нет блока {block}")
    rows = []
    for raw in values:
        if not isinstance(raw, list) or len(raw) != len(columns):
            _unavailable(f"Мосбиржа {path}: строка блока {block} не совпадает с columns")
        rows.append(dict(zip(columns, raw, strict=True)))
    return rows


def _cursor(data: Any, block: str) -> tuple[int, int, int] | None:
    """(INDEX, TOTAL, PAGESIZE) или None, если блок не постраничный"""
    table = data.get(f"{block}.cursor") if isinstance(data, dict) else None
    if not isinstance(table, dict) or not table.get("data"):
        return None
    try:
        row = dict(zip(table["columns"], table["data"][0], strict=True))
        index, total, size = int(row["INDEX"]), int(row["TOTAL"]), int(row["PAGESIZE"])
    except (KeyError, TypeError, ValueError):
        _unavailable(f"Мосбиржа: блок {block}.cursor не того формата")
    return (index, total, size) if size > 0 else None


def _currency(value: Any) -> str:
    raw = str(value or "").strip().upper()
    code = CURRENCY_ALIASES.get(raw, raw)
    if not ISO_CURRENCY.match(code):
        _incompatible(f"валюта Мосбиржи не ISO-код: {value!r}")
    return code


def _currency_or_none(value: Any) -> str | None:
    """Валюта режима торгов; пусто или не ISO → None (такой режим просто не подходит)"""
    raw = str(value or "").strip().upper()
    code = CURRENCY_ALIASES.get(raw, raw)
    return code if ISO_CURRENCY.match(code) else None


def _decimal(row: Row, field: str) -> Decimal | None:
    """Число из ответа как Decimal; None или пустая строка → None (не 0)"""
    value = row.get(field)
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))  # int, Decimal или строка
    except InvalidOperation:
        _incompatible(f"поле {field} не число: {value!r}")
    if not number.is_finite():
        _incompatible(f"поле {field} не число: {value!r}")
    return number


def _date(row: Row, field: str) -> date | None:
    value = row.get(field)
    if value in (None, "", "0000-00-00"):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        _incompatible(f"поле {field} не дата: {value!r}")


def _frequency(value: Any) -> int | None:
    """Выплат купона в год; 0 или пусто: частоты нет"""
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        _incompatible(f"частота купона не число: {value!r}")
    if number <= 0:
        return None
    if number != number.to_integral_value():
        _incompatible(f"дробная частота купона: {value!r}")
    return int(number)


def _unavailable(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, message)


def _incompatible(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.INCOMPATIBLE_MARKET_DATA, message)
