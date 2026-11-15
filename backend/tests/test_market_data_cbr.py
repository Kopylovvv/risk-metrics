"""Курсы ЦБ РФ без сети: записанные ответы XML_daily.asp через httpx.MockTransport

Фикстуры: tests/fixtures/cbr (реальные ответы от 15.11.2026, описание в README там же)
Ожидаемые курсы выписаны из ответа ЦБ и поделены на Nominal вручную
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.contracts import MarketDataError
from app.enums import FxSource, RowErrorCode
from app.market_data import CbrFxRates

FIXTURES = Path(__file__).parent / "fixtures" / "cbr"
FRIDAY = date(2026, 11, 13)
SUNDAY = date(2026, 11, 15)

# date_req → файл: ЦБ на выходной и на будущую дату отдаёт последний установленный курс
RESPONSES = {
    "13/11/2026": "daily_2026-11-13.xml",
    "15/11/2026": "daily_2026-11-14.xml",
    "16/11/2026": "daily_2026-11-14.xml",
    "05/01/1998": "daily_1998-01-01.xml",
}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeCbr:
    """Отвечает записанным XML по date_req и запоминает запросы"""

    def __init__(self, responses=None, status=200, raw=None, error=None):
        self.responses = RESPONSES if responses is None else responses
        self.status, self.raw, self.error = status, raw, error
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        date_req = request.url.params.get("date_req", "")
        self.requests.append(date_req)
        if self.error:
            raise self.error
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        if date_req not in self.responses:
            return httpx.Response(200, content=fixture("error_in_parameters.xml"))
        return httpx.Response(
            self.status,
            content=fixture(self.responses[date_req]),
            headers={"content-type": "application/xml; charset=windows-1251"},
        )

    def source(self) -> CbrFxRates:
        transport = httpx.MockTransport(self.handler)
        return CbrFxRates("https://cbr.test/scripts/XML_daily.asp", transport)


async def rate(currency: str, on: date, fake: FakeCbr | None = None):
    return await (fake or FakeCbr()).source().get_rate_to_rub(currency, on)


async def error_code(currency: str, on: date, fake: FakeCbr) -> RowErrorCode:
    with pytest.raises(MarketDataError) as exc:
        await rate(currency, on, fake)
    return exc.value.code


# --- нормальный ответ --------------------------------------------------------------------


async def test_usd_rate_on_trading_day():
    fake = FakeCbr()
    usd = await rate("USD", FRIDAY, fake)
    assert (usd.currency, usd.rate_date, usd.rate_to_rub) == ("USD", FRIDAY, Decimal("82.9803"))
    assert usd.source == FxSource.CBR == "cbr"
    assert fake.requests == ["13/11/2026"]  # дата в формате ЦБ ДД/ММ/ГГГГ


async def test_rate_date_comes_from_response_not_request():
    """На воскресенье 15.11 ЦБ курс не устанавливал: в ответе курс от 14.11 и его дата"""
    usd = await rate("USD", SUNDAY)
    assert (usd.rate_date, usd.rate_to_rub) == (date(2026, 11, 14), Decimal("82.1516"))


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("EUR", Decimal("91.0322")),  # за 1 единицу
        ("KZT", Decimal("0.185185")),  # 18,5185 за 100
        ("JPY", Decimal("0.513897")),  # 51,3897 за 100
        ("VND", Decimal("0.00320442")),  # 32,0442 за 10 000
        ("IRR", Decimal("0.0000431322")),  # 43,1322 за 1 000 000 (VunitRate 4,31322E-05)
    ],
)
async def test_rate_is_per_one_unit(currency, expected):
    fx = await rate(currency, SUNDAY)
    assert fx.rate_to_rub == expected


async def test_one_request_per_date_for_several_currencies():
    fake = FakeCbr()
    source = fake.source()
    usd = await source.get_rate_to_rub("USD", SUNDAY)
    cny = await source.get_rate_to_rub("cny", SUNDAY)  # регистр кода не важен
    assert (usd.rate_to_rub, cny.currency, cny.rate_to_rub) == (
        Decimal("82.1516"),
        "CNY",
        Decimal("12.3149"),
    )
    assert fake.requests == ["15/11/2026"]


async def test_rub_is_one_without_request():
    fake = FakeCbr()
    rub = await rate("RUB", FRIDAY, fake)
    assert (rub.currency, rub.rate_date, rub.rate_to_rub) == ("RUB", FRIDAY, Decimal(1))
    assert fake.requests == []


# --- нет курса и ошибки источника ----------------------------------------------------------


async def test_currency_without_rate_is_missing_not_zero():
    """1998 год: евро ещё нет. Курс не подставляется, строка получит MISSING_FX_RATE"""
    on = date(1998, 1, 5)
    assert await error_code("EUR", on, FakeCbr()) == RowErrorCode.MISSING_FX_RATE
    assert (await rate("USD", on)).rate_to_rub == Decimal("5.96")


async def test_error_in_parameters_is_provider_unavailable():
    fake = FakeCbr(raw=fixture("error_in_parameters.xml"))
    assert await error_code("USD", FRIDAY, fake) == RowErrorCode.FX_PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "fake",
    [
        FakeCbr(status=500, raw=b"Server error"),
        FakeCbr(status=403, raw=b"Forbidden"),
        FakeCbr(raw=b"<html><body>maintenance</body></html"),  # не XML
        FakeCbr(raw=b'<?xml version="1.0"?><html><body>ok</body></html>'),  # XML, но не ValCurs
        FakeCbr(raw=b'<?xml version="1.0"?><ValCurs Date="2026-11-13"></ValCurs>'),
        FakeCbr(error=httpx.ConnectTimeout("timeout")),
        FakeCbr(error=httpx.ConnectError("dns")),
    ],
)
async def test_source_failures_are_fx_provider_unavailable(fake):
    assert await error_code("USD", FRIDAY, fake) == RowErrorCode.FX_PROVIDER_UNAVAILABLE


async def test_rate_dated_after_request_is_incompatible():
    fake = FakeCbr(responses={"12/11/2026": "daily_2026-11-13.xml"})
    code = await error_code("USD", date(2026, 11, 12), fake)
    assert code == RowErrorCode.INCOMPATIBLE_MARKET_DATA


def daily(*valutes: tuple[str, str, str]) -> bytes:
    """Ответ ЦБ на 13.11.2026 с заданными (CharCode, Nominal, Value)"""
    items = "".join(
        f"<Valute><CharCode>{c}</CharCode><Nominal>{n}</Nominal><Value>{v}</Value></Valute>"
        for c, n, v in valutes
    )
    xml = f'<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="13.11.2026">{items}'
    return (xml + "</ValCurs>").encode("cp1251")


@pytest.mark.parametrize(
    ("nominal", "value"),
    [("1", "0,0000"), ("0", "82,9803"), ("1", "н/д"), ("1", "-1,5"), ("1", "82.9803"), ("", "1")],
)
async def test_bad_rate_is_incompatible(nominal, value):
    fake = FakeCbr(raw=daily(("USD", nominal, value)))
    assert await error_code("USD", FRIDAY, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA


async def test_bad_rate_of_other_currency_does_not_break_needed_one():
    fake = FakeCbr(raw=daily(("XXX", "1", "н/д"), ("USD", "1", "82,9803")))
    assert (await rate("USD", FRIDAY, fake)).rate_to_rub == Decimal("82.9803")


@pytest.mark.parametrize("currency", ["US", "US D", "840", ""])
async def test_non_iso_currency_is_incompatible(currency):
    fake = FakeCbr()
    assert await error_code(currency, FRIDAY, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA
    assert fake.requests == []
