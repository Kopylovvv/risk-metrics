"""API набора и сделок: CRUD, проверка ввода, изоляция наборов, revision"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.schemas import TradeCreate, TradeUpdate

ISIN = "RU000A1038V6"  # есть в справочнике (см. фикстуру bond)
OTHER_ISIN = "US0378331005"  # корректный ISIN, которого нет в справочнике


async def open_workspace(client: AsyncClient) -> tuple[str, dict[str, str]]:
    """POST /workspaces → id набора и заголовок с CSRF-токеном"""
    r = await client.post("/api/v1/workspaces")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    return body["id"], {"X-CSRF-Token": body["csrf_token"]}


def trades_url(ws_id: str, trade_id: str | None = None) -> str:
    url = f"/api/v1/workspaces/{ws_id}/trades"
    return f"{url}/{trade_id}" if trade_id else url


async def add(client, ws_id, headers, isin=ISIN, quantity=100, revision=0):
    body = {"isin": isin, "quantity": quantity, "expected_revision": revision}
    return await client.post(trades_url(ws_id), json=body, headers=headers)


def second_client() -> AsyncClient:
    """Другой браузер: то же приложение, но без cookie первой сессии"""
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def bond(engine: Engine):
    """Облигация в справочнике и её снимок, чтобы проверить карточку инструмента"""
    with engine.begin() as conn:
        instrument_id = conn.execute(
            text(
                "INSERT INTO instruments (id, isin, name, instrument_type, currency)"
                " VALUES (:id, :isin, 'ОФЗ 26238', 'bond', 'RUB') RETURNING id"
            ),
            {"id": uuid.uuid4(), "isin": ISIN},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO market_data_snapshots (id, instrument_id, provider, data_as_of,"
                " retrieved_at, currency, nominal, price_pct, duration_mod, convexity,"
                " coupon_type, coupon_frequency)"
                " VALUES (:id, :i, 'cbonds', :d, :t, 'RUB', 1000, 95.5, 3, 12, 'fixed', 2)"
            ),
            {
                "id": uuid.uuid4(),
                "i": instrument_id,
                "d": date(2026, 10, 20),
                "t": datetime.now(UTC),
            },
        )
    return instrument_id


# --- сессия и набор ------------------------------------------------------------


async def test_open_workspace_sets_cookie_and_returns_token(api):
    r = await api.post("/api/v1/workspaces")
    assert r.status_code == 201
    body = r.json()
    assert body["revision"] == 0 and body["trades"] == [] and body["csrf_token"]
    cookie = r.headers["set-cookie"].lower()
    assert "rm_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie

    # повторное открытие с той же cookie возвращает тот же набор, 200
    again = await api.post("/api/v1/workspaces")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]
    assert again.json()["csrf_token"] == body["csrf_token"]


async def test_get_workspace(api):
    ws_id, _ = await open_workspace(api)
    r = await api.get(f"/api/v1/workspaces/{ws_id}")
    assert r.status_code == 200
    assert r.json()["id"] == ws_id


# --- CRUD сделок ---------------------------------------------------------------


async def test_trade_lifecycle_is_stored_in_postgres(api, bond, engine):
    ws_id, h = await open_workspace(api)

    r = await add(api, ws_id, h, quantity=100)
    assert r.status_code == 201, r.text
    ws = r.json()
    assert ws["revision"] == 1
    trade = ws["trades"][0]
    assert (trade["isin"], trade["quantity"]) == (ISIN, 100)
    card = trade["instrument"]
    assert card["name"] == "ОФЗ 26238" and card["currency"] == "RUB"
    assert card["nominal"] == "1000" and card["price_pct"] == "95.5"  # деньги приходят строками
    assert card["coupon_type"] == "fixed" and card["data_as_of"] == "2026-10-20"

    # в БД лежит то же самое, сделка связана со справочником
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT isin, quantity, instrument_id FROM trades WHERE id = :id"),
            {"id": trade["id"]},
        ).one()
        assert (row.isin, int(row.quantity), row.instrument_id) == (ISIN, 100, bond)

    r = await api.patch(
        trades_url(ws_id, trade["id"]), json={"quantity": -250, "expected_revision": 1}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2
    assert r.json()["trades"][0]["quantity"] == -250  # шорт

    r = await api.delete(trades_url(ws_id, trade["id"]), params={"expected_revision": 2}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {**r.json(), "revision": 3, "trades": []}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM trades")).scalar_one() == 0
        revision = conn.execute(
            text("SELECT revision FROM workspaces WHERE id = :id"), {"id": ws_id}
        ).scalar_one()
        assert revision == 3


async def test_unknown_instrument_is_saved_without_card(api):
    ws_id, h = await open_workspace(api)
    r = await add(api, ws_id, h, isin=OTHER_ISIN)
    assert r.status_code == 201
    assert r.json()["trades"][0]["instrument"] is None


async def test_change_isin_relinks_instrument(api, bond):
    ws_id, h = await open_workspace(api)
    trade_id = (await add(api, ws_id, h, isin=OTHER_ISIN)).json()["trades"][0]["id"]
    r = await api.patch(
        trades_url(ws_id, trade_id), json={"isin": ISIN, "expected_revision": 1}, headers=h
    )
    assert r.json()["trades"][0]["instrument"]["isin"] == ISIN


async def test_trades_keep_order_of_adding(api):
    ws_id, h = await open_workspace(api)
    await add(api, ws_id, h, isin=ISIN, revision=0)
    r = await add(api, ws_id, h, isin=OTHER_ISIN, revision=1)
    assert [t["isin"] for t in r.json()["trades"]] == [ISIN, OTHER_ISIN]


async def test_documented_examples_work(api):
    """Примеры из /docs проходят как есть"""
    ws_id, h = await open_workspace(api)
    for example in TradeCreate.model_json_schema()["examples"]:
        r = await api.post(trades_url(ws_id), json=example, headers=h)
        assert r.status_code == 201, (example, r.text)
    trade_id = r.json()["trades"][0]["id"]
    example = TradeUpdate.model_json_schema()["examples"][0] | {"expected_revision": 2}
    r = await api.patch(trades_url(ws_id, trade_id), json=example, headers=h)
    assert r.status_code == 200, r.text


# --- проверка ввода ------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"isin": "RU000A1038V7", "quantity": 1}, "body.isin"),  # контрольная цифра
        ({"isin": "RU000A10", "quantity": 1}, "body.isin"),  # длина
        ({"isin": "", "quantity": 1}, "body.isin"),
        ({"isin": ISIN, "quantity": 0}, "body.quantity"),
        ({"isin": ISIN, "quantity": 1.5}, "body.quantity"),
        ({"isin": ISIN, "quantity": "10"}, "body.quantity"),  # строка не превращается в число
        ({"isin": ISIN, "quantity": 10**15 + 1}, "body.quantity"),
        ({"isin": ISIN}, "body.quantity"),
        ({"quantity": 1}, "body.isin"),
    ],
)
async def test_invalid_trade_input(api, body, field):
    ws_id, h = await open_workspace(api)
    r = await api.post(trades_url(ws_id), json=body | {"expected_revision": 0}, headers=h)
    assert r.status_code == 422
    error = r.json()
    assert error["code"] == "VALIDATION_ERROR"
    assert field in {d["field"] for d in error["details"]}
    # набор не изменился
    assert (await api.get(f"/api/v1/workspaces/{ws_id}")).json()["revision"] == 0


async def test_isin_is_normalized(api):
    ws_id, h = await open_workspace(api)
    r = await add(api, ws_id, h, isin="  ru000a1038v6 ")
    assert r.json()["trades"][0]["isin"] == ISIN


async def test_patch_requires_something_to_change(api):
    ws_id, h = await open_workspace(api)
    trade_id = (await add(api, ws_id, h)).json()["trades"][0]["id"]
    r = await api.patch(trades_url(ws_id, trade_id), json={"expected_revision": 1}, headers=h)
    assert r.status_code == 422


# --- revision ------------------------------------------------------------------


async def test_stale_revision_is_rejected(api):
    ws_id, h = await open_workspace(api)
    assert (await add(api, ws_id, h, revision=0)).status_code == 201
    r = await add(api, ws_id, h, revision=0)  # клиент не видел первое изменение
    assert r.status_code == 409
    assert r.json()["code"] == "REVISION_CONFLICT"
    workspace = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()
    assert workspace["revision"] == 1 and len(workspace["trades"]) == 1


async def test_parallel_changes_only_one_wins(api):
    ws_id, h = await open_workspace(api)
    results = await asyncio.gather(*(add(api, ws_id, h, revision=0) for _ in range(5)))
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409]


# --- доступ к своему набору ----------------------------------------------------


async def test_no_session_cookie(api):
    ws_id, _ = await open_workspace(api)
    async with second_client() as stranger:
        r = await stranger.get(f"/api/v1/workspaces/{ws_id}")
    assert r.status_code == 401
    assert r.json()["code"] == "SESSION_REQUIRED"


async def test_foreign_workspace_is_not_found(api):
    ws_id, h = await open_workspace(api)
    trade_id = (await add(api, ws_id, h)).json()["trades"][0]["id"]

    async with second_client() as stranger:
        _, stranger_h = await open_workspace(stranger)
        responses = [
            await stranger.get(f"/api/v1/workspaces/{ws_id}"),
            await add(stranger, ws_id, stranger_h, revision=1),
            await stranger.patch(
                trades_url(ws_id, trade_id),
                json={"quantity": 1, "expected_revision": 1},
                headers=stranger_h,
            ),
            await stranger.delete(
                trades_url(ws_id, trade_id), params={"expected_revision": 1}, headers=stranger_h
            ),
        ]
    for r in responses:
        assert r.status_code == 404, r.text
        assert r.json()["code"] == "WORKSPACE_NOT_FOUND"
    # чужие запросы ничего не изменили
    workspace = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()
    assert workspace["revision"] == 1 and workspace["trades"][0]["quantity"] == 100


async def test_trade_from_other_workspace_is_not_found(api):
    ws_a, h_a = await open_workspace(api)
    async with second_client() as other:
        ws_b, h_b = await open_workspace(other)
        foreign_trade = (await add(other, ws_b, h_b)).json()["trades"][0]["id"]
    r = await api.patch(
        trades_url(ws_a, foreign_trade), json={"quantity": 5, "expected_revision": 0}, headers=h_a
    )
    assert r.status_code == 404
    assert r.json()["code"] == "TRADE_NOT_FOUND"


async def test_unknown_ids(api):
    ws_id, h = await open_workspace(api)
    assert (await api.get(f"/api/v1/workspaces/{uuid.uuid4()}")).status_code == 404
    r = await api.delete(
        trades_url(ws_id, str(uuid.uuid4())), params={"expected_revision": 0}, headers=h
    )
    assert r.json()["code"] == "TRADE_NOT_FOUND"
    assert (await api.get("/api/v1/workspaces/not-a-uuid")).status_code == 422


@pytest.mark.parametrize("token", [None, "wrong"])
async def test_changes_require_csrf_token(api, token):
    ws_id, _ = await open_workspace(api)
    headers = {"X-CSRF-Token": token} if token else {}
    r = await add(api, ws_id, headers)
    assert r.status_code == 403
    assert r.json()["code"] == "CSRF_TOKEN_INVALID"


async def test_foreign_origin_is_rejected(api):
    ws_id, h = await open_workspace(api)
    r = await add(api, ws_id, h | {"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["code"] == "ORIGIN_NOT_ALLOWED"
    r = await add(api, ws_id, h | {"Origin": "http://localhost:5173"})  # фронт на Vite
    assert r.status_code == 201


# --- инструменты и служебное ---------------------------------------------------


async def test_instrument_card(api, bond):
    r = await api.get(f"/api/v1/instruments/{ISIN.lower()}")
    assert r.status_code == 200
    assert r.json()["price_pct"] == "95.5"
    r = await api.get(f"/api/v1/instruments/{OTHER_ISIN}")
    assert (r.status_code, r.json()["code"]) == (404, "INSTRUMENT_NOT_FOUND")
    r = await api.get("/api/v1/instruments/RU000A1038V7")
    assert (r.status_code, r.json()["code"]) == (422, "VALIDATION_ERROR")


async def test_health_and_docs(api):
    assert (await api.get("/health/live")).json() == {"status": "ok"}
    assert (await api.get("/health/ready")).json()["database"] == "ok"
    paths = (await api.get("/openapi.json")).json()["paths"]
    assert "/api/v1/workspaces/{workspace_id}/trades/{trade_id}" in paths
    assert (await api.get("/docs")).status_code == 200


async def test_standard_errors_use_common_format(api):
    r = await api.get("/api/v1/nothing-here")
    assert (r.status_code, r.json()["code"]) == (404, "NOT_FOUND")
    r = await api.put("/api/v1/workspaces")
    assert (r.status_code, r.json()["code"]) == (405, "METHOD_NOT_ALLOWED")


async def test_database_down_gives_503(api):
    from app.db.session import get_session
    from app.main import app

    # подключение к порту, где нет PostgreSQL
    broken = create_async_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", poolclass=NullPool)

    async def broken_session():
        async with async_sessionmaker(broken)() as s:
            yield s

    app.dependency_overrides[get_session] = broken_session
    r = await api.post("/api/v1/workspaces")
    assert (r.status_code, r.json()["code"]) == (503, "DATABASE_UNAVAILABLE")
    assert (await api.get("/health/ready")).status_code == 503
    await broken.dispose()
