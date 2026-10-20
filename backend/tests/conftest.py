"""Тесты на настоящем PostgreSQL: отдельная база <имя>_test, схема ставится миграциями"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.config import database_url

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(connection) -> Config:
    """Настройки Alembic с уже открытым подключением"""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["connection"] = connection
    return cfg


def create_empty_database(name: str) -> URL:
    """Пересоздаёт пустую базу на том же сервере; подключаемся к служебной базе postgres"""
    server = database_url()
    admin = create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # понятное сообщение вместо длинного трейса
        pytest.exit(f"Нет доступа к PostgreSQL {server.host}:{server.port}: {exc}", returncode=1)
    finally:
        admin.dispose()
    return server.set(database=name)


def drop_database(name: str) -> None:
    admin = create_engine(database_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def test_db_url() -> Iterator[URL]:
    """Одна тестовая база на весь прогон; рабочая база разработчика не трогается"""
    name = f"{database_url().database}_test"
    url = create_empty_database(name)
    engine = create_engine(url)
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    engine.dispose()
    # приложение (app.db.session) при импорте подключится к тестовой базе
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    yield url
    drop_database(name)


@pytest.fixture(scope="session")
def engine(test_db_url: URL) -> Iterator[Engine]:
    eng = create_engine(test_db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Всё, что тест записал, откатывается после него"""
    with engine.connect() as conn:
        outer = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
            yield s
        outer.rollback()
