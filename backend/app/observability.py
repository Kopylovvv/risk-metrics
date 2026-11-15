"""Диагностика и мониторинг

Два разных канала:
* логи (stdout, JSON по строке на событие): индивидуальная диагностика одного запроса;
  все события запроса несут request_id; в них этап, источник, длительность и тип ошибки
* метрики Prometheus (GET /metrics): только агрегаты. Метки из короткого фиксированного
  списка: шаблон маршрута, метод, код ответа, статус, этап, источник. ISIN, id набора или
  сделки, cookie, токены и сырой путь в метки не попадают

Идентификатор запроса берётся из заголовка X-Request-ID (если он безопасного вида) или
создаётся заново и возвращается в ответе и в теле ошибки
"""

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_METHODS = {"GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS", "HEAD"}
UNMATCHED_ROUTE = "unmatched"
_NOT_MEASURED = {"/metrics"}

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
error_code_var: ContextVar[str | None] = ContextVar("error_code", default=None)
stage_var: ContextVar[str | None] = ContextVar("stage", default=None)

events = logging.getLogger("app.events")


# --- метрики -------------------------------------------------------------------------

_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

HTTP_REQUESTS = Counter(
    "risk_http_requests_total", "HTTP-запросы к API", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "risk_http_request_duration_seconds",
    "Длительность обработки HTTP-запроса",
    ["method", "route"],
    buckets=_SECONDS,
)
CALCULATIONS = Counter("risk_calculations_total", "Запуски расчёта DV01", ["status"])
CALCULATION_DURATION = Histogram(
    "risk_calculation_duration_seconds",
    "Длительность расчёта DV01 от чтения набора до записи результата",
    ["status"],
    buckets=_SECONDS,
)
CALCULATION_STAGE_DURATION = Histogram(
    "risk_calculation_stage_duration_seconds",
    "Длительность этапа расчёта: read, sources, write",
    ["stage"],
    buckets=_SECONDS,
)
CALCULATION_ROWS = Counter(
    "risk_calculation_rows_total", "Строки результата расчёта", ["status", "error_code"]
)
SOURCE_REQUESTS = Counter(
    "risk_source_requests_total",
    "Запросы к источникам данных",
    ["source", "operation", "outcome"],
)
SOURCE_DURATION = Histogram(
    "risk_source_request_duration_seconds",
    "Длительность запроса к источнику данных",
    ["source", "operation"],
    buckets=_SECONDS,
)


async def metrics_endpoint(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- логи ----------------------------------------------------------------------------

# на случай, если секрет окажется в тексте исключения: адрес БД с паролем, key=value
_SECRETS = (
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(r"(?i)\b(password|passwd|token|secret|cookie|csrf[_-]?token|session)=\S+"),
        r"\1=***",
    ),
)


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    """Одна строка JSON: время, уровень, событие, request_id и поля события"""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
            # запись пишется синхронно в том же запросе: контекст ещё его
            "request_id": request_id_var.get(),
        }
        if stage_var.get() is not None:
            data["stage"] = stage_var.get()
        for key, value in vars(record).items():
            if key not in _STANDARD and key not in data:
                data[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            data["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """JSON в stdout для всех логгеров, включая uvicorn; вызывается при старте приложения"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # журнал доступа uvicorn пишет сырой путь с id набора; вместо него событие http.request
    logging.getLogger("uvicorn.access").disabled = True
    # httpx в адаптерах пишет каждый URL запроса к источнику; хватает события source.request
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    events.log(level, event, extra=fields)


def error_type(exc: BaseException) -> str:
    """Тип ошибки для диагностики: код контракта, если есть, иначе имя класса"""
    code = getattr(exc, "code", None)
    return str(getattr(code, "value", code)) if code else type(exc).__name__


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


# --- этапы и источники ---------------------------------------------------------------


@contextmanager
def calculation_stage(name: str) -> Iterator[None]:
    """Этап расчёта: длительность в метрику и в лог, при сбое тип ошибки"""
    token = stage_var.set(name)
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        log_event(
            "calc.stage",
            logging.WARNING,
            stage=name,
            outcome="error",
            error_type=error_type(exc),
            duration_ms=_ms(time.perf_counter() - started),
        )
        raise
    else:
        duration = time.perf_counter() - started
        CALCULATION_STAGE_DURATION.labels(name).observe(duration)
        log_event("calc.stage", stage=name, outcome="ok", duration_ms=_ms(duration))
    finally:
        stage_var.reset(token)


def observe_source(
    source: str, operation: str, outcome: str, seconds: float, **fields: object
) -> None:
    SOURCE_REQUESTS.labels(source, operation, outcome).inc()
    SOURCE_DURATION.labels(source, operation).observe(seconds)
    level = logging.INFO if outcome == "ok" else logging.WARNING
    log_event(
        "source.request",
        level,
        source=source,
        operation=operation,
        outcome=outcome,
        duration_ms=_ms(seconds),
        **fields,
    )


def observe_calculation(status: str, seconds: float, rows: list[tuple[str, str]], **fields):
    """rows: (статус строки, код ошибки или none)"""
    CALCULATIONS.labels(status).inc()
    CALCULATION_DURATION.labels(status).observe(seconds)
    for row_status, code in rows:
        CALCULATION_ROWS.labels(row_status, code).inc()
    failed = sum(1 for s, _ in rows if s != "success")
    log_event(
        "calc.finished",
        logging.INFO if status in ("success", "partial", "failed") else logging.WARNING,
        status=status,
        duration_ms=_ms(seconds),
        rows=len(rows),
        failed_rows=failed,
        **fields,
    )


# --- HTTP ----------------------------------------------------------------------------


def new_request_id(incoming: str | None) -> str:
    if incoming and _SAFE_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


def route_of(scope: Scope) -> str:
    """Шаблон маршрута (/api/v1/workspaces/{workspace_id}), а не сырой путь"""
    route = scope.get("route")
    if getattr(route, "path", None):
        return route.path
    # служебные маршруты Starlette (/docs, /metrics) не пишут route в scope: только точное
    # совпадение с путём без параметров, иначе сырой путь мог бы попасть в метку
    path = scope.get("path", "")
    routes = getattr(getattr(scope.get("app"), "router", None), "routes", ())
    if "{" not in path and any(getattr(r, "path", None) == path for r in routes):
        return path
    return UNMATCHED_ROUTE


class ObservabilityMiddleware:
    """request_id, событие http.request и метрики HTTP по каждому запросу"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        request_id = new_request_id(incoming)
        request_id_var.set(request_id)
        error_code_var.set(None)
        stage_var.set(None)
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                names = {k.lower() for k, _ in message.get("headers", [])}
                if REQUEST_ID_HEADER.lower().encode() not in names:
                    message.setdefault("headers", [])
                    message["headers"] = [
                        *message["headers"],
                        (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
                    ]
            await send(message)

        failure: BaseException | None = None
        try:
            await self.app(scope, receive, send_with_id)
        except BaseException as exc:
            failure = exc
            status = 500
            raise
        finally:
            self._finish(scope, status, time.perf_counter() - started, failure)

    @staticmethod
    def _finish(scope: Scope, status: int, seconds: float, failure: BaseException | None):
        route = route_of(scope)
        if route in _NOT_MEASURED:
            return
        method = scope.get("method", "")
        method = method if method in _METHODS else "OTHER"
        HTTP_REQUESTS.labels(method, route, str(status)).inc()
        HTTP_DURATION.labels(method, route).observe(seconds)
        fields = dict(method=method, route=route, status=status, duration_ms=_ms(seconds))
        code = error_code_var.get() or ("INTERNAL_ERROR" if failure else None)
        if code:
            fields["error_code"] = code
        if failure is not None:
            fields["error_type"] = type(failure).__name__
        level = logging.ERROR if status >= 500 else logging.INFO
        log_event("http.request", level, **fields)
