"""Ошибки API: единый формат ErrorOut для всех ответов 4xx/5xx"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorDetail, ErrorOut
from app.services.workspaces import RevisionConflict, TradeNotFound

logger = logging.getLogger(__name__)

# стандартные ответы FastAPI (нет маршрута, не тот метод) тоже приводим к нашему формату
_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: list[ErrorDetail] | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def session_required() -> ApiError:
    return ApiError(401, "SESSION_REQUIRED", "Нет сессии: откройте модуль заново")


def csrf_invalid() -> ApiError:
    return ApiError(403, "CSRF_TOKEN_INVALID", "Неверный или отсутствующий X-CSRF-Token")


def origin_not_allowed() -> ApiError:
    return ApiError(403, "ORIGIN_NOT_ALLOWED", "Запрос с недопустимого адреса")


def workspace_not_found() -> ApiError:
    # 404, а не 403: не раскрываем, что чужой набор существует
    return ApiError(404, "WORKSPACE_NOT_FOUND", "Набор сделок не найден")


def instrument_not_found() -> ApiError:
    return ApiError(404, "INSTRUMENT_NOT_FOUND", "Инструмент не найден")


def validation_error(message: str, field: str | None = None) -> ApiError:
    details = [ErrorDetail(field=field, message=message)]
    return ApiError(422, "VALIDATION_ERROR", "Проверьте введённые данные", details)


def _json(status: int, body: ErrorOut) -> JSONResponse:
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        body = ErrorOut(code=exc.code, message=exc.message, details=exc.details)
        return _json(exc.status_code, body)

    # ошибки Pydantic → 422 в нашем формате, путь к полю в details.field
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in err.get("loc", ())),
                message=str(err.get("msg", "")).removeprefix("Value error, "),
            )
            for err in exc.errors()
        ]
        body = ErrorOut(
            code="VALIDATION_ERROR", message="Проверьте введённые данные", details=details
        )
        return _json(422, body)

    # исключения слоя данных → коды контракта
    @app.exception_handler(RevisionConflict)
    async def _conflict(_: Request, __: RevisionConflict) -> JSONResponse:
        message = "Набор сделок изменился. Обновите данные и повторите."
        return _json(409, ErrorOut(code="REVISION_CONFLICT", message=message))

    @app.exception_handler(TradeNotFound)
    async def _trade_missing(_: Request, __: TradeNotFound) -> JSONResponse:
        return _json(404, ErrorOut(code="TRADE_NOT_FOUND", message="Сделка не найдена"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return _json(exc.status_code, ErrorOut(code=code, message=str(exc.detail)))

    # нет соединения с PostgreSQL → 503, а не 500
    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    async def _db_down(_: Request, exc: Exception) -> JSONResponse:
        logger.error("База данных недоступна: %s", exc)
        message = "База данных недоступна, повторите позже"
        return _json(503, ErrorOut(code="DATABASE_UNAVAILABLE", message=message))

    # всё непредвиденное: подробности только в лог, клиенту общий ответ
    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Необработанная ошибка", exc_info=exc)
        return _json(500, ErrorOut(code="INTERNAL_ERROR", message="Внутренняя ошибка сервера"))


# Описание ошибок для OpenAPI (/docs)
_DESCRIPTIONS = {
    401: "SESSION_REQUIRED: нет cookie сессии",
    403: "CSRF_TOKEN_INVALID / ORIGIN_NOT_ALLOWED",
    404: "WORKSPACE_NOT_FOUND (в т.ч. чужой набор), TRADE_NOT_FOUND, INSTRUMENT_NOT_FOUND",
    409: "REVISION_CONFLICT: набор изменился, expected_revision устарел",
    422: "VALIDATION_ERROR: неверный ввод, details указывает поле",
    503: "DATABASE_UNAVAILABLE: нет соединения с PostgreSQL",
}


def error_responses(*codes: int) -> dict[int | str, dict]:
    return {code: {"model": ErrorOut, "description": _DESCRIPTIONS[code]} for code in codes}
