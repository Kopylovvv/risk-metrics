"""Тесты на настоящем PostgreSQL: отдельная база <имя>_test, схема ставится миграциями"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import database_url

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Windows: psycopg в async-режиме не работает с ProactorEventLoop (стандартным для Windows)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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


# --- API ---------------------------------------------------------------------

TABLES = (
    "calculation_results, calculation_runs, trades, market_data_snapshots, fx_rates,"
    " instruments, workspaces"
)


@pytest.fixture
async def api(test_db_url: URL, engine: Engine) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент к приложению; после теста все таблицы очищаются"""
    from app.db.session import get_session  # импорт после выбора тестовой базы
    from app.main import app

    # NullPool: у каждого теста свой event loop, соединения между тестами не переиспользуем
    async_engine = create_async_engine(test_db_url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    await async_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
