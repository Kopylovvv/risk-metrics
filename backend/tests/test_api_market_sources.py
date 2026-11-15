"""Источники новых расчётов: Мосбиржа и курсы ЦБ, происхождение и даты данных (S3-10)"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from app import contracts
from app.api.deps import get_fx_rates, get_market_data, get_risk_calculator
from app.enums import CouponType, Provider, RowErrorCode
from app.risk_core import calc_dv01
from app.stubs.sources import NoFxRateSource, NoMarketDataSource
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

YESTERDAY = date(2026, 10, 19)


def insert_rate(engine: Engine, currency: str, rate: str, on: date, source: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO fx_rates (id, currency, rate_date, rate_to_rub, source, retrieved_at)"
                " VALUES (:id, :c, :d, :r, :s, now())"
            ),
            {"id": uuid.uuid4(), "c": currency, "d": on, "r": rate, "s": source},
        )


class Moex:
    """Подставной адаптер Мосбиржи: NEW_BOND в рублях, номинал USD у USD_BOND, расчёты в RUB"""

    provider = Provider.MOEX

    def __init__(self):
        self.calls: list[str] = []
        self.down = False

    async def get_instrument(self, isin: str) -> contracts.InstrumentInfo:
        self.calls.append(f"instrument:{isin}")
        if self.down:
            raise contracts.MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE)
        if isin not in (NEW_BOND, USD_BOND):
            raise contracts.MarketDataError(RowErrorCode.INSTRUMENT_NOT_FOUND)
        currency = "USD" if isin == USD_BOND else "RUB"
        return contracts.InstrumentInfo(isin, f"Облигация {isin}", "bond", currency)

    async def get_snapshot(self, isin: str, as_of: date) -> contracts.MarketSnapshot:
        self.calls.append(f"snapshot:{isin}")
        if self.down:
            raise contracts.MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE)
        usd = isin == USD_BOND
        return contracts.MarketSnapshot(
            isin=isin,
            provider=Provider.MOEX,
            data_as_of=as_of,
            retrieved_at=datetime.now(UTC),
            currency="USD" if usd else "RUB",
            settlement_currency="RUB",  # у замещающих облигаций номинал USD, расчёты в рублях
            nominal=Decimal(1000),
            price_pct=Decimal("98.5") if usd else Decimal("101.2"),
            accrued_interest=Decimal("10.25") if usd else Decimal("3.1"),
            duration_mod=Decimal("4.2") if usd else Decimal(2),
            convexity=Decimal(25) if usd else Decimal(5),
            coupon_type=CouponType.FIXED,
            coupon_frequency=2,
        )


class Cbr:
    """Подставные курсы ЦБ: USD = 92.5, по запросу недоступен"""

    source = "cbr"

    def __init__(self):
        self.calls: list[str] = []
        self.mode = "ok"  # down: не отвечает; crash: падает; none: курса нет

    async def get_rate_to_rub(self, currency: str, on: date) -> contracts.FxRate:
        self.calls.append(currency)
        if self.mode == "down":
            raise contracts.MarketDataError(RowErrorCode.FX_PROVIDER_UNAVAILABLE)
        if self.mode == "crash":
            raise TimeoutError("ЦБ не ответил")
        if self.mode == "none":
            raise contracts.MarketDataError(RowErrorCode.MISSING_FX_RATE)
        return contracts.FxRate.per_unit(currency, on, Decimal("92.5"), 1)


@pytest.fixture
def moex_cbr():
    from app.main import app

    market, fx = Moex(), Cbr()
    app.dependency_overrides[get_market_data] = lambda: market
    app.dependency_overrides[get_fx_rates] = lambda: fx
    app.dependency_overrides[get_risk_calculator] = lambda: calc_dv01
    return market, fx


# --- происхождение данных в ответе ---------------------------------------------------


async def test_rows_show_source_and_dates_of_quote_and_fx(api, moex_cbr):
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 100), (USD_BOND, -10))
    calc = (await calculate(api, ws_id, h, rev)).json()
    assert calc["status"] == "success"
    rows = by_isin(calc)

    rub = rows[NEW_BOND]
    assert (rub["provider"], rub["data_as_of"]) == ("moex", "2026-10-20")
    assert (rub["currency"], rub["settlement_currency"]) == ("RUB", "RUB")
    # рублёвой бумаге курс не нужен: источника и даты курса нет, курс 1
    assert (rub["fx_source"], rub["fx_rate_date"], rub["fx_rate_used"]) == (None, None, "1")

    usd = rows[USD_BOND]
    assert (usd["provider"], usd["data_as_of"]) == ("moex", "2026-10-20")
    # номинал в долларах, расчёты в рублях: курс ЦБ берётся для валюты номинала
    assert (usd["currency"], usd["settlement_currency"]) == ("USD", "RUB")
    assert (usd["fx_source"], usd["fx_rate_date"]) == ("cbr", "2026-10-20")
    # −10 × (1000 × 0.985 + 10.25) × 92.5 = −920 606.25
    assert Decimal(usd["current_fair_value"]) == Decimal("-920606.25")
    assert Decimal(usd["metric_value"]) == Decimal("386.53954921875")

    # то же происхождение у сохранённого расчёта и в карточке инструмента
    again = (await api.get(f"/api/v1/calculations/{calc['id']}")).json()
    assert again == calc
    card = (await api.get(f"/api/v1/instruments/{USD_BOND}")).json()
    assert (card["provider"], card["currency"], card["settlement_currency"]) == (
        "moex",
        "USD",
        "RUB",
    )


async def test_test_source_is_distinguishable(api, engine, moex_cbr):
    """Контрольный набор виден в ответе как test, курс как demo"""
    market, fx = moex_cbr
    market.down = True
    fx.mode = "down"
    instrument_id = insert_instrument(engine, USD_BOND, "USD")
    insert_snapshot(engine, instrument_id, provider="test", currency="USD")
    insert_rate(engine, "USD", "90", DAY, "demo")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert (row["status"], row["provider"], row["fx_source"]) == ("success", "test", "demo")


# --- новые расчёты: Мосбиржа и ЦБ, а не Cbonds и RuData ---------------------------------


@pytest.mark.parametrize("old_provider", ["cbonds", "rudata"])
async def test_saved_cbonds_and_rudata_snapshots_are_not_used(api, engine, moex_cbr, old_provider):
    market, _ = moex_cbr
    market.down = True
    insert_snapshot(engine, insert_instrument(engine, FIXED), provider=old_provider)
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    calc = (await calculate(api, ws_id, h, rev)).json()
    row = calc["results"][0]
    # сохранённый снимок старого источника есть, но новый расчёт его не берёт
    assert (calc["status"], row["error_code"], row["provider"]) == (
        "failed",
        "PROVIDER_UNAVAILABLE",
        None,
    )
    card = (await api.get(f"/api/v1/instruments/{FIXED}")).json()
    assert card["provider"] is None and card["price_pct"] is None


async def test_moex_wins_over_test_snapshot_of_same_day(api, engine, moex_cbr):
    market, _ = moex_cbr
    instrument_id = insert_instrument(engine, FIXED)
    insert_snapshot(engine, instrument_id, provider="test", price_pct=90)
    insert_snapshot(engine, instrument_id, provider="moex", price_pct=95)
    ws_id, h, rev = await workspace_with(api, (FIXED, 100))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert (row["provider"], row["price_pct_used"]) == ("moex", "95")
    assert market.calls == []  # снимок Мосбиржи за дату уже есть: в источник не ходим


async def test_moex_is_asked_when_only_test_data_of_the_day_is_saved(api, engine, moex_cbr):
    market, _ = moex_cbr
    insert_snapshot(engine, insert_instrument(engine, NEW_BOND), provider="test", price_pct=90)
    ws_id, h, rev = await workspace_with(api, (NEW_BOND, 100))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert market.calls == [f"snapshot:{NEW_BOND}"]
    assert (row["provider"], row["price_pct_used"]) == ("moex", "101.2")

    market.down = True  # снимок Мосбиржи сохранён: второй расчёт за ту же дату без запроса
    again = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert (again["provider"], again["price_pct_used"]) == ("moex", "101.2")
    assert len(market.calls) == 1


# --- курсы ЦБ ---------------------------------------------------------------------------


def test_cbr_rate_is_normalized_to_one_unit():
    # ЦБ публикует курс иены за 100 единиц: 60.5 рубля за 100 JPY = 0.605 за 1
    rate = contracts.FxRate.per_unit("JPY", DAY, Decimal("60.5"), 100)
    assert (rate.rate_to_rub, rate.source) == (Decimal("0.605"), "cbr")
    assert contracts.FxRate.per_unit("USD", DAY, Decimal("92.5"), 1).rate_to_rub == Decimal("92.5")
    for value, nominal in ((Decimal("60.5"), 0), (Decimal(0), 1)):
        with pytest.raises(contracts.MarketDataError) as exc:
            contracts.FxRate.per_unit("JPY", DAY, value, nominal)
        assert exc.value.code == RowErrorCode.INCOMPATIBLE_MARKET_DATA


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("none", "MISSING_FX_RATE"),  # ЦБ ответил, курса нет
        ("down", "FX_PROVIDER_UNAVAILABLE"),  # источник курсов не ответил
        ("crash", "FX_PROVIDER_UNAVAILABLE"),  # сбой адаптера курсов
    ],
)
async def test_missing_rate_differs_from_unavailable_fx_source(api, engine, moex_cbr, mode, code):
    _, fx = moex_cbr
    fx.mode = mode
    insert_snapshot(engine, insert_instrument(engine, USD_BOND, "USD"), currency="USD")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1), (FIXED, 1))
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    calc = (await calculate(api, ws_id, h, rev)).json()
    rows = by_isin(calc)
    assert (rows[USD_BOND]["error_code"], rows[USD_BOND]["metric_value"]) == (code, None)
    assert rows[FIXED]["status"] == "success"  # рублёвой бумаге курс не нужен
    assert calc["status"] == "partial" and calc["total_value"] is None


async def test_old_rate_is_used_with_its_date_when_cbr_is_down(api, engine, moex_cbr):
    _, fx = moex_cbr
    fx.mode = "down"
    insert_snapshot(engine, insert_instrument(engine, USD_BOND, "USD"), currency="USD")
    insert_rate(engine, "USD", "91", YESTERDAY, "cbr")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert row["status"] == "success"
    assert (row["fx_rate_used"], row["fx_source"], row["fx_rate_date"]) == (
        "91",
        "cbr",
        YESTERDAY.isoformat(),
    )


async def test_cbr_rate_wins_over_demo_of_same_day(api, engine, moex_cbr):
    _, fx = moex_cbr
    insert_snapshot(engine, insert_instrument(engine, USD_BOND, "USD"), currency="USD")
    insert_rate(engine, "USD", "90", DAY, "demo")
    insert_rate(engine, "USD", "92.5", DAY, "cbr")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    row = (await calculate(api, ws_id, h, rev)).json()["results"][0]
    assert (row["fx_rate_used"], row["fx_source"]) == ("92.5", "cbr")
    assert fx.calls == []  # курс ЦБ за дату уже сохранён


# --- история: новые загрузки не меняют сохранённый расчёт ---------------------------------


async def test_new_quote_and_rate_do_not_change_saved_calculation(api, engine, moex_cbr):
    market, fx = moex_cbr
    market.down = True
    fx.mode = "down"
    instrument_id = insert_instrument(engine, USD_BOND, "USD")
    insert_snapshot(engine, instrument_id, provider="test", currency="USD", price_pct="98.5")
    insert_rate(engine, "USD", "90", DAY, "demo")
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1))
    old = (await calculate(api, ws_id, h, rev)).json()
    assert (old["results"][0]["provider"], old["results"][0]["fx_source"]) == ("test", "demo")

    # позже пришли данные Мосбиржи и курс ЦБ за тот же день: новый расчёт берёт их
    market.down = False
    fx.mode = "ok"
    new = (await calculate(api, ws_id, h, rev)).json()
    assert (new["results"][0]["provider"], new["results"][0]["fx_source"]) == ("moex", "cbr")
    assert new["results"][0]["fx_rate_used"] == "92.5"

    # а старый расчёт тот же: свои источник, даты, курс и числа
    again = (await api.get(f"/api/v1/calculations/{old['id']}")).json()
    assert again == old
    with engine.connect() as conn:
        sources = conn.execute(
            text("SELECT provider FROM market_data_snapshots ORDER BY provider")
        ).scalars()
        assert list(sources) == ["moex", "test"]  # снимки разных источников хранятся рядом


def test_default_sources_are_moex_and_cbr(monkeypatch):
    """В deps.py подключены адаптеры Мосбиржи и ЦБ (не Cbonds); каждый запрос: новый объект"""
    from app.market_data import CbrFxRates, MoexMarketData

    monkeypatch.setenv("EXTERNAL_SOURCES", "on")
    market, fx = get_market_data(), get_fx_rates()
    assert isinstance(market, MoexMarketData) and market.provider == Provider.MOEX
    assert isinstance(fx, CbrFxRates) and fx.source == "cbr"
    assert get_market_data() is not market


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false"])
def test_external_sources_can_be_switched_off(monkeypatch, value):
    """Без сети и в тестах: заглушки, расчёт берёт данные только из БД"""
    monkeypatch.setenv("EXTERNAL_SOURCES", value)
    market, fx = get_market_data(), get_fx_rates()
    assert isinstance(market, NoMarketDataSource) and market.provider is None
    assert isinstance(fx, NoFxRateSource) and fx.source is None


async def test_placeholder_fx_source_is_reported_as_unavailable(api, engine):
    """Без адаптера ЦБ и без сохранённого курса строка получает FX_PROVIDER_UNAVAILABLE"""
    insert_snapshot(engine, insert_instrument(engine, USD_BOND, "USD"), currency="USD")
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    ws_id, h, rev = await workspace_with(api, (USD_BOND, 1), (FIXED, 1))
    rows = by_isin((await calculate(api, ws_id, h, rev)).json())
    assert rows[USD_BOND]["error_code"] == "FX_PROVIDER_UNAVAILABLE"
    assert rows[FIXED]["status"] == "success"
