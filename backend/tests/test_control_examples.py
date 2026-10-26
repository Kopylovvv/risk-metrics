"""Контрольный набор #31: от ответа Cbonds до сохранённого DV01 через API, без сети

Цепочка настоящая: адаптер CbondsMarketData → путь расчёта backend → расчётное ядро → БД.
Подменён только транспорт HTTP (ответы в формате web-сервиса Cbonds) и курс валюты.
Ожидаемые значения посчитаны вручную, арифметика в комментариях; входы и происхождение
описаны в docs/control-example.md
"""

import json
from decimal import Decimal

import httpx
import pytest

from app.api.deps import get_fx_rates, get_market_data
from app.market_data import CbondsMarketData
from tests.test_api_calculations import DAY, FakeFx, by_isin, calculate, workspace_with

FIXED = "RU000A1038V6"  # рублёвая, фиксированный купон
FLOATER = "RU000A105A95"  # рублёвый флоатер, 4 выплаты в год
EUROBOND = "XS0191754729"  # долларовый евробонд, номинал = мин. лот 2000, НКД из get_aci
NO_QUOTES = "RU000A0JX0J2"  # выпуск есть, торгов с ценой нет
UNKNOWN = "US0378331005"  # Cbonds не знает

EMISSIONS = {
    FIXED: {
        "currency_name": "RUB",
        "nominal_price": "1000",
        "floating_rate": "0",
        "cupon_period": "2",
    },
    FLOATER: {
        "currency_name": "RUB",
        "nominal_price": "1000",
        "floating_rate": "1",
        "cupon_period": "4",
    },
    EUROBOND: {
        "currency_name": "USD",
        "nominal_price": "2000",
        "floating_rate": "0",
        "cupon_period": "2",
    },
    NO_QUOTES: {
        "currency_name": "RUB",
        "nominal_price": "1000",
        "floating_rate": "0",
        "cupon_period": "2",
    },
}
TRADINGS = {
    FIXED: {"indicative_price": "101.2", "aci": "3.1", "dur_mod": "2", "convexity": "5"},
    FLOATER: {"indicative_price": "99.8", "aci": "7.4", "dur_mod": None, "convexity": None},
    # внебиржевая котировка евробонда: НКД в итогах торгов нет
    EUROBOND: {"indicative_price": "98.5", "aci": None, "dur_mod": "4.2", "convexity": "25"},
}
ACI = {EUROBOND: "12.75"}  # НКД на мин. лот


class CbondsServer:
    """Ответы web-сервиса Cbonds по ISIN из фильтра; down=True — сервис отвечает 500"""

    def __init__(self):
        self.down = False
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        isin = json.loads(request.content)["filters"][0]["value"]
        self.calls.append(f"{method}:{isin}")
        if self.down:
            return httpx.Response(500, content=b"Server error")
        if method == "get_emissions":
            items = [dict(EMISSIONS[isin], isin_code=isin)] if isin in EMISSIONS else []
        elif method == "get_tradings_new":
            row = TRADINGS.get(isin)
            items = [dict(row, isin_code=isin, date=str(DAY), trading_ground_id="4")] if row else []
        else:  # get_aci
            items = (
                [{"emission_isin": isin, "anchor_date": str(DAY), "aci": ACI[isin]}]
                if isin in ACI
                else []
            )
        return httpx.Response(200, json={"count": len(items), "items": items})


@pytest.fixture
def cbonds():
    """Подключает настоящий адаптер с подставным транспортом и курс USD = 92.5"""
    from app.main import app

    server = CbondsServer()
    adapter = CbondsMarketData(
        "user",
        "password",
        "https://cbonds.test/services/json/",
        httpx.MockTransport(server.handler),
    )
    fx = FakeFx({"USD": "92.5"})
    app.dependency_overrides[get_market_data] = lambda: adapter
    app.dependency_overrides[get_fx_rates] = lambda: fx
    return server


async def test_control_set_from_cbonds_to_saved_result(api, cbonds):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (FLOATER, 50), (EUROBOND, -3))
    r = await calculate(api, ws_id, h, rev)
    assert r.status_code == 201, r.text
    calc = r.json()
    assert calc["status"] == "success"
    rows = by_isin(calc)

    # FIXED: CFV = 100 × (1000 × 101.2 / 100 + 3.1) = 100 × 1015.1 = 101 510
    # множитель = −2 × 0.0001 + ½ × 5 × 0.0001² = −0.000199975
    # DV01 = 101 510 × −0.000199975 = −20.29946225
    assert Decimal(rows[FIXED]["current_fair_value"]) == Decimal("101510")
    assert Decimal(rows[FIXED]["metric_value"]) == Decimal("-20.29946225")
    assert rows[FIXED]["provider"] == "cbonds" and rows[FIXED]["data_as_of"] == str(DAY)

    # FLOATER: D = 1/4, C = 0 (у источника дюрации нет — и не нужна)
    # CFV = 50 × (998 + 7.4) = 50 270; DV01 = 50 270 × −0.000025 = −1.25675
    assert Decimal(rows[FLOATER]["duration_used"]) == Decimal("0.25")
    assert Decimal(rows[FLOATER]["metric_value"]) == Decimal("-1.25675")

    # EUROBOND, шорт 3 лота: НКД 12.75 на лот взят из get_aci
    # CFV = −3 × (2000 × 0.985 + 12.75) × 92.5 = −3 × 1982.75 × 92.5 = −550 213.125
    # множитель = −4.2 × 0.0001 + ½ × 25 × 0.0001² = −0.000419875
    # DV01 = −550 213.125 × −0.000419875 = 231.020735859375
    assert Decimal(rows[EUROBOND]["accrued_interest_used"]) == Decimal("12.75")
    assert Decimal(rows[EUROBOND]["current_fair_value"]) == Decimal("-550213.125")
    assert Decimal(rows[EUROBOND]["metric_value"]) == Decimal("231.020735859375")

    # итог = −20.29946225 − 1.25675 + 231.020735859375 = 209.464523609375
    assert Decimal(calc["total_value"]) == Decimal("209.464523609375")


async def test_errors_are_not_replaced_by_zero(api, cbonds):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (NO_QUOTES, 10), (UNKNOWN, 5))
    calc = (await calculate(api, ws_id, h, rev)).json()
    rows = by_isin(calc)
    assert calc["status"] == "partial" and calc["total_value"] is None
    assert rows[FIXED]["status"] == "success"
    assert (rows[NO_QUOTES]["error_code"], rows[NO_QUOTES]["metric_value"]) == (
        "MISSING_MARKET_DATA",
        None,
    )
    assert (rows[UNKNOWN]["error_code"], rows[UNKNOWN]["metric_value"]) == (
        "INSTRUMENT_NOT_FOUND",
        None,
    )


async def test_source_down_is_not_a_financial_error(api, cbonds):
    # недоступность источника отличается от финансовой ошибки: другой код, итога нет
    cbonds.down = True
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "failed" and calc["total_value"] is None
    assert by_isin(calc)[FIXED]["error_code"] == "PROVIDER_UNAVAILABLE"


async def test_saved_data_is_reused_without_source(api, cbonds):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    first = (await calculate(api, ws_id, h, rev)).json()
    calls = len(cbonds.calls)
    cbonds.down = True  # второй расчёт на ту же дату не ходит в источник
    second = (await calculate(api, ws_id, h, rev)).json()
    assert len(cbonds.calls) == calls
    assert (
        Decimal(second["total_value"]) == Decimal(first["total_value"]) == Decimal("-20.29946225")
    )
