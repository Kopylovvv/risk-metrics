"""Адаптер web-сервиса Cbonds (JSON): параметры выпуска и итоги торгов → MarketSnapshot

Методы (POST <адрес>/<метод>/, в теле auth, filters, sorting, quantity):
* get_emissions: валюта, номинал, плавающая ставка, выплат купона в год
* get_tradings_new: индикативная цена, НКД, модифицированная дюрация, выпуклость на дату
* get_aci: НКД на дату, если в итогах торгов его нет (у внебиржевых котировок евробондов)

Реальный ответ (демо-доступ, 26.10.2026): числа и флаги приходят строками, валюта
ISO-кодом, dur_mod 1.9–9.4, convexity 5.5–116 (порядка D², то есть в годах²)

Нормализация:
* price_pct = indicative_price, % от номинала
* nominal = nominal_price; у амортизируемых (bond_type = 1) непогашенный номинал
  outstanding_nominal_price. У еврооблигаций nominal_price и aci оба считаются
  от минимального лота, поэтому одна «бумага» = один лот, CFV остаётся согласованной
* accrued_interest = aci из итогов торгов, иначе aci из get_aci на ту же дату; в валюте номинала
* duration_mod = dur_mod, в годах (дюрация Маколея в годах / (1 + YTM), по документации)
* convexity = convexity как есть: в документации единиц нет, по реальным значениям годы²
  (подтвердить у эксперта)
* coupon_type: floating_rate = 1 → floating, иначе fixed (бескупонные считаются так же,
  как fixed: D и C от источника); coupon_frequency = cupon_period
* нет значения у источника → None, не 0: ошибку строки поставит расчёт

Ошибки (MarketDataError, код попадает в error_code строки):
* INSTRUMENT_NOT_FOUND: Cbonds не знает ISIN
* MISSING_MARKET_DATA: нет итогов торгов с ценой на дату оценки или раньше
* INCOMPATIBLE_MARKET_DATA: чужой ISIN, дата позже запрошенной, валюта не ISO-код,
  номинал или цена ≤ 0, не число
* PROVIDER_UNAVAILABLE: таймаут, сеть, HTTP-ошибка (в том числе 401/403: нет доступа),
  ответ не того формата. Логин и пароль в сообщения и логи не попадают
"""

import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

import httpx

from app.contracts import InstrumentInfo, MarketDataError, MarketSnapshot
from app.enums import CouponType, Provider, RowErrorCode

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
# итогов торгов за последние дни хватает, чтобы найти строку с ценой
TRADINGS_LIMIT = 20
ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")
# старое обозначение рубля
CURRENCY_ALIASES = {"RUR": "RUB"}


class CbondsMarketData:
    """Реализует contracts.MarketDataProvider поверх web-сервиса Cbonds"""

    def __init__(
        self,
        login: str,
        password: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,  # подмена сети в тестах
    ):
        self._auth = {"login": login, "password": password}
        self._base_url = base_url.rstrip("/") + "/"
        self._transport = transport
        # параметры выпуска нужны и get_instrument, и get_snapshot: один запрос на ISIN
        # объект живёт один HTTP-запрос к backend (создаётся в deps.py), данные не устаревают
        self._emissions: dict[str, dict[str, Any]] = {}

    async def get_instrument(self, isin: str) -> InstrumentInfo:
        emission = await self._emission(isin)
        name = emission.get("document_rus") or emission.get("document_eng")
        return InstrumentInfo(
            isin=isin, name=name, instrument_type="bond", currency=_currency(emission)
        )

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        emission = await self._emission(isin)
        rows = await self._post(
            "get_tradings_new",
            filters=[
                _eq("isin_code", isin),
                {"field": "date", "operator": "le", "value": str(as_of)},
            ],
            sorting=[{"field": "date", "order": "desc"}],
            limit=TRADINGS_LIMIT,
        )
        trading = _latest_with_price(rows, isin, as_of)
        nominal = _nominal(emission)
        price = _decimal(trading, "indicative_price")
        # в БД стоят CHECK nominal > 0 и price_pct > 0: отсекаем до записи
        if (nominal is not None and nominal <= 0) or (price is not None and price <= 0):
            _incompatible(f"{isin}: номинал или цена ≤ 0")
        data_as_of = date.fromisoformat(str(trading["date"])[:10])
        accrued = _decimal(trading, "aci")
        if accrued is None:
            accrued = await self._accrued_interest(isin, data_as_of)
        return MarketSnapshot(
            isin=isin,
            provider=Provider.CBONDS,
            data_as_of=data_as_of,
            retrieved_at=datetime.now(UTC),
            currency=_currency(emission),
            nominal=nominal,
            price_pct=price,
            accrued_interest=accrued,
            duration_mod=_decimal(trading, "dur_mod"),
            convexity=_decimal(trading, "convexity"),
            coupon_type=CouponType.FLOATING
            if _flag(emission, "floating_rate")
            else CouponType.FIXED,
            coupon_frequency=_frequency(emission),
        )

    async def _emission(self, isin: str) -> dict[str, Any]:
        if isin in self._emissions:
            return self._emissions[isin]
        rows = await self._post("get_emissions", filters=[_eq("isin_code", isin)], limit=2)
        if not rows:
            raise MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND, f"Cbonds не знает {isin}")
        if rows[0].get("isin_code") != isin:
            _incompatible(f"Cbonds вернул другой ISIN вместо {isin}")
        self._emissions[isin] = rows[0]
        return rows[0]

    async def _accrued_interest(self, isin: str, on: date) -> Decimal | None:
        """НКД из get_aci, если в итогах торгов его нет (внебиржевые котировки евробондов)

        Сбой этого запроса не роняет снимок: НКД останется None, расчёт поставит
        строке MISSING_MARKET_DATA, а цена и дюрация сохранятся
        """
        try:
            rows = await self._post(
                "get_aci",
                filters=[_eq("emission_isin", isin), _eq("anchor_date", str(on))],
                limit=1,
            )
        except MarketDataError:
            return None
        if not rows:
            return None
        if rows[0].get("emission_isin") not in (None, isin):
            _incompatible(f"Cbonds вернул НКД другого ISIN вместо {isin}")
        return _decimal(rows[0], "aci")

    async def _post(
        self,
        method: str,
        filters: list[dict[str, Any]],
        limit: int,
        sorting: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Запрос к методу; возвращает items. Любой сбой → PROVIDER_UNAVAILABLE"""
        body: dict[str, Any] = {
            "auth": self._auth,
            "filters": filters,
            "quantity": {"limit": limit, "offset": 0},
        }
        if sorting:
            body["sorting"] = sorting
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(f"{method}/", params={"lang": "rus"}, json=body)
        except httpx.HTTPError as exc:  # таймаут, сеть, TLS
            logger.warning("Cbonds %s: %s", method, type(exc).__name__)
            _unavailable(f"Cbonds {method}: {type(exc).__name__}")
        if response.status_code != 200:
            # 401/403: нет доступа или истёк; тело ответа не логируем, в нём может быть логин
            logger.warning("Cbonds %s: HTTP %s", method, response.status_code)
            _unavailable(f"Cbonds {method}: HTTP {response.status_code}")
        try:
            # числа сразу в Decimal: float потерял бы знаки цены и дюрации
            data = response.json(parse_float=Decimal)
        except ValueError:
            _unavailable(f"Cbonds {method}: ответ не JSON")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            _unavailable(f"Cbonds {method}: в ответе нет items")
        return items


def _latest_with_price(rows: list[dict[str, Any]], isin: str, as_of: date) -> dict[str, Any]:
    """Самая свежая строка итогов торгов с индикативной ценой (площадок может быть несколько)"""
    for row in sorted(rows, key=lambda r: str(r.get("date")), reverse=True):
        if row.get("isin_code") not in (None, isin):
            _incompatible(f"Cbonds вернул итоги торгов другого ISIN вместо {isin}")
        if not row.get("date"):
            continue
        if date.fromisoformat(str(row["date"])[:10]) > as_of:
            _incompatible(f"{isin}: итоги торгов позже {as_of}")
        if row.get("indicative_price") is not None:
            return row
    raise MarketDataError(
        RowErrorCode.MISSING_MARKET_DATA, f"{isin}: нет итогов торгов с ценой на {as_of} или раньше"
    )


def _nominal(emission: dict[str, Any]) -> Decimal | None:
    if _flag(emission, "bond_type"):
        outstanding = _decimal(emission, "outstanding_nominal_price")
        if outstanding is not None:
            return outstanding
    return _decimal(emission, "nominal_price")


def _currency(emission: dict[str, Any]) -> str:
    raw = str(emission.get("currency_name") or "").strip().upper()
    code = CURRENCY_ALIASES.get(raw, raw)
    if not ISO_CURRENCY.match(code):
        _incompatible(f"валюта Cbonds не ISO-код: {raw!r}")
    return code


def _decimal(row: dict[str, Any], field: str) -> Decimal | None:
    """Число из ответа как Decimal; None или пустая строка → None (не 0)"""
    value = row.get(field)
    if value is None or value == "":
        return None
    try:
        # str(): числа могут прийти строкой, int или Decimal
        number = Decimal(str(value))
    except InvalidOperation:
        _incompatible(f"поле {field} не число: {value!r}")
    if not number.is_finite():  # NaN, Infinity
        _incompatible(f"поле {field} не число: {value!r}")
    return number


def _frequency(emission: dict[str, Any]) -> int | None:
    """Выплат купона в год; не заполнено или 0: выплаты нерегулярные, частоты нет"""
    value = _decimal(emission, "cupon_period")
    if value is None or value <= 0:
        return None
    if value != value.to_integral_value():
        _incompatible(f"дробная частота купона: {value}")
    return int(value)


def _flag(row: dict[str, Any], field: str) -> bool:
    """Флаг да/нет: 1, "1", true"""
    return str(row.get(field)).strip().lower() in ("1", "true")


def _eq(field: str, value: str) -> dict[str, str]:
    return {"field": field, "operator": "eq", "value": value}


def _unavailable(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, message)


def _incompatible(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.INCOMPATIBLE_MARKET_DATA, message)
