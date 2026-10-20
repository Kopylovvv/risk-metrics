"""Окружение Alembic: адрес БД из app/config.py, схема из моделей app/db"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import database_url
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Схема из моделей: по ней работают autogenerate и alembic check
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Вывести SQL без подключения к БД: alembic upgrade head --sql"""
    context.configure(
        url=database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Тесты передают готовое подключение, иначе подключаемся по адресу из окружения
    connection = config.attributes.get("connection")
    if connection is None:
        engine = create_engine(database_url(), poolclass=pool.NullPool)
        with engine.connect() as conn:
            _run(conn)
    else:
        _run(connection)


def _run(connection) -> None:
    # compare_type: замечать смену типов колонок при autogenerate
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
