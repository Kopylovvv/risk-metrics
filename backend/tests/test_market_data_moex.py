"""Адаптер Мосбиржи без сети: записанные ответы ISS через httpx.MockTransport

Фикстуры: tests/fixtures/moex (реальные ответы от 15.11.2026, описание в README там же)
Подставной сервер отдаёт строки истории в пределах from/till запроса и сам режет страницы
по start/limit, поэтому проверяется и выбор даты, и постраничная загрузка

Ожидаемые D_mod и C посчитаны отдельно от адаптера по формулам из bond_math.py и
сверены с биржей: дюрация по потокам 2447.24 дня (ISS 2447) у ОФЗ 26238 и 2038.68 дня
(ISS 2038) у ГазКЗ-34Д; D_mod по DURATION из ISS: 2447/365/1.162331 = 5.76781,
2038/360/1.09399 = 5.17474
"""

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.contracts import MarketDataError
from app.enums import CouponType, Provider, RowErrorCode
from app.market_data.moex import MoexMarketData

FIXTURES = Path(__file__).parent / "fixtures" / "moex"
FRIDAY = date(2026, 11, 13)
SUNDAY = date(2026, 11, 15)

OFZ = "RU000A1038V6"  # ОФЗ 26238, SECID SU26238RMFS4
USD_BOND = "RU000A105A95"  # ГазКЗ-34Д, номинал USD
FLOATER = "RU000A105B11"  # ОФЗ 29021, SECID SU29021RMFS1
SHARE = "RU0009029540"  # Сбербанк ао
UNKNOWN = "XS9999999998"

SECID = {OFZ: "SU26238RMFS4", USD_BOND: "RU000A105A95", FLOATER: "SU29021RMFS1"}


def load(name: str) -> dict:
    # числа сразу в Decimal, как их читает адаптер
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"), parse_float=Decimal)


def dumps(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, default=str).encode()


class FakeIss:
    """Подставной ISS: отвечает записанными ответами по пути запроса"""

    def __init__(self, page_size: int = 100):
        self.page_size = page_size
        self.files: dict[str, dict] = {}
        for path in FIXTURES.glob("*.json"):
            self.files[path.stem] = load(path.name)
        self.requests: list[tuple[str, dict]] = []
        self.status: dict[str, int] = {}  # вид запроса → HTTP-статус ответа
        self.raw: dict[str, bytes] = {}  # вид запроса → тело ответа как есть
        self.error: dict[str, Exception] = {}
        self.filter_dates = True

    def edit(self, name: str) -> dict:
        """Изменяемая копия ответа для отдельного теста"""
        self.files[name] = copy.deepcopy(self.files[name])
        return self.files[name]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/iss/")
        params = dict(request.url.params)
        self.requests.append((path, params))
        kind, name = self._route(path, params)
        if kind in self.error:
            raise self.error[kind]
        if kind in self.raw:
            return httpx.Response(self.status.get(kind, 200), content=self.raw[kind])
        if kind in self.status:
            return httpx.Response(self.status[kind], content=b"Server error")
        body = copy.deepcopy(self.files.get(name) or self._empty(kind))
        start = int(params.get("start", 0))
        limit = min(int(params.get("limit", 100)), self.page_size)
        for block, table in list(body.items()):
            if block.endswith(".cursor") or not isinstance(table, dict):
                continue
            rows = table["data"]
            if self.filter_dates and "from" in params and rows and "TRADEDATE" in table["columns"]:
                i = table["columns"].index("TRADEDATE")
                rows = [r for r in rows if params["from"] <= r[i] <= params["till"]]
            if f"{block}.cursor" in body or self.page_size < 100:
                table["data"] = rows[start : start + limit]
                body[f"{block}.cursor"] = {
                    "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                    "data": [[start, len(rows), limit]],
                }
            else:
                table["data"] = rows
        return httpx.Response(200, content=dumps(body))

    @staticmethod
    def _route(path: str, params: dict) -> tuple[str, str]:
        secid = path.rsplit("/", 1)[-1].removesuffix(".json")
        if path == "securities.json":
            return "search", f"search_{params.get('q')}"
        if path.startswith("securities/"):
            return "description", f"description_{secid}"
        if "/yields/" in path:
            return "yields", f"yields_{secid}"
        if path.startswith("history/"):
            return "history", f"history_{secid}"
        if "/bondization/" in path:
            return "bondization", f"bondization_{secid}"
        raise AssertionError(f"неожиданный запрос {path}")

    @staticmethod
    def _empty(kind: str) -> dict:
        block = {"search": "securities", "description": "description"}.get(kind, kind)
        return {block: {"columns": ["name", "value"], "data": []}}

    def kinds(self) -> list[str]:
        return [self._route(path, params)[0] for path, params in self.requests]

    def params(self, kind: str) -> list[dict]:
        """Параметры запросов одного вида по порядку"""
        return [p for path, p in self.requests if self._route(path, p)[0] == kind]

    def source(self) -> MoexMarketData:
        return MoexMarketData("https://iss.test/iss/", httpx.MockTransport(self.handler))


async def snapshot(isin: str, as_of: date = FRIDAY, fake: FakeIss | None = None):
    return await (fake or FakeIss()).source().get_snapshot(isin, as_of)


async def error_code(isin: str, fake: FakeIss, as_of: date = FRIDAY) -> RowErrorCode:
    with pytest.raises(MarketDataError) as exc:
        await snapshot(isin, as_of, fake)
    return exc.value.code


def q12(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1e-12"))


# --- рублёвый выпуск с фиксированным купоном -----------------------------------------------


async def test_ofz_snapshot_on_trading_day():
    snap = await snapshot(OFZ)
    assert snap.isin == OFZ and snap.provider == Provider.MOEX
    assert snap.data_as_of == FRIDAY
    assert (snap.currency, snap.settlement_currency) == ("RUB", "RUB")  # SUR → RUB
    assert snap.nominal == Decimal(1000)
    assert snap.price_pct == Decimal("51.577")  # LEGALCLOSEPRICE в %, без деления на 100
    assert snap.accrued_interest == Decimal("31.7")  # ACCINT на дату торгов
    assert (snap.coupon_type, snap.coupon_frequency) == (CouponType.FIXED, 2)
    assert q12(snap.duration_mod) == Decimal("5.768382680528")
    assert q12(snap.convexity) == Decimal("58.553339735139")
    # с биржей: D_mod по DURATION 2447 дней, отличие 0.01% (DURATION округлена до дня)
    assert abs(snap.duration_mod - Decimal("5.767814")) < Decimal("0.001")


async def test_weekend_valuation_takes_last_trading_day():
    """На воскресенье 15.11 берутся итоги пятницы 13.11, дата в data_as_of"""
    fake = FakeIss()
    snap = await snapshot(OFZ, SUNDAY, fake)
    assert (snap.data_as_of, snap.price_pct) == (FRIDAY, Decimal("51.577"))
    history, yields = fake.params("history")[0], fake.params("yields")[0]
    assert (history["from"], history["till"]) == ("2026-11-01", "2026-11-15")  # 14 дней
    assert (yields["from"], yields["till"]) == ("2026-11-13", "2026-11-13")


async def test_earlier_valuation_date_uses_that_day():
    snap = await snapshot(OFZ, date(2026, 11, 12))
    assert (snap.data_as_of, snap.price_pct, snap.accrued_interest) == (
        date(2026, 11, 12),
        Decimal("51.578"),
        Decimal("31.51"),
    )


async def test_instrument_info_and_one_lookup_per_isin():
    fake = FakeIss()
    source = fake.source()
    info = await source.get_instrument(OFZ)
    assert (info.isin, info.name, info.instrument_type, info.currency) == (
        OFZ,
        "ОФЗ-ПД 26238 15/05/2041",
        "bond",
        "RUB",
    )
    await source.get_snapshot(OFZ, FRIDAY)
    assert fake.kinds().count("search") == 1 and fake.kinds().count("description") == 1


# --- валютный выпуск --------------------------------------------------------------------------


async def test_usd_bond_takes_accrued_interest_in_face_currency():
    """НКД из TQOD (3.59 $), а не из TQCB (297.8993 ₽ = 3.59 $ по курсу ЦБ 82.9803)"""
    snap = await snapshot(USD_BOND)
    assert (snap.currency, snap.settlement_currency) == ("USD", "RUB")  # основной режим TQCB
    assert snap.accrued_interest == Decimal("3.59")
    assert snap.price_pct == Decimal("96.2999")
    assert snap.nominal == Decimal(1000)


async def test_usd_bond_duration_on_iss_year_basis_360():
    """ISS дисконтирует этот выпуск по базе 360: при 365 D_mod было бы 5.112, на 1.2% меньше"""
    snap = await snapshot(USD_BOND)
    assert q12(snap.duration_mod) == Decimal("5.176459212386")
    assert q12(snap.convexity) == Decimal("36.691611768865")
    assert abs(snap.duration_mod - Decimal("5.174738")) < Decimal("0.005")


async def test_usd_bond_without_face_currency_board_is_incompatible():
    """Режима в долларах нет: НКД только в рублях, сами не пересчитываем"""
    fake = FakeIss()
    history = fake.edit("history_RU000A105A95")["history"]
    history["data"] = [r for r in history["data"] if r[2] != "TQOD"]
    assert await error_code(USD_BOND, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA


async def test_price_taken_from_any_board_of_the_date():
    """В TQOD цены нет: цена % номинала одна для всех режимов, берём из TQCB за ту же дату"""
    fake = FakeIss()
    history = fake.edit("history_RU000A105A95")["history"]
    for row in history["data"]:
        if row[2] == "TQOD":
            row[3] = None  # LEGALCLOSEPRICE
    snap = await snapshot(USD_BOND, fake=fake)
    assert (snap.price_pct, snap.accrued_interest) == (Decimal("96.2999"), Decimal("3.59"))


# --- флоатер ----------------------------------------------------------------------------------


async def test_floater_has_frequency_and_no_bondization_request():
    fake = FakeIss()
    snap = await snapshot(FLOATER, fake=fake)
    assert (snap.coupon_type, snap.coupon_frequency) == (CouponType.FLOATING, 4)
    # D = 1/4 и C = 0 подставит risk_core по правилу ТЗ
    assert (snap.duration_mod, snap.convexity) == (None, None)
    assert (snap.price_pct, snap.accrued_interest) == (Decimal("98.239"), Decimal("27.56"))
    assert "bondization" not in fake.kinds() and "yields" not in fake.kinds()


# --- нет выпуска и нет данных -----------------------------------------------------------------


@pytest.mark.parametrize("isin", [UNKNOWN, SHARE])
async def test_unknown_isin_or_share_is_not_found(isin):
    fake = FakeIss()
    assert await error_code(isin, fake) == RowErrorCode.INSTRUMENT_NOT_FOUND
    with pytest.raises(MarketDataError) as exc:
        await fake.source().get_instrument(isin)
    assert exc.value.code == RowErrorCode.INSTRUMENT_NOT_FOUND


async def test_no_trading_within_two_weeks_is_missing_not_zero():
    assert await error_code(OFZ, FakeIss(), date(2026, 11, 10)) == RowErrorCode.MISSING_MARKET_DATA
    assert await error_code(OFZ, FakeIss(), date(2026, 12, 31)) == RowErrorCode.MISSING_MARKET_DATA


async def test_no_price_is_missing():
    fake = FakeIss()
    for row in fake.edit("history_SU26238RMFS4")["history"]["data"]:
        row[3] = row[4] = None
    assert await error_code(OFZ, fake) == RowErrorCode.MISSING_MARKET_DATA


async def test_missing_accrued_interest_stays_none():
    fake = FakeIss()
    for row in fake.edit("history_SU26238RMFS4")["history"]["data"]:
        row[5] = None
    snap = await snapshot(OFZ, fake=fake)
    assert snap.accrued_interest is None  # строка получит MISSING_MARKET_DATA в расчёте


# --- дюрация и выпуклость не выдумываются -------------------------------------------------------


async def test_duration_mismatch_with_iss_gives_none():
    fake = FakeIss()
    for row in fake.edit("yields_SU26238RMFS4")["history_yields"]["data"]:
        row[8] = 2300  # DURATION
    snap = await snapshot(OFZ, fake=fake)
    assert (snap.duration_mod, snap.convexity) == (None, None)
    assert snap.price_pct == Decimal("51.577")  # остальное сохраняется


async def test_unknown_future_coupon_gives_none():
    fake = FakeIss()
    coupons = fake.edit("bondization_SU26238RMFS4")["coupons"]["data"]
    coupons[-1][1] = None
    snap = await snapshot(OFZ, fake=fake)
    assert (snap.duration_mod, snap.convexity) == (None, None)


async def test_no_yield_row_gives_none():
    fake = FakeIss()
    fake.edit("yields_SU26238RMFS4")["history_yields"]["data"] = []
    snap = await snapshot(OFZ, fake=fake)
    assert (snap.duration_mod, snap.convexity) == (None, None)


async def test_yield_to_offer_redeems_outstanding_nominal_on_offer_date():
    """Биржа считает к оферте 30.05.2029: купоны до неё и номинал 1000 в дату оферты

    Отдельно посчитано при базе 365: цена по таким потокам 859.11 (PRICE 82.682 + НКД 32.29),
    дюрация 824.45 дня, D_mod 1.943306, C 5.779199. По дюрации, округлённой до дня, базу
    не отличить (при 360 тоже около 824), поэтому база выбирается по цене
    """
    fake = FakeIss()
    for row in fake.edit("yields_SU26238RMFS4")["history_yields"]["data"]:
        row[3], row[4], row[5], row[8] = "2029-05-30", "OFFERDATE", Decimal("82.682"), 824
    snap = await snapshot(OFZ, fake=fake)
    assert q12(snap.duration_mod) == Decimal("1.943305605265")
    assert q12(snap.convexity) == Decimal("5.779199240711")


# --- постраничные ответы ------------------------------------------------------------------------


async def test_paged_responses_are_loaded_completely():
    """У ОФЗ 26238 40 купонов: при странице 20 нужны два запроса графика (start=0, start=20)"""
    whole = await snapshot(OFZ)
    fake = FakeIss(page_size=20)
    paged = await snapshot(OFZ, fake=fake)
    assert (paged.duration_mod, paged.convexity) == (whole.duration_mod, whole.convexity)
    assert [p["start"] for p in fake.params("bondization")] == ["0", "20"]


async def test_paged_history_is_loaded_completely():
    fake = FakeIss(page_size=2)
    snap = await snapshot(USD_BOND, fake=fake)  # 6 строк истории: 3 страницы
    assert (snap.data_as_of, snap.accrued_interest) == (FRIDAY, Decimal("3.59"))
    assert [p["start"] for p in fake.params("history")] == ["0", "2", "4"]


# --- несовместимые данные и сбои источника ------------------------------------------------------


async def test_trading_after_valuation_date_is_incompatible():
    fake = FakeIss()
    fake.filter_dates = False  # источник вернул строку позже запрошенной даты
    code = await error_code(OFZ, fake, date(2026, 11, 12))
    assert code == RowErrorCode.INCOMPATIBLE_MARKET_DATA


async def test_other_secid_is_incompatible():
    fake = FakeIss()
    for row in fake.edit("history_SU26238RMFS4")["history"]["data"]:
        row[1] = "SU26237RMFS6"
    assert await error_code(OFZ, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA


@pytest.mark.parametrize(("column", "value"), [(3, 0), (3, -5), (6, 0), (5, -1), (3, "н/д")])
async def test_non_positive_or_non_numeric_values_are_incompatible(column, value):
    fake = FakeIss()
    for row in fake.edit("history_SU26238RMFS4")["history"]["data"]:
        row[column] = value
    assert await error_code(OFZ, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA


async def test_unknown_face_currency_is_incompatible():
    fake = FakeIss()
    for row in fake.edit("description_SU26238RMFS4")["description"]["data"]:
        if row[0] == "FACEUNIT":
            row[1] = "рубль"
    assert await error_code(OFZ, fake) == RowErrorCode.INCOMPATIBLE_MARKET_DATA


@pytest.mark.parametrize("kind", ["search", "description", "history", "yields", "bondization"])
@pytest.mark.parametrize(
    "failure",
    [
        {"status": 500},
        {"status": 403},
        {"raw": b"<html>maintenance</html>"},  # не JSON
        {"raw": b'{"other": {}}'},  # нет нужного блока
        {"raw": b'{"history": {"columns": ["A"], "data": [[1, 2]]}}'},  # строка длиннее columns
        {"error": httpx.ReadTimeout("timeout")},
        {"error": httpx.ConnectError("dns")},
    ],
)
async def test_source_failures_are_provider_unavailable(kind, failure):
    fake = FakeIss()
    if "status" in failure:
        fake.status[kind] = failure["status"]
    if "raw" in failure:
        fake.raw[kind] = failure["raw"]
    if "error" in failure:
        fake.error[kind] = failure["error"]
    assert await error_code(OFZ, fake) == RowErrorCode.PROVIDER_UNAVAILABLE


async def test_without_iss_accrued_basis_is_chosen_by_duration():
    """Нет НКД на дату расчётов: базу выбирает близость дюрации к DURATION, числа те же"""
    fake = FakeIss()
    for name in ("yields_SU26238RMFS4", "yields_RU000A105A95"):
        for row in fake.edit(name)["history_yields"]["data"]:
            row[6] = None  # ACCINT
    ofz, usd = await snapshot(OFZ, fake=fake), await snapshot(USD_BOND, fake=fake)
    assert q12(ofz.duration_mod) == Decimal("5.768382680528")
    assert q12(usd.duration_mod) == Decimal("5.176459212386")
