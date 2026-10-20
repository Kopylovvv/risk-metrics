"""Подключение приложения к PostgreSQL (асинхронно, psycopg 3)"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import database_url

# pool_pre_ping: проверять соединение перед выдачей из пула (после рестарта БД)
engine = create_async_engine(database_url(), pool_pre_ping=True)
# expire_on_commit=False: объекты остаются читаемыми после commit (нужно для ответа API)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Сессия на один запрос; для FastAPI Depends"""
    async with SessionFactory() as session:
        yield session
