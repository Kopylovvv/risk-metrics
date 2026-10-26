"""Расчёт DV01 через API: от сделок до сохранённого результата, история расчётов"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app import contracts
from app.api.deps import get_fx_rates, get_market_data, get_risk_calculator
from app.contracts import Dv01Input
from app.enums import CouponType, Provider, RowErrorCode
from app.risk_core import calc_dv01
from tests.test_api_trades import add, open_workspace, second_client, trades_url

DAY = date(2026, 10, 20)
FIXED = "RU000A1038V6"  # фикс. купон: D = 3, C = 12, цена 95%, НКД 12.5
FLOATER = "RU000A105A95"  # плавающий купон, 4 выплаты в год
USD_BOND = "XS0191754729"  # номинал в долларах
NEW_BOND = "RU000A0JX0J2"  # нет в БД, есть только у источника
UNKNOWN = "US0378331005"  # нет нигде


# --- данные в БД ------------------------------------------------------------------


def insert_instrument(engine: Engine, isin: str, currency: str = "RUB") -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO instruments (id, isin, name, instrument_type, currency)"
                " VALUES (:id, :isin, :name, 'bond', :cur) RETURNING id"
            ),
            {"id": uuid.uuid4(), "isin": isin, "name": f"Облигация {isin}", "cur": currency},
        ).scalar_one()


def insert_snapshot(engine: Engine, instrument_id: uuid.UUID, **values) -> None:
    row = dict(
        id=uuid.uuid4(),
        instrument_id=instrument_id,
        provider="cbonds",
        data_as_of=DAY,
        retrieved_at=datetime.now(UTC),
        currency="RUB",
        nominal=1000,
        price_pct=95,
        accrued_interest="12.5",
        duration_mod=3,
        convexity=12,
        coupon_type="fixed",
        coupon_frequency=2,
    )
    row.update(values)
    columns = ", ".join(row)
    params = ", ".join(f":{c}" for c in row)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO market_data_snapshots ({columns}) VALUES ({params})"), row)


def insert_fx(engine: Engine, currency: str, rate: str, on: date = DAY) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO fx_rates (id, currency, rate_date, rate_to_rub, source, retrieved_at)"
                " VALUES (:id, :c, :d, :r, 'cbr', now())"
            ),
            {"id": uuid.uuid4(), "c": currency, "d": on, "r": rate},
        )


@pytest.fixture
def market_in_db(engine: Engine):
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    insert_snapshot(
        engine,
        insert_instrument(engine, FLOATER),
        price_pct=100,
        accrued_interest=5,
        duration_mod=None,
        convexity=None,
        coupon_type="floating",
        coupon_frequency=4,
    )
    insert_snapshot(
        engine,
        insert_instrument(engine, USD_BOND, "USD"),
        currency="USD",
        price_pct="98.5",
        accrued_interest="10.25",
        duration_mod="4.2",
        convexity=25,
    )
    insert_fx(engine, "USD", "92.5")


# --- подставные источники ------------------------------------------------------------


class FakeMarket:
    """Источник с данными только по NEW_BOND; считает обращения"""

    def __init__(self, on_call=None):
        self.calls: list[str] = []
        self.on_call = on_call  # проверка во время «внешнего запроса»
        self.mode = "ok"  # crash: адаптер падает; future: данные позже даты оценки

    async def get_instrument(self, isin: str) -> contracts.InstrumentInfo:
        self.calls.append(f"instrument:{isin}")
        if self.on_call:
            self.on_call()
        if isin != NEW_BOND:
            raise contracts.MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND)
        return contracts.InstrumentInfo(isin, "Новая облигация", "bond", "RUB")

    async def get_snapshot(self, isin: str, as_of: date) -> contracts.MarketSnapshot:
        self.calls.append(f"snapshot:{isin}")
        if self.mode == "crash":
            raise TimeoutError("источник не ответил")
        if self.mode == "future":
            as_of = date(2030, 1, 1)
        if isin != NEW_BOND:
            raise contracts.MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE)
        return contracts.MarketSnapshot(
            isin=isin,
            provider=Provider.RUDATA,
            data_as_of=as_of,
            retrieved_at=datetime.now(UTC),
            currency="RUB",
            nominal=Decimal(1000),
            price_pct=Decimal("101.2"),
            accrued_interest=Decimal("3.1"),
            duration_mod=Decimal(2),
            convexity=Decimal(5),
            coupon_type=CouponType.FIXED,
            coupon_frequency=2,
        )


class FakeFx:
    def __init__(self, rates: dict[str, str] | None = None):
        self.rates = rates or {}

    async def get_rate_to_rub(self, currency: str, on: date) -> contracts.FxRate:
        if currency not in self.rates:
            raise contracts.MarketDataError(RowErrorCode.MISSING_FX_RATE)
        return contracts.FxRate(currency, on, Decimal(self.rates[currency]), "cbr")


@pytest.fixture
def sources():
    """Подменяет источники и ядро в приложении; ядро настоящее (app/risk_core)"""
    from app.main import app

    market, fx = FakeMarket(), FakeFx()
    app.dependency_overrides[get_market_data] = lambda: market
    app.dependency_overrides[get_fx_rates] = lambda: fx
    app.dependency_overrides[get_risk_calculator] = lambda: calc_dv01
    return market, fx


async def calculate(client, ws_id, headers, revision, valuation_date=DAY):
    body = {"expected_revision": revision}
    if valuation_date:
        body["valuation_date"] = valuation_date.isoformat()
    return await client.post(f"/api/v1/workspaces/{ws_id}/calculations", json=body, headers=headers)


async def workspace_with(client, *trades) -> tuple[str, dict, int]:
    """Набор со сделками (isin, quantity); возвращает id, заголовки и revision"""
    ws_id, h = await open_workspace(client)
    for revision, (isin, quantity) in enumerate(trades):
        r = await add(client, ws_id, h, isin=isin, quantity=quantity, revision=revision)
        assert r.status_code == 201, r.text
    return ws_id, h, len(trades)


def by_isin(calc: dict) -> dict[str, dict]:
    return {row["isin"]: row for row in calc["results"]}


# --- контрольный запрос ---------------------------------------------------------------


async def test_control_request_from_trades_to_saved_result(api, market_in_db, sources, engine):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (FLOATER, 299), (USD_BOND, -10))

    r = await calculate(api, ws_id, h, rev)
    assert r.status_code == 201, r.text
    calc = r.json()
    assert calc["status"] == "success"
    assert calc["measure_code"] == "DV01" and calc["methodology_version"] == "dv01-v2"
    assert calc["workspace_revision"] == rev and calc["valuation_date"] == "2026-10-20"
    assert [row["isin"] for row in calc["results"]] == [FIXED, FLOATER, USD_BOND]  # порядок сделок

    rows = by_isin(calc)
    # цена с НКД: 100 × (1000 × 0.95 + 12.5) = 96 250 ₽
    # DV01 = 96 250 × (−3·1bp + ½·12·1bp²) = −28.869225
    assert Decimal(rows[FIXED]["accrued_interest_used"]) == Decimal("12.5")
    assert Decimal(rows[FIXED]["current_fair_value"]) == Decimal("96250")
    assert Decimal(rows[FIXED]["metric_value"]) == Decimal("-28.869225")
    # плавающий купон: D = 1/4, C = 0; 299 × (1000 + 5) = 300 495 ₽
    assert Decimal(rows[FLOATER]["duration_used"]) == Decimal("0.25")
    assert Decimal(rows[FLOATER]["convexity_used"]) == 0
    assert Decimal(rows[FLOATER]["metric_value"]) == Decimal("-7.512375")
    # шорт в долларах, НКД тоже в долларах: −10 × (985 + 10.25) × 92.5 = −920 606.25 ₽
    assert Decimal(rows[USD_BOND]["fx_rate_used"]) == Decimal("92.5")
    assert Decimal(rows[USD_BOND]["current_fair_value"]) == Decimal("-920606.25")
    assert Decimal(rows[USD_BOND]["metric_value"]) > 0
    expected_total = sum(Decimal(row["metric_value"]) for row in calc["results"])
    assert Decimal(calc["total_value"]) == expected_total
    assert rows[FIXED]["provider"] == "cbonds" and rows[FIXED]["data_as_of"] == "2026-10-20"

    # всё лежит в БД
    with engine.connect() as conn:
        run = conn.execute(
            text("SELECT status, total_value, workspace_revision FROM calculation_runs")
        ).one()
        assert (run.status, run.total_value, run.workspace_revision) == (
            "success",
            expected_total,
            rev,
        )
        saved = conn.execute(
            text(
                "SELECT input_isin, input_quantity, metric_value, fx_rate_id"
                " FROM calculation_results ORDER BY input_isin"
            )
        ).all()
        assert len(saved) == 3
        usd = next(s for s in saved if s.input_isin == USD_BOND)
        assert int(usd.input_quantity) == -10 and usd.fx_rate_id is not None
        rub = next(s for s in saved if s.input_isin == FIXED)
        assert rub.fx_rate_id is None  # рублю курс не нужен

    # тот же результат можно прочитать позже
    again = await api.get(f"/api/v1/calculations/{calc['id']}")
    assert again.status_code == 200
    assert again.json() == calc

    # данные были в БД: к источникам не ходили
    market, _ = sources
    assert market.calls == []


async def test_history_keeps_own_inputs_after_trade_changes(api, market_in_db, sources, engine):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (FLOATER, 299))
    calc = (await calculate(api, ws_id, h, rev)).json()
    trades = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()["trades"]

    await api.patch(
        trades_url(ws_id, trades[0]["id"]), json={"quantity": 5, "expected_revision": 2}, headers=h
    )
    await api.delete(trades_url(ws_id, trades[1]["id"]), params={"expected_revision": 3}, headers=h)

    old = (await api.get(f"/api/v1/calculations/{calc['id']}")).json()
    assert [row["quantity"] for row in old["results"]] == [100, 299]
    assert old["total_value"] == calc["total_value"]
    assert old["results"][1]["trade_id"] is None  # сделку удалили, строка осталась
    assert old["results"][0]["trade_id"] == trades[0]["id"]

    # новый расчёт видит уже новый набор
    new = (await calculate(api, ws_id, h, 4)).json()
    assert [(row["isin"], row["quantity"]) for row in new["results"]] == [(FIXED, 5)]


# --- разрешённые ошибки строк ------------------------------------------------------


async def test_workspace_points_to_last_calculation(api, market_in_db, sources):
    """После перезагрузки страницы фронт находит последний расчёт через набор"""
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    assert (await api.get(f"/api/v1/workspaces/{ws_id}")).json()["last_calculation_id"] is None

    first = (await calculate(api, ws_id, h, rev)).json()["id"]
    second = (await calculate(api, ws_id, h, rev)).json()["id"]
    reopened = (await api.post("/api/v1/workspaces")).json()  # та же cookie: тот же набор
    assert reopened["id"] == ws_id
    assert reopened["last_calculation_id"] == second != first
    restored = await api.get(f"/api/v1/calculations/{reopened['last_calculation_id']}")
    assert restored.json()["results"][0]["quantity"] == 100

    # изменение набора не стирает ссылку: старый результат по-прежнему доступен
    trade_id = restored.json()["results"][0]["trade_id"]
    r = await api.patch(
        trades_url(ws_id, trade_id), json={"quantity": 5, "expected_revision": rev}, headers=h
    )
    assert r.json()["last_calculation_id"] == second


async def test_partial_result_has_no_total(api, market_in_db, sources):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (UNKNOWN, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "partial" and calc["total_value"] is None
    bad = by_isin(calc)[UNKNOWN]
    assert bad["status"] == "failed" and bad["error_code"] == "INSTRUMENT_NOT_FOUND"
    assert bad["metric_value"] is None and bad["current_fair_value"] is None  # не 0
    assert by_isin(calc)[FIXED]["status"] == "success"


async def test_all_rows_failed(api, sources):
    ws_id, h, rev = await workspace_with(api, (UNKNOWN, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "failed" and calc["total_value"] is None


async def test_data_from_source_is_saved_and_reused(api, sources, engine):
    market, _ = sources
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 10))

    first = (await calculate(api, ws_id, h, rev)).json()
    row = first["results"][0]
    assert first["status"] == "success" and row["provider"] == "rudata"
    assert market.calls == [f"instrument:{NEW_BOND}", f"snapshot:{NEW_BOND}"]

    # справочник и снимок сохранены, сделка связана с инструментом
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM market_data_snapshots")).scalar_one() == 1
    card = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()["trades"][0]["instrument"]
    assert card["isin"] == NEW_BOND and card["price_pct"] == "101.2"

    # второй расчёт за тот же день берёт снимок из БД
    second = (await calculate(api, ws_id, h, rev)).json()
    assert second["results"][0]["metric_value"] == row["metric_value"]
    assert len(market.calls) == 2


async def test_old_snapshot_is_used_when_source_is_down(api, sources, engine):
    insert_snapshot(engine, insert_instrument(engine, FIXED), data_as_of=date(2026, 10, 15))
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    calc = (await calculate(api, ws_id, h, rev)).json()
    row = calc["results"][0]
    assert calc["status"] == "success"
    assert row["data_as_of"] == "2026-10-15"  # пользователь видит, что данные не за сегодня
    market, _ = sources
    assert market.calls == [f"snapshot:{FIXED}"]  # сначала спросили источник


@pytest.mark.parametrize(
    ("mode", "code"), [("crash", "PROVIDER_UNAVAILABLE"), ("future", "INCOMPATIBLE_MARKET_DATA")]
)
async def test_bad_source_answer_becomes_row_error(api, sources, engine, mode, code):
    market, _ = sources
    market.mode = mode
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    r = await calculate(api, ws_id, h, rev)
    assert r.status_code == 201  # не 500
    row = r.json()["results"][0]
    assert (row["status"], row["error_code"]) == ("failed", code)
    with engine.connect() as conn:  # чужие данные не сохранены
        assert conn.execute(text("SELECT count(*) FROM market_data_snapshots")).scalar_one() == 0


async def test_snapshot_after_valuation_date_is_ignored(api, sources, engine):
    insert_snapshot(engine, insert_instrument(engine, FIXED), data_as_of=date(2026, 10, 25))
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert row["error_code"] == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("snapshot", "code"),
    [
        ({"duration_mod": None}, "MISSING_MARKET_DATA"),  # фикс. купон без дюрации
        ({"price_pct": None}, "MISSING_MARKET_DATA"),
        ({"accrued_interest": None}, "MISSING_MARKET_DATA"),  # 0 вместо НКД не подставляем
        ({"currency": "EUR"}, "INCOMPATIBLE_MARKET_DATA"),  # в справочнике RUB
    ],
)
async def test_row_errors_from_market_data(api, sources, engine, snapshot, code):
    insert_snapshot(engine, insert_instrument(engine, FIXED), **snapshot)
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert (row["status"], row["error_code"], row["metric_value"]) == ("failed", code, None)


async def test_zero_accrued_interest_is_valid(api, sources, engine):
    """НКД = 0 (день выплаты купона) это данные, а не их отсутствие"""
    insert_snapshot(engine, insert_instrument(engine, FIXED), accrued_interest=0)
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert row["status"] == "success" and Decimal(row["current_fair_value"]) == Decimal("95000")


async def test_missing_fx_rate(api, sources, engine):
    insert_snapshot(engine, insert_instrument(engine, USD_BOND, "USD"), currency="USD")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert row["error_code"] == "MISSING_FX_RATE"

    _, fx = sources
    fx.rates["USD"] = "90"  # источник курсов ответил: курс сохраняется и используется
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert row["status"] == "success" and Decimal(row["fx_rate_used"]) == 90


async def test_valuation_date_in_future_is_rejected(api, market_in_db, sources):
    ws_id, h, rev = await workspace_with(api, (FIXED, 1))
    tomorrow = date.fromordinal(date.today().toordinal() + 1)
    r = await calculate(api, ws_id, h, rev, valuation_date=tomorrow)
    assert (r.status_code, r.json()["details"][0]["field"]) == (422, "body.valuation_date")


async def test_valuation_date_defaults_to_today(api, sources, engine):
    insert_snapshot(engine, insert_instrument(engine, FIXED), data_as_of=date.today())
    ws_id, h, rev = await workspace_with(api, (FIXED, 1))
    calc = (await calculate(api, ws_id, h, rev, valuation_date=None)).json()
    assert calc["valuation_date"] == date.today().isoformat()


# --- транзакции и revision -------------------------------------------------------------


async def test_no_open_transaction_during_external_request(api, sources, engine):
    market, _ = sources
    seen = []

    def check():
        with engine.connect() as conn:
            seen.append(
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity"
                        " WHERE datname = current_database() AND state LIKE 'idle in transaction%'"
                    )
                ).scalar_one()
            )

    market.on_call = check
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
    assert (await calculate(api, ws_id, h, rev)).status_code == 201
    assert seen and all(count == 0 for count in seen)


async def test_stale_revision(api, market_in_db, sources, engine):
    ws_id, h, rev = await workspace_with(api, (FIXED, 1))
    r = await calculate(api, ws_id, h, rev - 1)
    assert (r.status_code, r.json()["code"]) == (409, "REVISION_CONFLICT")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM calculation_runs")).scalar_one() == 0


async def test_workspace_changed_during_external_request(api, sources, engine):
    market, _ = sources
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))

    def change_workspace():  # кто-то меняет набор, пока ждём источник
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE workspaces SET revision = revision + 1 WHERE id = :id"), {"id": ws_id}
            )

    market.on_call = change_workspace
    r = await calculate(api, ws_id, h, rev)
    assert (r.status_code, r.json()["code"]) == (409, "REVISION_CONFLICT")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM calculation_runs")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM instruments")).scalar_one() == 0


async def test_empty_workspace(api, sources):
    ws_id, h = await open_workspace(api)
    r = await calculate(api, ws_id, h, 0)
    assert (r.status_code, r.json()["code"]) == (422, "WORKSPACE_EMPTY")


# --- доступ ------------------------------------------------------------------------


async def test_access_to_calculations(api, market_in_db, sources):
    ws_id, h, rev = await workspace_with(api, (FIXED, 1))
    calc_id = (await calculate(api, ws_id, h, rev)).json()["id"]

    r = await calculate(api, ws_id, {}, rev)
    assert (r.status_code, r.json()["code"]) == (403, "CSRF_TOKEN_INVALID")

    async with second_client() as stranger:
        r = await stranger.get(f"/api/v1/calculations/{calc_id}")
        assert (r.status_code, r.json()["code"]) == (401, "SESSION_REQUIRED")
        _, stranger_h = await open_workspace(stranger)
        r = await stranger.get(f"/api/v1/calculations/{calc_id}")
        assert (r.status_code, r.json()["code"]) == (404, "CALCULATION_NOT_FOUND")
        r = await calculate(stranger, ws_id, stranger_h, rev)
        assert (r.status_code, r.json()["code"]) == (404, "WORKSPACE_NOT_FOUND")

    r = await api.get(f"/api/v1/calculations/{uuid.uuid4()}")
    assert r.json()["code"] == "CALCULATION_NOT_FOUND"


def test_default_sources_are_unavailable_stubs(monkeypatch):
    """Без доступа к Cbonds живой API берёт данные только из БД"""
    from app.stubs.sources import NoMarketDataSource

    # пустое значение: .env разработчика с логином Cbonds его не перезапишет
    monkeypatch.setenv("CBONDS_LOGIN", "")
    assert isinstance(get_market_data(), NoMarketDataSource)
    assert get_risk_calculator() is calc_dv01


# --- история: старые результаты не меняются задним числом ----------------------


RESULT_COLUMNS = (
    "id, trade_id, market_data_snapshot_id, fx_rate_id, input_isin, input_quantity,"
    " nominal_used, price_pct_used, accrued_interest_used, fx_rate_used, current_fair_value,"
    " duration_used, convexity_used, metric_value, status, error_code"
)


def stored_rows(engine: Engine, run_id: str) -> list:
    with engine.connect() as conn:
        return conn.execute(
            text(
                f"SELECT {RESULT_COLUMNS} FROM calculation_results"
                " WHERE run_id = :id ORDER BY input_isin"
            ),
            {"id": run_id},
        ).all()


def stored_run(engine: Engine, run_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM calculation_runs WHERE id = :id"), {"id": run_id}
        ).one()


async def test_old_result_does_not_change_after_anything(api, market_in_db, sources, engine):
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (FLOATER, 299), (USD_BOND, -10))
    old = (await calculate(api, ws_id, h, rev)).json()
    rows_before = stored_rows(engine, old["id"])
    run_before = stored_run(engine, old["id"])

    # меняем всё, что можно: сделки, рынок, курс, считаем заново
    trades = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()["trades"]
    await api.patch(
        trades_url(ws_id, trades[0]["id"]), json={"quantity": 1, "expected_revision": 3}, headers=h
    )
    await api.delete(trades_url(ws_id, trades[1]["id"]), params={"expected_revision": 4}, headers=h)
    with engine.begin() as conn:
        instrument_id = conn.execute(
            text("SELECT id FROM instruments WHERE isin = :isin"), {"isin": FIXED}
        ).scalar_one()
    insert_snapshot(engine, instrument_id, data_as_of=date(2026, 10, 21), price_pct=80)
    new = (await calculate(api, ws_id, h, 5, valuation_date=date(2026, 10, 21))).json()
    assert new["results"][0]["price_pct_used"] == "80"

    # старый расчёт тот же: и в API, и строка в строку в БД
    again = (await api.get(f"/api/v1/calculations/{old['id']}")).json()

    def without_trade_id(calc: dict) -> list[dict]:
        return sorted(({**r, "trade_id": None} for r in calc["results"]), key=lambda r: r["isin"])

    assert without_trade_id(again) == without_trade_id(old)
    assert again["total_value"] == old["total_value"]
    rows_after = stored_rows(engine, old["id"])
    assert [{**r._asdict(), "trade_id": None} for r in rows_after] == [
        {**r._asdict(), "trade_id": None} for r in rows_before
    ]
    deleted = {r.input_isin: r.trade_id for r in rows_after}
    assert deleted[FLOATER] is None  # ссылка на удалённую сделку обнулилась, данные остались
    assert stored_run(engine, old["id"]) == run_before


async def test_stored_result_can_be_recomputed(api, market_in_db, sources, engine):
    """По сохранённым входам и параметрам формула даёт ровно сохранённый результат"""
    ws_id, h, rev = await workspace_with(api, (FIXED, 100), (FLOATER, -299), (USD_BOND, 7))
    calc = (await calculate(api, ws_id, h, rev)).json()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT r.*, s.coupon_type, s.coupon_frequency, s.duration_mod, s.convexity"
                " FROM calculation_results r"
                " JOIN market_data_snapshots s ON s.id = r.market_data_snapshot_id"
                " WHERE r.run_id = :id"
            ),
            {"id": calc["id"]},
        ).all()
    assert len(rows) == 3
    total = Decimal(0)
    for row in rows:
        again = calc_dv01(
            Dv01Input(
                quantity=int(row.input_quantity),
                nominal=row.nominal_used,
                price_pct=row.price_pct_used,
                accrued_interest=row.accrued_interest_used,
                fx_rate=row.fx_rate_used,
                duration_mod=row.duration_mod,
                convexity=row.convexity,
                coupon_type=CouponType(row.coupon_type),
                coupon_frequency=row.coupon_frequency,
            )
        )
        assert again.current_fair_value == row.current_fair_value
        assert again.duration_used == row.duration_used
        assert again.convexity_used == row.convexity_used
        assert again.dv01 == row.metric_value
        total += row.metric_value
    assert Decimal(calc["total_value"]) == total
    assert calc["valuation_date"] == DAY.isoformat()


async def test_used_market_data_cannot_be_deleted(api, market_in_db, sources, engine):
    """Снимок и курс, на которые ссылается расчёт, БД удалить не даст"""
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "success"
    for table in ("market_data_snapshots", "fx_rates"):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {table}"))
