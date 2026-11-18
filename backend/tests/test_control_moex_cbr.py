"""Контрольный набор S3-15 (#67) на реальных параметрах: API → адаптеры Мосбиржи и ЦБ

В отличие от test_integration_s3.py (учебные числа на уровне контракта) здесь работают
настоящие адаптеры MoexMarketData и CbrFxRates, а сеть подменена записанными ответами
ISS и ЦБ от 15.11.2026 (tests/fixtures). Запись прогона: docs/control-example.md

Ожидаемые значения посчитаны отдельно от проверяемых функций (методика dv01-v2):
D и C по графику выплат (bond_math, сверено с DURATION биржи), остальное по формуле:

ОФЗ 26238, +100 шт., итоги 13.11.2026, рубли:
  CFV = 100 × (1000 × 51.577 / 100 + 31.7) = 100 × 547.47 = 54 747 ₽
  D = 5.768382680528, C = 58.553339735139
  DV01 = 54 747 × (−5.768382680528 × 0.0001 + ½ × 58.553339735139 × 0.0001²) = −31.5641365626 ₽
ГазКЗ-34Д, −10 шт., номинал USD, итоги 13.11, курс ЦБ на 13.11 82.9803 ₽ за 1 $:
  CFV = −10 × (1000 × 96.2999 / 100 + 3.59) × 82.9803 = −10 × 966.589 × 82.9803 = −802 078.451967 ₽
  D = 5.176459212386, C = 36.691611768865 (база года 360, как у биржи)
  DV01 = −802 078.451967 × (−5.176459212386 × 0.0001 + ½ × 36.691611768865 × 0.0001²)
       = 415.0454914183 ₽
Итог: −31.5641365626 + 415.0454914183 = 383.4813548557 ₽
ОФЗ 29021 (флоатер, 4 выплаты в год), +100 шт.: CFV = 100 × (982.39 + 27.56) = 100 995 ₽,
  D = 1/4, C = 0: DV01 = 100 995 × (−0.25 × 0.0001) = −2.524875 ₽
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.api.deps import get_fx_rates, get_market_data, get_risk_calculator
from app.risk_core import calc_dv01
from app.stubs.sources import NoMarketDataSource
from tests.test_api_calculations import insert_instrument, insert_snapshot
from tests.test_api_trades import open_workspace
from tests.test_integration_s3 import post_trade, run_calc
from tests.test_market_data_cbr import FakeCbr
from tests.test_market_data_moex import FLOATER, OFZ, UNKNOWN, USD_BOND, FakeIss

FRIDAY = date(2026, 11, 13)
SUNDAY = date(2026, 11, 15)
PRECISION = Decimal("1e-9")  # D и C получены возведением в дробную степень, 28 знаков

QUANTITY = {OFZ: 100, USD_BOND: -10, FLOATER: 100}
EXPECTED_CFV = {
    OFZ: Decimal("54747"),
    USD_BOND: Decimal("-802078.451967"),
    FLOATER: Decimal("100995"),
}
EXPECTED_DV01 = {
    OFZ: Decimal("-31.5641365626337672323"),
    USD_BOND: Decimal("415.0454914182794933306"),
    FLOATER: Decimal("-2.524875"),
}
EXPECTED_TOTAL = Decimal("383.4813548556457260983")  # ОФЗ 26238 + ГазКЗ-34Д


@pytest.fixture
def sources():
    """Настоящие адаптеры на записанных ответах; объекты новые на каждый запрос, как в deps"""
    from app.main import app

    iss, cbr = FakeIss(), FakeCbr()
    app.dependency_overrides[get_market_data] = iss.source
    app.dependency_overrides[get_fx_rates] = cbr.source
    app.dependency_overrides[get_risk_calculator] = lambda: calc_dv01
    yield iss, cbr
    for dep in (get_market_data, get_fx_rates, get_risk_calculator):
        app.dependency_overrides.pop(dep, None)


async def calculate(api, isins, on=FRIDAY):
    ws_id, h = await open_workspace(api)
    for revision, isin in enumerate(isins):
        r = await post_trade(api, ws_id, h, isin, QUANTITY.get(isin, 1), revision)
        assert r.status_code == 201, r.text
    r = await run_calc(api, ws_id, h, len(isins), on)
    assert r.status_code == 201, r.text
    calc = r.json()
    return calc, {row["isin"]: row for row in calc["results"]}


def close(value: str | None, expected: Decimal) -> bool:
    return value is not None and abs(Decimal(value) - expected) < PRECISION


async def test_rub_and_usd_bonds_on_real_parameters(api, sources):
    iss, cbr = sources
    calc, rows = await calculate(api, [OFZ, USD_BOND])
    assert (calc["status"], calc["currency"]) == ("success", "RUB")
    for isin in (OFZ, USD_BOND):
        assert Decimal(rows[isin]["current_fair_value"]) == EXPECTED_CFV[isin], isin
        assert close(rows[isin]["metric_value"], EXPECTED_DV01[isin]), isin
        assert (rows[isin]["provider"], rows[isin]["data_as_of"]) == ("moex", "2026-11-13")
    assert close(calc["total_value"], EXPECTED_TOTAL)

    ofz, usd = rows[OFZ], rows[USD_BOND]
    assert (ofz["currency"], ofz["settlement_currency"], ofz["fx_source"]) == ("RUB", "RUB", None)
    assert (Decimal(ofz["price_pct_used"]), Decimal(ofz["accrued_interest_used"])) == (
        Decimal("51.577"),
        Decimal("31.7"),
    )
    assert (usd["currency"], usd["settlement_currency"]) == ("USD", "RUB")
    assert (usd["fx_source"], usd["fx_rate_date"], Decimal(usd["fx_rate_used"])) == (
        "cbr",
        "2026-11-13",
        Decimal("82.9803"),
    )
    assert Decimal(usd["accrued_interest_used"]) == Decimal("3.59")  # в долларах, не 297.8993 ₽
    # курс спрошен один раз и только для USD: у рублёвой бумаги внешнего запроса курса нет
    assert cbr.requests == ["13/11/2026"]


@pytest.mark.parametrize("cached_moex", [False, True])
async def test_legacy_demo_catalog_is_refreshed_without_changing_history(
    api, engine, sources, cached_moex
):
    """Old VM: this USD ISIN was seeded as a RUB demo floater, even with a cached MOEX quote."""
    from app.main import app

    instrument_id = insert_instrument(engine, USD_BOND, "RUB")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE instruments SET name = 'ДЕМО флоатер' WHERE id = :id"),
            {"id": instrument_id},
        )
    insert_snapshot(
        engine,
        instrument_id,
        provider="test",
        data_as_of=FRIDAY,
        price_pct=100,
        accrued_interest=5,
        duration_mod=None,
        convexity=None,
        coupon_type="floating",
        coupon_frequency=4,
    )
    ws_id, h = await open_workspace(api)
    assert (await post_trade(api, ws_id, h, USD_BOND, -10, 0)).status_code == 201
    source = app.dependency_overrides[get_market_data]
    app.dependency_overrides[get_market_data] = lambda: NoMarketDataSource()
    old = (await run_calc(api, ws_id, h, 1, FRIDAY)).json()
    app.dependency_overrides[get_market_data] = source
    assert (old["status"], old["results"][0]["provider"], old["results"][0]["currency"]) == (
        "success",
        "test",
        "RUB",
    )
    iss, _ = sources
    if cached_moex:
        snap = await iss.source().get_snapshot(USD_BOND, FRIDAY)
        insert_snapshot(
            engine,
            instrument_id,
            provider="moex",
            data_as_of=FRIDAY,
            currency=snap.currency,
            nominal=snap.nominal,
            price_pct=snap.price_pct,
            accrued_interest=snap.accrued_interest,
            duration_mod=snap.duration_mod,
            convexity=snap.convexity,
        )
    new = (await run_calc(api, ws_id, h, 1, FRIDAY)).json()
    assert new["status"] == "success"
    assert close(new["total_value"], EXPECTED_DV01[USD_BOND])
    card = (await api.get(f"/api/v1/instruments/{USD_BOND}")).json()
    assert card["currency"] == "USD" and not card["name"].startswith("ДЕМО ")
    assert (await api.get(f"/api/v1/calculations/{old['id']}")).json() == old
    calls = len(iss.requests)
    assert (await run_calc(api, ws_id, h, 1, FRIDAY)).json()["status"] == "success"
    assert len(iss.requests) == calls  # Once reconciled, the cached snapshot needs no network.


async def test_floater_uses_tz_rule(api, sources):
    calc, rows = await calculate(api, [FLOATER])
    row = rows[FLOATER]
    assert calc["status"] == "success"
    assert (Decimal(row["duration_used"]), Decimal(row["convexity_used"])) == (
        Decimal("0.25"),
        Decimal(0),
    )
    assert Decimal(row["current_fair_value"]) == EXPECTED_CFV[FLOATER]
    assert Decimal(row["metric_value"]) == EXPECTED_DV01[FLOATER]


async def test_sunday_valuation_shows_real_data_dates(api, sources):
    """Оценка на воскресенье: итоги торгов за пятницу 13.11, курс ЦБ от 14.11 (82.1516)"""
    calc, rows = await calculate(api, [USD_BOND], SUNDAY)
    usd = rows[USD_BOND]
    assert (usd["data_as_of"], usd["fx_rate_date"]) == ("2026-11-13", "2026-11-14")
    assert Decimal(usd["current_fair_value"]) == Decimal("-794068.328924")
    assert close(usd["metric_value"], Decimal("410.9005534180754904995"))


async def test_unknown_bond_gives_partial_and_no_total(api, sources):
    calc, rows = await calculate(api, [OFZ, UNKNOWN])
    assert (calc["status"], calc["total_value"]) == ("partial", None)
    assert rows[UNKNOWN]["error_code"] == "INSTRUMENT_NOT_FOUND"
    assert close(rows[OFZ]["metric_value"], EXPECTED_DV01[OFZ])


async def test_cbr_down_gives_fx_error_not_rate(api, sources):
    """ЦБ не ответил: у валютной строки FX_PROVIDER_UNAVAILABLE, курс не подставляется"""
    _, cbr = sources
    cbr.status, cbr.raw = 503, b"Service Unavailable"
    calc, rows = await calculate(api, [OFZ, USD_BOND])
    assert (calc["status"], calc["total_value"]) == ("partial", None)
    assert (rows[USD_BOND]["error_code"], rows[USD_BOND]["metric_value"]) == (
        "FX_PROVIDER_UNAVAILABLE",
        None,
    )
    assert rows[OFZ]["status"] == "success"


async def test_moex_down_fails_rows_without_saved_data(api, sources):
    iss, _ = sources
    iss.status["search"] = 502
    calc, rows = await calculate(api, [OFZ, USD_BOND])
    assert (calc["status"], calc["total_value"]) == ("failed", None)
    assert {row["error_code"] for row in rows.values()} == {"PROVIDER_UNAVAILABLE"}


async def test_missing_duration_is_row_error_not_zero(api, sources):
    """Дюрация по графику не совпала с биржевой: D и C нет, строка MISSING_MARKET_DATA"""
    iss, _ = sources
    for row in iss.edit("yields_SU26238RMFS4")["history_yields"]["data"]:
        row[8] = 1000  # DURATION
    calc, rows = await calculate(api, [OFZ])
    assert (calc["status"], rows[OFZ]["error_code"], calc["total_value"]) == (
        "failed",
        "MISSING_MARKET_DATA",
        None,
    )
