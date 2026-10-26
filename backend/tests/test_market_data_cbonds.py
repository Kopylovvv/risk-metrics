"""Адаптер Cbonds без сети: подставные ответы web-сервиса через httpx.MockTransport

Ответы собраны по спецификации Cbonds (openapi.yaml: get_emissions, get_tradings_new),
это не записи реальных ответов: реальный запрос проверяется python -m app.market_data.check
"""

import json
import re
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.contracts import MarketDataError
from app.enums import CouponType, Provider, RowErrorCode
from app.market_data import CbondsMarketData

ISIN = "RU000A1038V6"
DAY = date(2026, 10, 20)
URL = "https://cbonds.test/services/json/"

EMISSION = {
    "id": 101,
    "isin_code": ISIN,
    "document_rus": "Облигация, 1-й выпуск",
    "currency_name": "RUB",
    "nominal_price": 1000,
    "outstanding_nominal_price": 1000,
    "bond_type": 0,
    "floating_rate": 0,
    "cupon_period": 2,
}
TRADING = {
    "id": 501,
    "date": "2026-10-20",
    "trading_ground_id": 1,
    "isin_code": ISIN,
    "indicative_price": Decimal("95.1234567890123456"),
    "aci": 12.5,
    "dur_mod": 3.0123456789,
    "convexity": 12.25,
}


def dumps(body: dict) -> str:
    """JSON, где Decimal записан числом со всеми знаками, как его отдал бы сервер"""
    text = json.dumps(body, ensure_ascii=False, default=lambda d: f"@@{d}@@")
    return re.sub(r'"@@(.*?)@@"', r"\1", text)


class FakeCbonds:
    """Отвечает по методу из адреса и запоминает тела запросов"""

    def __init__(
        self, emissions=None, tradings=None, acis=(), status=200, raw=None, error=None, down=()
    ):
        self.emissions = [EMISSION] if emissions is None else emissions
        self.tradings = [TRADING] if tradings is None else tradings
        self.acis = list(acis)
        self.status, self.raw, self.error = status, raw, error
        self.down = set(down)  # методы, которые отвечают 500
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        self.requests.append((method, json.loads(request.content)))
        if self.error:
            raise self.error
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        if method in self.down:
            return httpx.Response(500, content=b"Server error")
        items = {"get_emissions": self.emissions, "get_aci": self.acis}.get(method, self.tradings)
        body = {"count": len(items), "total": len(items), "items": items}
        return httpx.Response(self.status, content=dumps(body))

    def source(self) -> CbondsMarketData:
        return CbondsMarketData("user", "secret-password", URL, httpx.MockTransport(self.handler))


async def expect_error(call, code: RowErrorCode) -> MarketDataError:
    with pytest.raises(MarketDataError) as exc:
        await call
    assert exc.value.code == code
    return exc.value


# --- нормализация -------------------------------------------------------------------


async def test_snapshot_is_normalized():
    snapshot = await FakeCbonds().source().get_snapshot(ISIN, DAY)
    assert snapshot.isin == ISIN
    assert snapshot.provider == Provider.CBONDS
    assert snapshot.data_as_of == DAY
    assert snapshot.currency == "RUB"
    assert snapshot.nominal == Decimal(1000)
    assert snapshot.accrued_interest == Decimal("12.5")
    assert snapshot.duration_mod == Decimal("3.0123456789")
    assert snapshot.convexity == Decimal("12.25")
    assert snapshot.coupon_type == CouponType.FIXED
    assert snapshot.coupon_frequency == 2


async def test_numbers_keep_all_digits():
    # float оставил бы 95.12345678901235
    snapshot = await FakeCbonds().source().get_snapshot(ISIN, DAY)
    assert snapshot.price_pct == Decimal("95.1234567890123456")


async def test_numbers_as_strings():
    trading = dict(TRADING, indicative_price="99.5", aci="0", dur_mod="2.5", convexity="7")
    snapshot = await FakeCbonds(tradings=[trading]).source().get_snapshot(ISIN, DAY)
    assert (snapshot.price_pct, snapshot.accrued_interest) == (Decimal("99.5"), Decimal(0))


async def test_request_format():
    fake = FakeCbonds()
    await fake.source().get_snapshot(ISIN, DAY)
    (m1, emissions), (m2, tradings) = fake.requests
    assert (m1, m2) == ("get_emissions", "get_tradings_new")
    assert emissions["auth"] == {"login": "user", "password": "secret-password"}
    assert emissions["filters"] == [{"field": "isin_code", "operator": "eq", "value": ISIN}]
    assert {"field": "date", "operator": "le", "value": "2026-10-20"} in tradings["filters"]
    assert tradings["sorting"] == [{"field": "date", "order": "desc"}]


async def test_floating_coupon():
    emission = dict(EMISSION, floating_rate=1, cupon_period=4)
    trading = dict(TRADING, dur_mod=None, convexity=None)
    snapshot = await FakeCbonds([emission], [trading]).source().get_snapshot(ISIN, DAY)
    assert snapshot.coupon_type == CouponType.FLOATING
    assert snapshot.coupon_frequency == 4
    # нет дюрации у источника → None, а не 0; для floating расчёт её и не требует
    assert snapshot.duration_mod is None and snapshot.convexity is None


@pytest.mark.parametrize("period", [None, 0, ""])
async def test_irregular_coupon_has_no_frequency(period):
    emission = dict(EMISSION, cupon_period=period)
    snapshot = await FakeCbonds([emission]).source().get_snapshot(ISIN, DAY)
    assert snapshot.coupon_frequency is None


async def test_amortizing_bond_uses_outstanding_nominal():
    emission = dict(EMISSION, bond_type=1, nominal_price=1000, outstanding_nominal_price=600)
    snapshot = await FakeCbonds([emission]).source().get_snapshot(ISIN, DAY)
    assert snapshot.nominal == Decimal(600)


async def test_missing_values_stay_none():
    trading = dict(TRADING, aci=None, dur_mod=None, convexity="")
    snapshot = await FakeCbonds(tradings=[trading]).source().get_snapshot(ISIN, DAY)
    assert snapshot.accrued_interest is None
    assert snapshot.duration_mod is None and snapshot.convexity is None


async def test_accrued_interest_is_not_requested_twice():
    fake = FakeCbonds()
    await fake.source().get_snapshot(ISIN, DAY)
    assert [m for m, _ in fake.requests] == ["get_emissions", "get_tradings_new"]


async def test_accrued_interest_from_get_aci():
    # внебиржевые котировки евробондов приходят без aci: берём НКД на ту же дату
    trading = dict(TRADING, date="2026-10-19", aci=None)
    aci = {"emission_isin": ISIN, "anchor_date": "2026-10-19", "aci": "31.4567"}
    fake = FakeCbonds(tradings=[trading], acis=[aci])
    snapshot = await fake.source().get_snapshot(ISIN, DAY)
    assert snapshot.accrued_interest == Decimal("31.4567")
    method, body = fake.requests[-1]
    assert method == "get_aci"
    assert body["filters"] == [
        {"field": "emission_isin", "operator": "eq", "value": ISIN},
        {"field": "anchor_date", "operator": "eq", "value": "2026-10-19"},
    ]


@pytest.mark.parametrize(
    "fake",
    [
        FakeCbonds(tradings=[dict(TRADING, aci=None)]),  # get_aci пустой
        FakeCbonds(tradings=[dict(TRADING, aci=None)], down={"get_aci"}),  # get_aci упал
    ],
    ids=["empty", "down"],
)
async def test_no_accrued_interest_anywhere(fake):
    # снимок с ценой и дюрацией сохраняется, НКД None: расчёт поставит MISSING_MARKET_DATA
    snapshot = await fake.source().get_snapshot(ISIN, DAY)
    assert snapshot.accrued_interest is None
    assert snapshot.price_pct is not None


async def test_accrued_interest_of_other_isin():
    trading = dict(TRADING, aci=None)
    acis = [{"emission_isin": "RU000A105A95", "aci": "1"}]
    source = FakeCbonds(tradings=[trading], acis=acis).source()
    await expect_error(source.get_snapshot(ISIN, DAY), RowErrorCode.INCOMPATIBLE_MARKET_DATA)


async def test_latest_row_with_price_is_used():
    rows = [
        dict(TRADING, date="2026-10-20", indicative_price=None),  # торгов с ценой не было
        dict(TRADING, date="2026-10-17", indicative_price=97),
        dict(TRADING, date="2026-10-16", indicative_price=96),
    ]
    snapshot = await FakeCbonds(tradings=rows).source().get_snapshot(ISIN, DAY)
    assert (snapshot.data_as_of, snapshot.price_pct) == (date(2026, 10, 17), Decimal(97))


async def test_old_currency_code():
    emission = dict(EMISSION, currency_name="rur")
    assert (await FakeCbonds([emission]).source().get_instrument(ISIN)).currency == "RUB"


async def test_instrument_card():
    info = await FakeCbonds().source().get_instrument(ISIN)
    assert (info.isin, info.name, info.instrument_type, info.currency) == (
        ISIN,
        "Облигация, 1-й выпуск",
        "bond",
        "RUB",
    )


# --- данных нет или они несовместимы ---------------------------------------------------


async def test_unknown_isin():
    source = FakeCbonds(emissions=[]).source()
    await expect_error(source.get_instrument(ISIN), RowErrorCode.INSTRUMENT_NOT_FOUND)
    await expect_error(source.get_snapshot(ISIN, DAY), RowErrorCode.INSTRUMENT_NOT_FOUND)


@pytest.mark.parametrize("rows", [[], [dict(TRADING, indicative_price=None)]])
async def test_no_quotes(rows):
    source = FakeCbonds(tradings=rows).source()
    await expect_error(source.get_snapshot(ISIN, DAY), RowErrorCode.MISSING_MARKET_DATA)


@pytest.mark.parametrize(
    ("emission", "trading"),
    [
        (dict(EMISSION, isin_code="RU000A105A95"), TRADING),  # другой ISIN в параметрах
        (EMISSION, dict(TRADING, isin_code="RU000A105A95")),  # другой ISIN в торгах
        (EMISSION, dict(TRADING, date="2026-10-21")),  # данные позже даты оценки
        (dict(EMISSION, currency_name="рубль"), TRADING),  # не ISO-код
        (dict(EMISSION, currency_name=None), TRADING),
        (dict(EMISSION, nominal_price=0), TRADING),  # в БД CHECK nominal > 0
        (EMISSION, dict(TRADING, indicative_price=0)),  # в БД CHECK price_pct > 0
        (EMISSION, dict(TRADING, aci="n/a")),
        (EMISSION, dict(TRADING, dur_mod="NaN")),
        (dict(EMISSION, cupon_period=2.5), TRADING),
    ],
)
async def test_incompatible_data(emission, trading):
    source = FakeCbonds([emission], [trading]).source()
    await expect_error(source.get_snapshot(ISIN, DAY), RowErrorCode.INCOMPATIBLE_MARKET_DATA)


# --- источник недоступен ------------------------------------------------------------------


@pytest.mark.parametrize(
    "fake",
    [
        FakeCbonds(error=httpx.ConnectTimeout("timeout")),
        FakeCbonds(error=httpx.ConnectError("no route")),
        FakeCbonds(status=401, raw=b'{"error": "Authentication failed"}'),
        FakeCbonds(status=403, raw=b'{"error": "Access denied"}'),
        FakeCbonds(status=500, raw=b"Server error"),
        FakeCbonds(raw=b"<html>maintenance</html>"),
        FakeCbonds(raw=b'{"count": 0}'),
        FakeCbonds(raw=b"[]"),
    ],
    ids=["timeout", "network", "401", "403", "500", "not-json", "no-items", "not-object"],
)
async def test_provider_unavailable(fake):
    error = await expect_error(
        fake.source().get_snapshot(ISIN, DAY), RowErrorCode.PROVIDER_UNAVAILABLE
    )
    assert "secret-password" not in str(error)


# --- подключение -----------------------------------------------------------------------------


def test_adapter_is_used_when_credentials_are_set(monkeypatch):
    from app.api.deps import get_market_data

    monkeypatch.setenv("CBONDS_LOGIN", "user")
    monkeypatch.setenv("CBONDS_PASSWORD", "secret-password")
    assert isinstance(get_market_data(), CbondsMarketData)
