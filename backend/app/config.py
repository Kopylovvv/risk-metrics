"""Адрес PostgreSQL: DATABASE_URL или переменные POSTGRES_* из .env (как в compose.yaml)"""

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
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),  # порт, проброшенный в compose.yaml
        database=os.getenv("POSTGRES_DB", "risk_metrics"),
    )
