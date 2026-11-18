"""Сквозная проверка S3-12 (#64): API, контрольные ответы Мосбиржи и ЦБ, две сессии, история

Контрольные ответы заданы на уровне контракта (MarketSnapshot, FxRate): это то, что должны
вернуть адаптеры Мосбиржи (#66) и ЦБ (#65) после нормализации. Значения учебные, это не
котировки Мосбиржи и не курсы ЦБ. Ожидаемые числа посчитаны вручную и записаны константами,
а не повторяют формулу из risk_core

Запись прогона: docs/backend-verification.md
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import contracts
from app.api.deps import csrf_token_for, get_fx_rates, get_market_data, get_risk_calculator
from app.enums import CouponType, Provider, RowErrorCode
from app.risk_core import calc_dv01
from app.services.workspaces import hash_session_key
from tests.conftest import alembic_config, create_empty_database, drop_database
from tests.test_api_trades import open_workspace, second_client, trades_url

VALUATION = date(2026, 11, 13)
RUB_BOND = "RU000A1038V6"  # рублёвый выпуск: номинал и расчёты в RUB
USD_BOND = "XS0191754729"  # валютный: номинал USD, расчёты в RUB (как у замещающих облигаций)
UNKNOWN = "US0378331005"  # Мосбиржа такого выпуска не знает

# Ответ адаптера Мосбиржи после нормализации: (валюта номинала, валюта расчётов, номинал,
# цена %, НКД в валюте номинала, дюрация, выпуклость)
MOEX = {
    RUB_BOND: ("RUB", "RUB", "1000", "95", "12.5", "3", "12"),
    USD_BOND: ("USD", "RUB", "1000", "98.5", "10.25", "4.2", "25"),
}
# Ответ ЦБ: (рублей, за сколько единиц); у USD курс за 1 единицу
CBR = {"USD": ("92.5", 1)}

# Ручной расчёт (методика dv01-v2):
# RUB: 100 × (1000 × 0.95 + 12.5) = 96 250;   × (−3 × 0.0001 + ½ × 12 × 0.0001²) = −28.869225
# USD: −10 × (1000 × 0.985 + 10.25) × 92.5 = −920 606.25;
#      × (−4.2 × 0.0001 + ½ × 25 × 0.0001²) = −920 606.25 × −0.000419875 = 386.53954921875
EXPECTED = {
    RUB_BOND: (100, Decimal("96250"), Decimal("-28.869225")),
    USD_BOND: (-10, Decimal("-920606.25"), Decimal("386.53954921875")),
}
EXPECTED_TOTAL = Decimal("357.67032421875")


class ControlMoex:
    provider = Provider.MOEX

    def __init__(self):
        self.calls: list[str] = []
        self.price_shift = Decimal(0)  # «следующая загрузка» с другой ценой

    async def get_instrument(self, isin: str) -> contracts.InstrumentInfo:
        self.calls.append(f"instrument:{isin}")
        if isin not in MOEX:
            raise contracts.MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND)
        return contracts.InstrumentInfo(isin, f"Контрольный {isin}", "bond", MOEX[isin][0])

    async def get_snapshot(self, isin: str, as_of: date) -> contracts.MarketSnapshot:
        self.calls.append(f"snapshot:{isin}:{as_of}")
        if isin not in MOEX:
            raise contracts.MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND)
        currency, settlement, nominal, price, accrued, dur, conv = MOEX[isin]
        return contracts.MarketSnapshot(
            isin=isin,
            provider=Provider.MOEX,
            data_as_of=as_of,
            retrieved_at=datetime.now(UTC),
            currency=currency,
            nominal=Decimal(nominal),
            price_pct=Decimal(price) + self.price_shift,
            accrued_interest=Decimal(accrued),
            duration_mod=Decimal(dur),
            convexity=Decimal(conv),
            coupon_type=CouponType.FIXED,
            coupon_frequency=2,
            settlement_currency=settlement,
        )


class ControlCbr:
    source = "cbr"

    def __init__(self):
        self.calls: list[str] = []

    async def get_rate_to_rub(self, currency: str, on: date) -> contracts.FxRate:
        self.calls.append(f"{currency}:{on}")
        if currency not in CBR:
            raise contracts.MarketDataError(RowErrorCode.MISSING_FX_RATE)
        value, nominal = CBR[currency]
        return contracts.FxRate.per_unit(currency, on, Decimal(value), nominal)


@pytest.fixture
def control():
    from app.main import app

    market, fx = ControlMoex(), ControlCbr()
    app.dependency_overrides[get_market_data] = lambda: market
    app.dependency_overrides[get_fx_rates] = lambda: fx
    app.dependency_overrides[get_risk_calculator] = lambda: calc_dv01
    yield market, fx
    for dep in (get_market_data, get_fx_rates, get_risk_calculator):
        app.dependency_overrides.pop(dep, None)


async def post_trade(client, ws_id, headers, isin, quantity, revision):
    body = {"isin": isin, "quantity": quantity, "expected_revision": revision}
    return await client.post(trades_url(ws_id), json=body, headers=headers)


async def run_calc(client, ws_id, headers, revision, on=VALUATION):
    body = {"expected_revision": revision, "valuation_date": on.isoformat()}
    return await client.post(f"/api/v1/workspaces/{ws_id}/calculations", json=body, headers=headers)


# --- 1. основной сценарий: рублёвый и валютный выпуск ----------------------------------


async def test_main_scenario_rub_and_fx_bonds(api, control):
    market, fx = control
    ws_id, h = await open_workspace(api)
    revision = 0
    for isin, (quantity, _, _) in EXPECTED.items():
        r = await post_trade(api, ws_id, h, isin, quantity, revision)
        assert r.status_code == 201, r.text
        revision = r.json()["revision"]
    assert revision == 2

    r = await run_calc(api, ws_id, h, revision)
    assert r.status_code == 201, r.text
    calc = r.json()
    assert (calc["status"], calc["methodology_version"], calc["currency"]) == (
        "success",
        "dv01-v2",
        "RUB",
    )
    assert Decimal(calc["total_value"]) == EXPECTED_TOTAL
    rows = {row["isin"]: row for row in calc["results"]}
    for isin, (quantity, cfv, dv01) in EXPECTED.items():
        row = rows[isin]
        assert row["quantity"] == quantity
        assert Decimal(row["current_fair_value"]) == cfv, isin
        assert Decimal(row["metric_value"]) == dv01, isin
        assert (row["provider"], row["data_as_of"]) == ("moex", VALUATION.isoformat())

    rub, usd = rows[RUB_BOND], rows[USD_BOND]
    assert (rub["currency"], rub["fx_source"], rub["fx_rate_used"]) == ("RUB", None, "1")
    assert (usd["currency"], usd["settlement_currency"]) == ("USD", "RUB")
    assert (usd["fx_source"], usd["fx_rate_date"], usd["fx_rate_used"]) == (
        "cbr",
        VALUATION.isoformat(),
        "92.5",
    )
    # каждый источник спрошен по одному разу, курс только для валюты номинала USD
    assert sorted(market.calls) == sorted(
        [f"instrument:{i}" for i in MOEX] + [f"snapshot:{i}:{VALUATION}" for i in MOEX]
    )
    assert fx.calls == [f"USD:{VALUATION}"]

    # результат читается повторно и после перезагрузки страницы (last_calculation_id)
    assert (await api.get(f"/api/v1/calculations/{calc['id']}")).json() == calc
    reopened = (await api.post("/api/v1/workspaces")).json()
    assert (reopened["id"], reopened["last_calculation_id"]) == (ws_id, calc["id"])


async def test_unknown_bond_gives_partial_without_total(api, control):
    ws_id, h = await open_workspace(api)
    await post_trade(api, ws_id, h, RUB_BOND, 100, 0)
    await post_trade(api, ws_id, h, UNKNOWN, 5, 1)
    calc = (await run_calc(api, ws_id, h, 2)).json()
    rows = {row["isin"]: row for row in calc["results"]}
    assert (calc["status"], calc["total_value"]) == ("partial", None)
    assert (rows[UNKNOWN]["error_code"], rows[UNKNOWN]["metric_value"]) == (
        "INSTRUMENT_NOT_FOUND",
        None,
    )
    assert Decimal(rows[RUB_BOND]["metric_value"]) == EXPECTED[RUB_BOND][2]


# --- 2. два независимых посетителя ------------------------------------------------------


async def test_two_visitors_see_and_change_only_their_own_set(api, control):
    # посетитель A
    a_id, a_h = await open_workspace(api)
    r = await post_trade(api, a_id, a_h, RUB_BOND, 100, 0)
    a_trade = r.json()["trades"][0]["id"]
    a_calc = (await run_calc(api, a_id, a_h, 1)).json()

    async with second_client() as b:
        b_id, b_h = await open_workspace(b)
        assert b_id != a_id and b_h != a_h
        b_ws = (await b.get(f"/api/v1/workspaces/{b_id}")).json()
        assert (b_ws["trades"], b_ws["revision"]) == ([], 0)

        # чужой набор, сделка и расчёт: 404, как будто их нет
        assert (await b.get(f"/api/v1/workspaces/{a_id}")).json()["code"] == "WORKSPACE_NOT_FOUND"
        attempts = [
            b.post(
                trades_url(a_id),
                json={"isin": USD_BOND, "quantity": 1, "expected_revision": 1},
                headers=b_h,
            ),
            b.patch(
                trades_url(a_id, a_trade),
                json={"quantity": 999, "expected_revision": 1},
                headers=b_h,
            ),
            b.delete(trades_url(a_id, a_trade), params={"expected_revision": 1}, headers=b_h),
            run_calc(b, a_id, b_h, 1),
            b.get(f"/api/v1/calculations/{a_calc['id']}"),
            b.get(f"/api/v1/calculations/{a_calc['id']}/source-diff"),
        ]
        for attempt in attempts:
            r = await attempt
            assert r.status_code == 404, r.text
        # CSRF-токен A с cookie B не подходит даже к набору B
        r = await post_trade(b, b_id, a_h, USD_BOND, 1, 0)
        assert (r.status_code, r.json()["code"]) == (403, "CSRF_TOKEN_INVALID")

        # B меняет только свой набор
        r = await post_trade(b, b_id, b_h, USD_BOND, -3, 0)
        assert r.status_code == 201 and r.json()["revision"] == 1

    # набор и расчёт A не изменились
    a_ws = (await api.get(f"/api/v1/workspaces/{a_id}")).json()
    assert a_ws["revision"] == 1
    assert [(t["isin"], t["quantity"]) for t in a_ws["trades"]] == [(RUB_BOND, 100)]
    assert (await api.get(f"/api/v1/calculations/{a_calc['id']}")).json() == a_calc

    # без cookie: 401
    async with second_client() as anonymous:
        r = await anonymous.get(f"/api/v1/workspaces/{a_id}")
        assert (r.status_code, r.json()["code"]) == (401, "SESSION_REQUIRED")


# --- 3. revision и история ----------------------------------------------------------------


async def test_revision_conflict_and_history_keeps_own_inputs(api, control, engine):
    market, _ = control
    ws_id, h = await open_workspace(api)
    r = await post_trade(api, ws_id, h, RUB_BOND, 100, 0)
    trade_id = r.json()["trades"][0]["id"]
    await post_trade(api, ws_id, h, USD_BOND, -10, 1)
    old = (await run_calc(api, ws_id, h, 2)).json()
    assert old["workspace_revision"] == 2

    # вторая вкладка изменила набор: первая со старым revision получает 409, набор не меняется
    r = await api.patch(
        trades_url(ws_id, trade_id), json={"quantity": 200, "expected_revision": 2}, headers=h
    )
    assert r.json()["revision"] == 3
    for stale in (
        api.patch(
            trades_url(ws_id, trade_id),
            json={"quantity": 300, "expected_revision": 2},
            headers=h,
        ),
        post_trade(api, ws_id, h, UNKNOWN, 1, 2),
        api.delete(trades_url(ws_id, trade_id), params={"expected_revision": 2}, headers=h),
        run_calc(api, ws_id, h, 2),
    ):
        r = await stale
        assert (r.status_code, r.json()["code"]) == (409, "REVISION_CONFLICT")
    ws = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()
    assert ws["revision"] == 3
    assert {t["isin"]: t["quantity"] for t in ws["trades"]} == {RUB_BOND: 200, USD_BOND: -10}

    # новая загрузка на следующий день: цена у Мосбиржи выше на 1 п.п.
    market.price_shift = Decimal(1)
    new = (await run_calc(api, ws_id, h, 3, VALUATION + timedelta(days=1))).json()
    new_rub = next(row for row in new["results"] if row["isin"] == RUB_BOND)
    assert (new_rub["quantity"], new_rub["price_pct_used"]) == (200, "96")

    # старый расчёт сохранил собственные входы: количество 100, цена 95, дата 13.11
    again = (await api.get(f"/api/v1/calculations/{old['id']}")).json()
    assert again == old
    old_rub = next(row for row in again["results"] if row["isin"] == RUB_BOND)
    assert (old_rub["quantity"], old_rub["price_pct_used"], old_rub["data_as_of"]) == (
        100,
        "95",
        VALUATION.isoformat(),
    )
    diff = (
        await api.get(f"/api/v1/calculations/{old['id']}/source-diff?as_of={VALUATION.isoformat()}")
    ).json()
    assert all(row["changed"] == ["price_pct"] for row in diff["rows"])

    # удаление сделки отвязывает строку истории, но не стирает её
    await api.delete(trades_url(ws_id, trade_id), params={"expected_revision": 3}, headers=h)
    again = (await api.get(f"/api/v1/calculations/{old['id']}")).json()
    old_rub = next(row for row in again["results"] if row["isin"] == RUB_BOND)
    assert old_rub["trade_id"] is None
    assert Decimal(old_rub["metric_value"]) == EXPECTED[RUB_BOND][2]


# --- 4. обновление приложения: данные стенда до 0004/0005 переживают миграции -------------


SESSION = "integration-check-session-key"


def _seed_stand_at_0003(conn) -> dict:
    """Данные в том виде, в каком их записала версия main (схема 0003, демо и Cbonds)"""
    ids = {k: uuid.uuid4() for k in ("ws", "trade", "inst", "snap_test", "snap_cbonds", "fx")}
    ids.update(run=uuid.uuid4(), result=uuid.uuid4())
    day = date(2026, 10, 26)
    conn.execute(
        text("INSERT INTO workspaces (id, session_key_hash, revision) VALUES (:id, :h, 1);"),
        {"id": ids["ws"], "h": hash_session_key(SESSION)},
    )
    conn.execute(
        text(
            "INSERT INTO instruments (id, isin, name, instrument_type, currency)"
            " VALUES (:id, :isin, 'ДЕМО еврооблигация (USD)', 'bond', 'USD')"
        ),
        {"id": ids["inst"], "isin": USD_BOND},
    )
    conn.execute(
        text(
            "INSERT INTO trades (id, workspace_id, isin, quantity, instrument_id)"
            " VALUES (:id, :ws, :isin, -10, :inst)"
        ),
        {"id": ids["trade"], "ws": ids["ws"], "isin": USD_BOND, "inst": ids["inst"]},
    )
    for key, provider in (("snap_test", "test"), ("snap_cbonds", "cbonds")):
        conn.execute(
            text(
                "INSERT INTO market_data_snapshots (id, instrument_id, provider, data_as_of,"
                " retrieved_at, currency, nominal, price_pct, accrued_interest, duration_mod,"
                " convexity, coupon_type, coupon_frequency) VALUES (:id, :inst, :p, :d, now(),"
                " 'USD', 1000, :price, 10.25, 4.2, 25, 'fixed', 2)"
            ),
            {
                "id": ids[key],
                "inst": ids["inst"],
                "p": provider,
                "d": day,
                # Cbonds дал бы другую цену: новый расчёт её брать не должен
                "price": "98.5" if provider == "test" else "50",
            },
        )
    conn.execute(
        text(
            "INSERT INTO fx_rates (id, currency, rate_date, rate_to_rub, source, retrieved_at)"
            " VALUES (:id, 'USD', :d, 92.5, 'demo', now())"
        ),
        {"id": ids["fx"], "d": day},
    )
    conn.execute(
        text(
            "INSERT INTO calculation_runs (id, workspace_id, workspace_revision, measure_code,"
            " methodology_version, valuation_date, status, total_value, completed_at)"
            " VALUES (:id, :ws, 1, 'DV01', 'dv01-v2', :d, 'success', 386.53954921875, now())"
        ),
        {"id": ids["run"], "ws": ids["ws"], "d": day},
    )
    conn.execute(
        text(
            "INSERT INTO calculation_results (id, run_id, trade_id, market_data_snapshot_id,"
            " fx_rate_id, input_isin, input_quantity, nominal_used, price_pct_used,"
            " accrued_interest_used, fx_rate_used, current_fair_value, duration_used,"
            " convexity_used, metric_value, status) VALUES (:id, :run, :trade, :snap, :fx,"
            " :isin, -10, 1000, 98.5, 10.25, 92.5, -920606.25, 4.2, 25, 386.53954921875,"
            " 'success')"
        ),
        {
            "id": ids["result"],
            "run": ids["run"],
            "trade": ids["trade"],
            "snap": ids["snap_test"],
            "fx": ids["fx"],
            "isin": USD_BOND,
        },
    )
    return ids


@pytest.fixture
async def upgraded_stand(test_db_url):
    """Отдельная база: миграции до 0003, данные старой версии, затем upgrade head"""
    from app.db.session import get_session
    from app.main import app

    name = f"{test_db_url.database}_upgrade"
    url = create_empty_database(name)
    engine = create_engine(url)
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "0003")
        ids = _seed_stand_at_0003(conn)
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    async_engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    yield ids, version, engine
    app.dependency_overrides.pop(get_session, None)
    await async_engine.dispose()
    engine.dispose()
    drop_database(name)


async def test_stand_data_survives_upgrade_to_head(upgraded_stand, control):
    from sqlalchemy.exc import IntegrityError

    ids, version, engine = upgraded_stand
    assert version == "0005"
    async with second_client() as user:
        user.headers["Cookie"] = f"rm_session={SESSION}"  # cookie, выданная старой версией
        h = {"X-CSRF-Token": csrf_token_for(SESSION)}

        # сессия, набор и сделки на месте
        ws = (await user.post("/api/v1/workspaces")).json()
        assert (ws["id"], ws["revision"], ws["last_calculation_id"]) == (
            str(ids["ws"]),
            1,
            str(ids["run"]),
        )
        assert [(t["isin"], t["quantity"]) for t in ws["trades"]] == [(USD_BOND, -10)]

        # старый расчёт читается с прежними входами и происхождением
        old = (await user.get(f"/api/v1/calculations/{ids['run']}")).json()
        row = old["results"][0]
        assert (old["status"], Decimal(old["total_value"])) == ("success", EXPECTED[USD_BOND][2])
        assert (row["provider"], row["data_as_of"], row["fx_source"]) == (
            "test",
            "2026-10-26",
            "demo",
        )
        assert (row["price_pct_used"], row["currency"], row["settlement_currency"]) == (
            "98.5",
            "USD",
            None,  # до 0004 валюту расчётов не хранили
        )

        # новый расчёт после обновления берёт Мосбиржу и ЦБ, снимок Cbonds не трогает
        new = (await run_calc(user, ws["id"], h, 1)).json()
        new_row = new["results"][0]
        assert (new["status"], new_row["provider"], new_row["fx_source"]) == (
            "success",
            "moex",
            "cbr",
        )
        assert new_row["price_pct_used"] == "98.5"
        assert (await user.get(f"/api/v1/calculations/{ids['run']}")).json() == old

    # переписать историю после обновления нельзя
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE calculation_results SET metric_value = 0"))
