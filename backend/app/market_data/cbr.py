"""Официальные курсы ЦБ РФ: ежедневный XML → FxRate (рублей за одну единицу валюты)

Запрос: GET https://www.cbr.ru/scripts/XML_daily.asp?date_req=ДД/ММ/ГГГГ, без ключа

Ответ (сверен с живыми ответами 15.11.2026, записи в tests/fixtures/cbr):
* XML в windows-1251: <ValCurs Date="14.11.2026"> и в нём <Valute> с полями
  CharCode (ISO 4217), Nominal (за сколько единиц курс), Value (рублей за Nominal единиц)
* десятичная запятая: "82,1516"
* часть курсов за 10, 100, ..., 1 000 000 единиц (KZT за 100, IRR за 1 000 000):
  FxRate.per_unit делит Value на Nominal без округления. Готовое поле VunitRate не берём:
  в нём бывает экспоненциальная запись (4,31322E-05)
* ЦБ отдаёт курс, действующий на запрошенную дату: если на неё курс не устанавливался
  (выходной, праздник, дата в будущем), приходит последний установленный. Поэтому дата
  курса rate_date берётся из ValCurs/@Date, а не из запроса (на 15.11.2026 пришёл курс от 14.11)

Ошибки (MarketDataError):
* MISSING_FX_RATE: ЦБ ответил, но этой валюты в списке на дату нет (евро в 1998 году)
* FX_PROVIDER_UNAVAILABLE: сеть, таймаут, HTTP-ошибка, ответ не XML или без ValCurs/@Date
  (на неверный запрос ЦБ отвечает HTTP 200 и текстом «Error in parameters»)
* INCOMPATIBLE_MARKET_DATA: код валюты не ISO, Value или Nominal не число или ≤ 0,
  дата курса позже запрошенной

RUB в ЦБ не запрашивается: курс 1 на дату запроса (backend RUB и так не спрашивает)
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from typing import NoReturn

import httpx

from app.contracts import FxRate, MarketDataError
from app.enums import FxSource, RowErrorCode

logger = logging.getLogger(__name__)

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
TIMEOUT_SECONDS = 10.0
USER_AGENT = "risk-metrics/0.4 (+https://github.com/Kopylovvv/risk-metrics)"
BASE_CURRENCY = "RUB"
ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")
# Value: цифры и десятичная запятая; Nominal: целое
CBR_NUMBER = re.compile(r"^\d+(,\d+)?$")
CBR_INTEGER = re.compile(r"^\d+$")


class CbrFxRates:
    """Реализует contracts.FxRateProvider поверх ежедневных курсов ЦБ РФ"""

    source = FxSource.CBR

    def __init__(
        self,
        url: str = CBR_DAILY_URL,
        transport: httpx.AsyncBaseTransport | None = None,  # подмена сети в тестах
    ):
        self._url = url
        self._transport = transport
        # один ответ ЦБ содержит все валюты на дату: при расчёте с USD и EUR запрос один
        # объект живёт один HTTP-запрос к backend (создаётся в deps.py), курс не устаревает
        self._daily: dict[date, tuple[date, dict[str, tuple[str, str]]]] = {}

    async def get_rate_to_rub(self, currency: str, on: date) -> FxRate:
        code = currency.strip().upper()
        if not ISO_CURRENCY.match(code):
            _incompatible(f"код валюты не ISO 4217: {currency!r}")
        if code == BASE_CURRENCY:
            return FxRate(code, on, Decimal(1), self.source)
        rate_date, rates = await self._rates_on(on)
        if code not in rates:
            raise MarketDataError(
                RowErrorCode.MISSING_FX_RATE, f"ЦБ не устанавливал курс {code} на {rate_date}"
            )
        raw_value, raw_nominal = rates[code]
        if not CBR_NUMBER.match(raw_value) or not CBR_INTEGER.match(raw_nominal):
            _incompatible(f"{code}: курс ЦБ {raw_value!r} за {raw_nominal!r} не число")
        value = Decimal(raw_value.replace(",", "."))
        # per_unit: рублей за одну единицу; ≤ 0 → INCOMPATIBLE_MARKET_DATA
        return FxRate.per_unit(code, rate_date, value, int(raw_nominal), self.source)

    async def _rates_on(self, on: date) -> tuple[date, dict[str, tuple[str, str]]]:
        """Дата курса из ответа и сырые (Value, Nominal) по коду валюты"""
        if on not in self._daily:
            content = await self._get(on)
            rate_date, rates = parse_daily(content)
            if rate_date > on:
                _incompatible(f"ЦБ вернул курс на {rate_date}, позже запрошенной даты {on}")
            self._daily[on] = (rate_date, rates)
        return self._daily[on]

    async def _get(self, on: date) -> bytes:
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(self._url, params={"date_req": f"{on:%d/%m/%Y}"})
        except httpx.HTTPError as exc:  # таймаут, сеть, TLS
            logger.warning("ЦБ XML_daily: %s", type(exc).__name__)
            _unavailable(f"ЦБ: {type(exc).__name__}")
        if response.status_code != 200:
            logger.warning("ЦБ XML_daily: HTTP %s", response.status_code)
            _unavailable(f"ЦБ: HTTP {response.status_code}")
        return response.content


def parse_daily(content: bytes) -> tuple[date, dict[str, tuple[str, str]]]:
    """Ответ XML_daily.asp → (дата курса, {код: (Value, Nominal)}) как строки из XML

    Кодировку берём из объявления XML (windows-1251), поэтому разбираем байты, а не текст
    Числа здесь не разбираются: ошибка в чужой валюте не должна ломать нужную
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        _unavailable("ЦБ: ответ не XML")
    raw_date = root.get("Date") if root.tag == "ValCurs" else None
    if not raw_date:
        # так ЦБ отвечает на неверный запрос: <ValCurs>Error in parameters</ValCurs>
        _unavailable("ЦБ: в ответе нет даты курса")
    try:
        rate_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
    except ValueError:
        _unavailable(f"ЦБ: дата курса не в формате ДД.ММ.ГГГГ: {raw_date!r}")
    rates: dict[str, tuple[str, str]] = {}
    for valute in root.iter("Valute"):
        code = (valute.findtext("CharCode") or "").strip().upper()
        if code and code not in rates:
            value = (valute.findtext("Value") or "").strip()
            nominal = (valute.findtext("Nominal") or "").strip()
            rates[code] = (value, nominal)
    return rate_date, rates


def _unavailable(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.FX_PROVIDER_UNAVAILABLE, message)


def _incompatible(message: str) -> NoReturn:
    raise MarketDataError(RowErrorCode.INCOMPATIBLE_MARKET_DATA, message)
