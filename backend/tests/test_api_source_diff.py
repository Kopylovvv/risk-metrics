"""Сохранённый расчёт и новый ответ источника; история не переписывается (S3-10)"""

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app import contracts
from app.api.deps import get_fx_rates, get_market_data, get_risk_calculator
from app.risk_core import calc_dv01
from tests.test_api_calculations import (
    DAY,
    FIXED,
    NEW_BOND,
    USD_BOND,
    by_isin,
    calculate,
    insert_instrument,
    insert_snapshot,
    workspace_with,
)
from tests.test_api_market_sources import Cbr, Moex, insert_rate
from tests.test_api_trades import second_client, trades_url

NEXT_DAY = DAY + timedelta(days=1)


class ChangingMoex(Moex):
    """Мосбиржа, у которой на следующий день другая цена и НКД"""

    price: Decimal | None = None
    accrued: Decimal | None = None

    async def get_snapshot(self, isin: str, as_of: date) -> contracts.MarketSnapshot:
        snap = await super().get_snapshot(isin, as_of)
        if as_of > DAY and self.price is not None:
            snap = replace(snap, price_pct=self.price, accrued_interest=self.accrued)
        return snap


class ChangingCbr(Cbr):
    rate = Decimal("92.5")

    async def get_rate_to_rub(self, currency: str, on: date) -> contracts.FxRate:
        rate = await super().get_rate_to_rub(currency, on)
        return replace(rate, rate_to_rub=self.rate if on > DAY else rate.rate_to_rub)


@pytest.fixture
def sources():
    from app.main import app

    market, fx = ChangingMoex(), ChangingCbr()
    app.dependency_overrides[get_market_data] = lambda: market
    app.dependency_overrides[get_fx_rates] = lambda: fx
    app.dependency_overrides[get_risk_calculator] = lambda: calc_dv01
    return market, fx


def diff_url(calc_id: str, as_of: date | None = None) -> str:
    url = f"/api/v1/calculations/{calc_id}/source-diff"
    return f"{url}?as_of={as_of.isoformat()}" if as_of else url


def count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


async def test_diff_shows_saved_snapshot_next_to_new_source_answer(api, engine, sources):
    market, fx = sources
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 100), (USD_BOND, -10))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "success"
    saved_rows = (count(engine, "market_data_snapshots"), count(engine, "fx_rates"))

    # на следующий день у Мосбиржи другая цена и НКД, у ЦБ другой курс
    market.price, market.accrued = Decimal("99.1"), Decimal("10.5")
    fx.rate = Decimal("93.1")
    r = await api.get(diff_url(calc["id"], NEXT_DAY))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["valuation_date"], body["compared_as_of"]) == (
        DAY.isoformat(),
        NEXT_DAY.isoformat(),
    )
    rows = {row["isin"]: row for row in body["rows"]}

    usd = rows[USD_BOND]
    assert (usd["saved"]["provider"], usd["saved"]["data_as_of"]) == ("moex", DAY.isoformat())
    assert (usd["saved"]["price_pct"], usd["saved"]["accrued_interest"]) == ("98.5", "10.25")
    assert (usd["current"]["data_as_of"], usd["current"]["price_pct"]) == (
        NEXT_DAY.isoformat(),
        "99.1",
    )
    assert (usd["current_origin"], usd["current_error"]) == ("source", None)
    assert usd["saved_fx"] == {"source": "cbr", "rate_date": DAY.isoformat(), "rate_to_rub": "92.5"}
    assert (usd["current_fx"]["rate_to_rub"], usd["current_fx_origin"]) == ("93.1", "source")
    assert usd["changed"] == [
        "data_as_of",
        "price_pct",
        "accrued_interest",
        "fx_rate_date",
        "fx_rate_to_rub",
    ]

    rub = rows[NEW_BOND]
    # рублёвой бумаге курс не нужен ни в расчёте, ни при сравнении
    assert (rub["saved_fx"], rub["current_fx"], rub["current_fx_origin"]) == (None, None, None)
    assert "fx_rate_to_rub" not in rub["changed"]

    # сравнение ничего не записывает, сохранённый расчёт тот же
    assert (count(engine, "market_data_snapshots"), count(engine, "fx_rates")) == saved_rows
    assert (await api.get(f"/api/v1/calculations/{calc['id']}")).json() == calc


async def test_same_answer_gives_no_changes(api, sources):
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    row = (await api.get(diff_url(calc["id"], DAY))).json()["rows"][0]
    assert row["changed"] == []
    assert row["saved"]["retrieved_at"] != row["current"]["retrieved_at"]  # не сравнивается


async def test_unavailable_source_falls_back_to_newer_saved_data(api, engine, sources):
    market, fx = sources
    market.down, fx.mode = True, "down"
    instrument_id = insert_instrument(engine, USD_BOND, "USD")
    insert_snapshot(engine, instrument_id, currency="USD", price_pct="98.5")
    insert_rate(engine, "USD", "92.5", DAY, "cbr")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()

    # позже загружены данные за следующий день
    insert_snapshot(engine, instrument_id, currency="USD", price_pct="97", data_as_of=NEXT_DAY)
    insert_rate(engine, "USD", "94", NEXT_DAY, "cbr")
    row = (await api.get(diff_url(calc["id"], NEXT_DAY))).json()["rows"][0]
    assert (row["current_origin"], row["current_error"]) == ("db", "PROVIDER_UNAVAILABLE")
    assert (row["current_fx_origin"], row["current_fx_error"]) == ("db", "FX_PROVIDER_UNAVAILABLE")
    assert (row["current"]["price_pct"], row["current_fx"]["rate_to_rub"]) == ("97", "94")
    assert row["changed"] == ["data_as_of", "price_pct", "fx_rate_date", "fx_rate_to_rub"]
    # старый расчёт по-прежнему на своих данных
    old = (await api.get(f"/api/v1/calculations/{calc['id']}")).json()["results"][0]
    assert (old["price_pct_used"], old["fx_rate_used"], old["data_as_of"]) == (
        "98.5",
        "92.5",
        DAY.isoformat(),
    )


async def test_failed_row_has_nothing_saved_to_compare(api, sources):
    market, _ = sources
    market.down = True
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["results"][0]["error_code"] == "PROVIDER_UNAVAILABLE"
    row = (await api.get(diff_url(calc["id"], DAY))).json()["rows"][0]
    assert (row["saved"], row["current"], row["changed"]) == (None, None, [])
    assert (row["current_origin"], row["current_error"]) == (None, "PROVIDER_UNAVAILABLE")


async def test_diff_without_adapters_uses_saved_data(api, engine):
    """С заглушками вместо Мосбиржи и ЦБ сравнение идёт с последним сохранённым в БД"""
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    ws_id, h, rev = await workspace_with(api, (FIXED, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    row = (await api.get(diff_url(calc["id"]))).json()["rows"][0]
    assert (row["current_origin"], row["changed"]) == ("db", [])


async def test_diff_checks_date_and_owner(api, sources):
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    calc_id = (await calculate(api, ws_id, h, rev)).json()["id"]
    r = await api.get(diff_url(calc_id, date.today() + timedelta(days=1)))
    assert r.status_code == 422
    async with second_client() as stranger:
        await stranger.post("/api/v1/workspaces")
        assert (await stranger.get(diff_url(calc_id))).status_code == 404
    assert (await api.get(diff_url(str(uuid.uuid4())))).status_code == 404


# --- DV01 на сегодня: «сравнить с данными сегодня» ----------------------------------------


async def test_diff_gives_dv01_by_new_data_equal_to_real_recalculation(api, engine, sources):
    market, fx = sources
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 100), (USD_BOND, -10), (NEW_BOND, 5))
    calc = (await calculate(api, ws_id, h, rev)).json()
    market.price, market.accrued = Decimal("99.1"), Decimal("10.5")
    fx.rate = Decimal("93.1")
    saved_rows = (count(engine, "market_data_snapshots"), count(engine, "calculation_runs"))

    body = (await api.get(diff_url(calc["id"], NEXT_DAY))).json()
    rows = {row["isin"]: row for row in body["rows"]}
    assert (count(engine, "market_data_snapshots"), count(engine, "calculation_runs")) == saved_rows

    # тот же состав, посчитанный по-настоящему на следующий день в другом наборе
    async with second_client() as other:
        other_id, oh, orev = await workspace_with(
            other, (NEW_BOND, 100), (USD_BOND, -10), (NEW_BOND, 5)
        )
        fresh = (await calculate(other, other_id, oh, orev, NEXT_DAY)).json()
    assert fresh["status"] == "success"
    fresh_by_isin: dict[str, Decimal] = {}
    for row in fresh["results"]:
        fresh_by_isin[row["isin"]] = fresh_by_isin.get(row["isin"], 0) + Decimal(
            row["metric_value"]
        )
    saved_by_isin: dict[str, Decimal] = {}
    for row in calc["results"]:
        saved_by_isin[row["isin"]] = saved_by_isin.get(row["isin"], 0) + Decimal(
            row["metric_value"]
        )

    for isin in (NEW_BOND, USD_BOND):
        row = rows[isin]
        assert Decimal(row["saved_dv01"]) == saved_by_isin[isin]
        assert Decimal(row["current_dv01"]) == fresh_by_isin[isin]
        assert Decimal(row["dv01_change"]) == fresh_by_isin[isin] - saved_by_isin[isin]
        assert row["current_dv01_error"] is None
    assert (rows[NEW_BOND]["quantity"], rows[USD_BOND]["quantity"]) == (105, -10)
    assert Decimal(rows[USD_BOND]["dv01_change"]) != 0  # цена и курс изменились

    assert Decimal(body["saved_total"]) == Decimal(calc["total_value"])
    assert Decimal(body["current_total"]) == Decimal(fresh["total_value"])
    assert Decimal(body["total_change"]) == Decimal(fresh["total_value"]) - Decimal(
        calc["total_value"]
    )
    # старый расчёт и его история не изменились
    assert (await api.get(f"/api/v1/calculations/{calc['id']}")).json() == calc


async def test_same_data_gives_zero_change(api, sources):
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 3))
    calc = (await calculate(api, ws_id, h, rev)).json()
    body = (await api.get(diff_url(calc["id"], DAY))).json()
    assert Decimal(body["total_change"]) == 0
    assert body["current_total"] == body["saved_total"] == calc["total_value"]
    assert Decimal(body["rows"][0]["dv01_change"]) == 0


async def test_current_dv01_uses_saved_rate_when_fx_source_is_down(api, sources):
    """Источник курсов не ответил: DV01 на сегодня считается по последнему курсу из БД"""
    _, fx = sources
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1), (USD_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    fx.mode = "down"
    body = (await api.get(diff_url(calc["id"], NEXT_DAY))).json()
    rows = {row["isin"]: row for row in body["rows"]}
    # рублёвой бумаге курс не нужен; валютная берёт курс расчёта из БД
    assert rows[NEW_BOND]["current_dv01"] is not None
    usd = rows[USD_BOND]
    assert (usd["current_fx_origin"], usd["current_fx_error"]) == ("db", "FX_PROVIDER_UNAVAILABLE")
    assert usd["current_dv01"] is not None and usd["current_dv01_error"] is None
    assert body["current_total"] is not None


async def test_no_current_dv01_without_any_data(api, sources):
    """Данных нет ни у источника, ни в БД: DV01 на сегодня нет, итога тоже"""
    market, _ = sources
    market.down = True
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    failed = (await calculate(api, ws_id, h, rev)).json()
    assert failed["status"] == "failed"
    body = (await api.get(diff_url(failed["id"], DAY))).json()
    row = body["rows"][0]
    assert (row["saved_dv01"], row["current_dv01"], row["dv01_change"]) == (None, None, None)
    assert row["current_dv01_error"] == "PROVIDER_UNAVAILABLE"
    assert (body["saved_total"], body["current_total"], body["total_change"]) == (None, None, None)


# --- БД: история только добавляется ---------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE market_data_snapshots SET price_pct = 1",
        "UPDATE fx_rates SET rate_to_rub = 1",
        "UPDATE calculation_runs SET valuation_date = '2026-01-01'",
        "UPDATE calculation_results SET price_pct_used = 1",
        "UPDATE calculation_results SET market_data_snapshot_id = NULL",
    ],
)
async def test_saved_data_cannot_be_rewritten(api, engine, sources, statement):
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    assert (await calculate(api, ws_id, h, rev)).json()["status"] == "success"
    with pytest.raises(IntegrityError, match="не изменя"), engine.begin() as conn:
        conn.execute(text(statement))


async def test_deleting_trade_still_detaches_result(api, engine, sources):
    """Удаление сделки обнуляет trade_id в результате: это единственная разрешённая правка"""
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    trade_id = calc["results"][0]["trade_id"]
    r = await api.delete(trades_url(ws_id, trade_id), params={"expected_revision": rev}, headers=h)
    assert r.status_code in (200, 204), r.text
    row = by_isin((await api.get(f"/api/v1/calculations/{calc['id']}")).json())[NEW_BOND]
    assert row["trade_id"] is None and row["metric_value"] == calc["results"][0]["metric_value"]
