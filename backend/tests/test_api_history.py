"""История расчётов набора и «взять расчёт за основу»"""

import uuid
from decimal import Decimal

from tests.test_api_trades import open_workspace, second_client, trades_url
from tests.test_integration_s3 import (  # noqa: F401  фикстура control
    RUB_BOND,
    UNKNOWN,
    USD_BOND,
    control,
    post_trade,
    run_calc,
)


def history_url(ws_id: str, **params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/v1/workspaces/{ws_id}/calculations" + (f"?{query}" if query else "")


async def restore(client, ws_id, headers, calc_id, revision):
    body = {"calculation_id": calc_id, "expected_revision": revision}
    return await client.post(f"/api/v1/workspaces/{ws_id}/restore", json=body, headers=headers)


async def three_calculations(api):
    """Расчёт 1: ОФЗ 100 и ГазКЗ −10; расчёт 2: ОФЗ 150; расчёт 3: + неизвестный ISIN"""
    ws_id, h = await open_workspace(api)
    r = await post_trade(api, ws_id, h, RUB_BOND, 100, 0)
    ofz = r.json()["trades"][0]["id"]
    await post_trade(api, ws_id, h, USD_BOND, -10, 1)
    first = (await run_calc(api, ws_id, h, 2)).json()
    await api.patch(
        trades_url(ws_id, ofz), json={"quantity": 150, "expected_revision": 2}, headers=h
    )
    second = (await run_calc(api, ws_id, h, 3)).json()
    await post_trade(api, ws_id, h, UNKNOWN, 5, 3)
    third = (await run_calc(api, ws_id, h, 4)).json()
    return ws_id, h, [first, second, third]


async def test_empty_history(api):
    ws_id, _ = await open_workspace(api)
    assert (await api.get(history_url(ws_id))).json() == {"items": [], "total": 0}


async def test_history_lists_calculations_newest_first(api, control):  # noqa: F811
    ws_id, h, (first, second, third) = await three_calculations(api)
    body = (await api.get(history_url(ws_id))).json()
    assert body["total"] == 3
    items = body["items"]
    assert [i["id"] for i in items] == [third["id"], second["id"], first["id"]]
    newest, middle, oldest = items
    assert (newest["status"], newest["positions"], newest["failed_positions"]) == ("partial", 3, 1)
    assert newest["total_value"] is None  # partial: итога нет
    assert (oldest["status"], oldest["positions"], oldest["failed_positions"]) == ("success", 2, 0)
    assert Decimal(oldest["total_value"]) == Decimal(first["total_value"])
    assert [i["workspace_revision"] for i in items] == [4, 3, 2]
    # актуален только последний: набор после него не меняли
    assert [i["is_current"] for i in items] == [True, False, False]

    await post_trade(api, ws_id, h, RUB_BOND, 1, 4)
    after_edit = (await api.get(history_url(ws_id))).json()["items"]
    assert [i["is_current"] for i in after_edit] == [False, False, False]


async def test_history_pages(api, control):  # noqa: F811
    ws_id, _, calcs = await three_calculations(api)
    page = (await api.get(history_url(ws_id, limit=2, offset=1))).json()
    assert page["total"] == 3
    assert [i["id"] for i in page["items"]] == [calcs[1]["id"], calcs[0]["id"]]
    assert (await api.get(history_url(ws_id, limit=0))).status_code == 422
    assert (await api.get(history_url(ws_id, limit=101))).status_code == 422


async def test_restore_takes_old_calculation_as_base(api, control):  # noqa: F811
    ws_id, h, (first, second, third) = await three_calculations(api)
    r = await restore(api, ws_id, h, first["id"], 4)
    assert r.status_code == 200, r.text
    ws = r.json()
    # состав и порядок как в первом расчёте, revision + 1, последний расчёт уже не актуален
    assert [(t["isin"], t["quantity"]) for t in ws["trades"]] == [(RUB_BOND, 100), (USD_BOND, -10)]
    assert ws["revision"] == 5 and ws["last_calculation_id"] == third["id"]
    assert ws["trades"][0]["instrument"]["isin"] == RUB_BOND  # связь со справочником на месте

    # история не изменилась: те же три расчёта, каждый отдаёт прежний ответ
    assert (await api.get(history_url(ws_id))).json()["total"] == 3
    for old in (first, second, third):
        again = (await api.get(f"/api/v1/calculations/{old['id']}")).json()
        # значения те же; порядок строк без живых сделок идёт по ISIN, поэтому сравниваем множества
        assert sorted(
            (x["isin"], x["quantity"], x["metric_value"]) for x in again["results"]
        ) == sorted((x["isin"], x["quantity"], x["metric_value"]) for x in old["results"])
        assert (again["status"], again["total_value"]) == (old["status"], old["total_value"])

    # новый расчёт по восстановленному составу даёт тот же итог, что и первый
    fourth = (await run_calc(api, ws_id, h, 5)).json()
    assert Decimal(fourth["total_value"]) == Decimal(first["total_value"])
    assert (await api.get(history_url(ws_id))).json()["total"] == 4


async def test_restore_checks_revision_owner_and_csrf(api, control):  # noqa: F811
    ws_id, h, (first, _, _) = await three_calculations(api)
    before = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()

    r = await restore(api, ws_id, h, first["id"], 3)  # устаревший revision
    assert (r.status_code, r.json()["code"]) == (409, "REVISION_CONFLICT")
    r = await restore(api, ws_id, h, str(uuid.uuid4()), 4)
    assert (r.status_code, r.json()["code"]) == (404, "CALCULATION_NOT_FOUND")
    r = await restore(api, ws_id, {}, first["id"], 4)
    assert (r.status_code, r.json()["code"]) == (403, "CSRF_TOKEN_INVALID")

    async with second_client() as other:
        other_id, oh = await open_workspace(other)
        # чужой расчёт в своём наборе: 404, как будто его нет
        r = await restore(other, other_id, oh, first["id"], 0)
        assert (r.status_code, r.json()["code"]) == (404, "CALCULATION_NOT_FOUND")
        # чужая история: 404
        assert (await other.get(history_url(ws_id))).status_code == 404

    # набор после всех отказов не изменился
    after = (await api.get(f"/api/v1/workspaces/{ws_id}")).json()
    assert (after["revision"], after["trades"]) == (before["revision"], before["trades"])
