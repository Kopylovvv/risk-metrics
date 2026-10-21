"""Приложение FastAPI. Запуск из папки backend: uvicorn app.main:app --reload → /docs"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, instruments, trades, workspaces
from app.config import allowed_origins
from app.errors import register_error_handlers

app = FastAPI(
    title="Модуль расчёта риск-метрик (DV01)",
    version="0.4.0",
    description=(
        "Пользователь вводит ISIN и количество бумаг (отрицательное количество означает шорт) "
        "и добавляет сделки по одной.\n\n"
        "Порядок работы: `POST /api/v1/workspaces` (cookie сессии + `csrf_token`) → "
        "изменения набора с заголовком `X-CSRF-Token` и `expected_revision`.\n\n"
        "Деньги передаются десятичными строками. Формат ошибок: `{code, message, details}`"
    ),
)

# Фронт на другом порту (Vite) ходит с cookie, поэтому allow_credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

register_error_handlers(app)

for module in (workspaces, trades, instruments, health):
    app.include_router(module.router)
