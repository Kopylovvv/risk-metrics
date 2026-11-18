"""Настройки из окружения: адрес PostgreSQL, разрешённые Origin, cookie сессии, источники"""

import os
from pathlib import Path

from sqlalchemy.engine import URL, make_url

# .env лежит в корне репозитория, на уровень выше backend
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Читает KEY=VALUE из .env; уже заданные переменные окружения не перезаписывает"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:  # пустые строки и комментарии
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def database_url() -> URL:
    load_env_file()
    explicit = os.getenv("DATABASE_URL")  # полный адрес важнее отдельных переменных
    if explicit:
        return make_url(explicit)
    return URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "risk_metrics"),
        password=os.getenv("POSTGRES_PASSWORD"),
        # не localhost: на Windows он сначала идёт в IPv6, а compose слушает только IPv4
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),  # порт, проброшенный в compose.yaml
        database=os.getenv("POSTGRES_DB", "risk_metrics"),
    )


# Откуда браузеру можно слать изменяющие запросы: фронт на Vite (5173) и Swagger (8000)
DEFAULT_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
)


def allowed_origins() -> list[str]:
    load_env_file()
    raw = os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def log_level() -> str:
    load_env_file()
    return os.getenv("LOG_LEVEL", "INFO").upper()


def external_sources_enabled() -> bool:
    """Ходить ли за данными в Мосбиржу и ЦБ. По умолчанию да

    EXTERNAL_SOURCES=off: расчёт берёт данные только из БД (тесты, работа без сети)
    """
    load_env_file()
    return os.getenv("EXTERNAL_SOURCES", "on").strip().lower() not in ("off", "0", "false")


CBONDS_DEFAULT_URL = "https://ws2.cbonds.info/services/json/"


def cbonds_credentials() -> tuple[str, str, str] | None:
    """Логин, пароль и адрес web-сервиса Cbonds; None без доступа. В расчёте не используется"""
    load_env_file()
    login, password = os.getenv("CBONDS_LOGIN", ""), os.getenv("CBONDS_PASSWORD", "")
    if not login or not password:
        return None
    return login, password, os.getenv("CBONDS_URL") or CBONDS_DEFAULT_URL


def cookie_secure() -> bool:
    """Secure-cookie только по HTTPS: на сервере COOKIE_SECURE=true, локально false"""
    load_env_file()
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"
