"""Диагностика (логи с request_id) и метрики Prometheus (S3-11)"""

import io
import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.api.deps import get_risk_calculator
from app.observability import JsonFormatter, redact
from tests.test_api_calculations import (
    FIXED,
    NEW_BOND,
    USD_BOND,
    calculate,
    insert_instrument,
    insert_snapshot,
    workspace_with,
)
from tests.test_api_market_sources import moex_cbr  # noqa: F401  фикстура
from tests.test_api_trades import add, open_workspace, second_client, trades_url


@pytest.fixture
def logs():
    """Строки JSON, которые приложение пишет в stdout (форматтер тот же, что в setup_logging)"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def read() -> list[dict]:
        # httpx здесь клиент теста, а не приложение
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        return [e for e in lines if e["logger"].startswith("app")]

    yield read
    root.removeHandler(handler)
    root.setLevel(old_level)


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Delta:
    """Изменение набора метрик за время блока"""

    def __init__(self, *samples: tuple[str, dict]):
        self.samples = samples

    def __enter__(self):
        self.before = [metric(n, **lb) for n, lb in self.samples]
        return self

    def __exit__(self, *_):
        self.changes = [
            metric(n, **lb) - b for (n, lb), b in zip(self.samples, self.before, strict=True)
        ]


# --- идентификатор запроса -------------------------------------------------------------


async def test_request_id_is_created_and_returned(api):
    r = await api.get("/health/live")
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 32 and int(rid, 16) >= 0


async def test_safe_incoming_request_id_is_kept_unsafe_is_replaced(api):
    r = await api.get("/health/live", headers={"X-Request-ID": "front-1234abcd"})
    assert r.headers["X-Request-ID"] == "front-1234abcd"
    for bad in ("short", "x" * 65, "a b c d e f g h", "id;rm -rf"):
        r = await api.get("/health/live", headers={"X-Request-ID": bad})
        assert r.headers["X-Request-ID"] != bad


@pytest.mark.parametrize(
    ("method", "url", "code"),
    [
        ("get", "/api/v1/calculations/00000000-0000-4000-8000-000000000000", "SESSION_REQUIRED"),
        ("get", "/api/v1/instruments/bad", "VALIDATION_ERROR"),
        ("get", "/no/such/path", "NOT_FOUND"),
    ],
)
async def test_error_body_carries_request_id(api, method, url, code):
    r = await getattr(api, method)(url, headers={"X-Request-ID": "trace-0001"})
    assert r.json()["code"] == code
    assert r.json()["request_id"] == r.headers["X-Request-ID"] == "trace-0001"


# --- логи: этапы, источник, длительность, тип ошибки -------------------------------------


async def test_calculation_events_are_linked_by_request_id(api, engine, logs, moex_cbr):  # noqa: F811
    _, fx = moex_cbr
    fx.mode = "crash"  # ЦБ падает с TimeoutError
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    ws_id, h, rev = await workspace_with(api, (FIXED, 1), (USD_BOND, 1))
    r = await calculate(api, ws_id, h, rev)
    assert r.json()["status"] == "partial"
    rid = r.headers["X-Request-ID"]

    mine = [e for e in logs() if e["request_id"] == rid]
    kinds = [e["event"] for e in mine]
    stages = [e["stage"] for e in mine if e["event"] == "calc.stage"]
    assert stages == ["read", "sources", "write"]
    assert kinds[-1] == "http.request" and "calc.finished" in kinds

    sources = {(e["source"], e["operation"]): e for e in mine if e["event"] == "source.request"}
    usd = sources[("moex", "snapshot")]
    assert (usd["outcome"], usd["isin"], usd["stage"]) == ("ok", USD_BOND, "sources")
    fx_event = sources[("cbr", "fx_rate")]
    assert (fx_event["outcome"], fx_event["error_type"]) == (
        "FX_PROVIDER_UNAVAILABLE",
        "TimeoutError",
    )
    assert fx_event["level"] == "WARNING" and fx_event["duration_ms"] >= 0

    done = next(e for e in mine if e["event"] == "calc.finished")
    assert (done["status"], done["rows"], done["failed_rows"]) == ("partial", 2, 1)
    assert done["calculation_id"] == r.json()["id"]
    http = mine[-1]
    assert (http["method"], http["route"], http["status"]) == (
        "POST",
        "/api/v1/workspaces/{workspace_id}/calculations",
        201,
    )


async def test_rejected_calculation_logs_error_type(api, logs):
    ws_id, h = await open_workspace(api)
    r = await calculate(api, ws_id, h, 0)
    assert r.json()["code"] == "WORKSPACE_EMPTY"
    mine = [e for e in logs() if e["request_id"] == r.headers["X-Request-ID"]]
    stage = next(e for e in mine if e["event"] == "calc.stage")
    assert (stage["stage"], stage["outcome"], stage["error_type"]) == (
        "read",
        "error",
        "WorkspaceEmpty",
    )
    assert next(e for e in mine if e["event"] == "calc.finished")["status"] == "empty"
    assert mine[-1]["error_code"] == "WORKSPACE_EMPTY" and mine[-1]["status"] == 422


async def test_unexpected_error_is_500_with_request_id_and_stage(api, engine, logs):
    from app.main import app

    def broken(_):
        raise RuntimeError("password=hunter2 in message")

    app.dependency_overrides[get_risk_calculator] = lambda: broken
    insert_snapshot(engine, insert_instrument(engine, FIXED))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ws_id, h, rev = await workspace_with(client, (FIXED, 1))
        r = await calculate(client, ws_id, h, rev, valuation_date=None)
    assert (r.status_code, r.json()["code"]) == (500, "INTERNAL_ERROR")
    rid = r.json()["request_id"]
    assert r.headers["X-Request-ID"] == rid

    mine = [e for e in logs() if e["request_id"] == rid]
    stage = next(e for e in mine if e["event"] == "calc.stage" and e["outcome"] == "error")
    assert (stage["stage"], stage["error_type"]) == ("write", "RuntimeError")
    http = next(e for e in mine if e["event"] == "http.request")
    assert (http["status"], http["error_type"], http["error_code"]) == (
        500,
        "RuntimeError",
        "INTERNAL_ERROR",
    )
    assert "hunter2" not in json.dumps(mine)


async def test_no_secrets_or_identifiers_in_logs_and_metrics(api, engine, logs, moex_cbr):  # noqa: F811
    ws_id, h = await open_workspace(api)
    r = await add(api, ws_id, h, isin=NEW_BOND, quantity=5)
    trade_id = r.json()["trades"][0]["id"] if "trades" in r.json() else r.json()["id"]
    await calculate(api, ws_id, h, 1)
    await api.delete(trades_url(ws_id, trade_id), params={"expected_revision": 1}, headers=h)

    cookie = api.cookies.get("rm_session")
    text = json.dumps(logs(), ensure_ascii=False)
    for secret in (cookie, h["X-CSRF-Token"], ws_id, trade_id):
        assert secret and secret not in text
    exposed = (await api.get("/metrics")).text
    for value in (cookie, h["X-CSRF-Token"], ws_id, trade_id, NEW_BOND, "rm_session"):
        assert value not in exposed


def test_redact_masks_secrets_in_text():
    text = "postgresql+psycopg://risk:s3cret@db:5432/x password=abc token=xyz csrf_token=q"
    assert redact(text) == (
        "postgresql+psycopg://risk:***@db:5432/x password=*** token=*** csrf_token=***"
    )


# --- метрики: известные запросы дают ожидаемые изменения ---------------------------------

CALC_ROUTE = "/api/v1/workspaces/{workspace_id}/calculations"


async def test_http_metrics_use_route_template_and_status(api):
    ws = ("risk_http_requests_total", dict(method="POST", route="/api/v1/workspaces", status="201"))
    miss = ("risk_http_requests_total", dict(method="GET", route="unmatched", status="404"))
    duration = (
        "risk_http_request_duration_seconds_count",
        dict(method="POST", route="/api/v1/workspaces"),
    )
    with Delta(ws, miss, duration) as d:
        await open_workspace(api)
        await api.get("/no/such/path/RU000A1038V6")
        await api.get("/no/other")
    assert d.changes == [1, 2, 1]


async def test_calculation_metrics_by_status(api, engine, moex_cbr):  # noqa: F811
    market, _ = moex_cbr
    samples = [
        ("risk_calculations_total", dict(status=s))
        for s in ("success", "partial", "conflict", "empty")
    ] + [
        ("risk_calculation_rows_total", dict(status="success", error_code="none")),
        ("risk_calculation_rows_total", dict(status="failed", error_code="INSTRUMENT_NOT_FOUND")),
        ("risk_calculation_duration_seconds_count", dict(status="success")),
        ("risk_source_requests_total", dict(source="moex", operation="snapshot", outcome="ok")),
        (
            "risk_http_requests_total",
            dict(method="POST", route=CALC_ROUTE, status="409"),
        ),
    ]
    with Delta(*samples) as d:
        ws_id, h, rev = await workspace_with(api, (NEW_BOND, 1))
        assert (await calculate(api, ws_id, h, rev)).json()["status"] == "success"
        async with second_client() as other:
            ws_id, h, rev = await workspace_with(other, (NEW_BOND, 1), ("US0378331005", 1))
            assert (await calculate(other, ws_id, h, rev)).json()["status"] == "partial"
            assert (await calculate(other, ws_id, h, rev - 1)).status_code == 409
        async with second_client() as third:
            empty_id, eh = await open_workspace(third)
            assert (await calculate(third, empty_id, eh, 0)).status_code == 422
    # success: 1 строка ok; partial: 1 ok + 1 нет выпуска; снимок у Мосбиржи спрошен один раз,
    # во втором расчёте он уже в БД
    assert d.changes == [1, 1, 1, 1, 2, 1, 1, 1, 1]
    assert market.calls.count(f"snapshot:{NEW_BOND}") == 1


async def test_metrics_endpoint_lists_names_and_is_not_counted(api):
    r = await api.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "risk_http_requests_total",
        "risk_http_request_duration_seconds",
        "risk_calculations_total",
        "risk_calculation_duration_seconds",
        "risk_calculation_stage_duration_seconds",
        "risk_calculation_rows_total",
        "risk_source_requests_total",
        "risk_source_request_duration_seconds",
    ):
        assert f"# HELP {name} " in r.text
    assert 'route="/metrics"' not in r.text


async def test_service_routes_are_named_and_unknown_paths_are_not(api):
    docs = ("risk_http_requests_total", dict(method="GET", route="/docs", status="200"))
    with Delta(docs) as d:
        await api.get("/docs")
        await api.get("/docs/RU000A1038V6")  # неизвестный путь: в метку не попадает
    assert d.changes == [1]
    assert "RU000A1038V6" not in (await api.get("/metrics")).text
