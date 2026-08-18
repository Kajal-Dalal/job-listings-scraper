"""
Async SQLAlchemy database setup.

- Uses SQLAlchemy 2.0 async engine
- Supports SQLite (default) and PostgreSQL
- Provides get_db_session() dependency for FastAPI
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.monitoring.logger import get_logger
from src.storage.models import Base

log = get_logger(__name__)


class Database:
    """
    Manages the async SQLAlchemy engine and session factory.
    Instantiated once at app startup.
    """

    def __init__(self, database_url: str):
        self._url = database_url
        self._engine: AsyncEngine = self._create_engine(database_url)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        log.info("database_configured", url=self._sanitise_url(database_url))

    def _create_engine(self, url: str) -> AsyncEngine:
        """Create an async engine with appropriate settings for the DB type."""
        if url.startswith("sqlite"):
            return create_async_engine(
                url,
                echo=False,
                connect_args={
                    "check_same_thread": False,
                    "timeout": 30,
                },
            )
        else:
            # PostgreSQL / other: use connection pooling
            return create_async_engine(
                url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

    @staticmethod
    def _sanitise_url(url: str) -> str:
        """Mask password in DB URL for logging."""
        import re
        return re.sub(r"(:)[^:@]+(@)", r"\1***\2", url)

    async def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("database_tables_created")

    async def drop_tables(self) -> None:
        """Drop all tables (use in tests only)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        log.warning("database_tables_dropped")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Async context manager that provides a database session.
        Commits on success, rolls back on exception.

        Usage:
            async with db.session() as session:
                result = await session.execute(select(JobListing))
        """
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """Dispose the engine connection pool."""
        await self._engine.dispose()
        log.info("database_closed")

    @property
    def engine(self) -> AsyncEngine:
        return self._engine


# ---------------------------------------------------------------------------
# Module-level singleton (set during app startup)
# ---------------------------------------------------------------------------
_db_instance: Database | None = None


def get_database() -> Database:
    """Return the module-level Database instance (must be initialised first)."""
    if _db_instance is None:
        raise RuntimeError(
            "Database not initialised. Call init_database() during app startup."
        )
    return _db_instance


def init_database(database_url: str) -> Database:
    """Initialise the module-level Database singleton."""
    global _db_instance
    _db_instance = Database(database_url)
    return _db_instance


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.

    Usage in endpoint:
        async def endpoint(session: AsyncSession = Depends(get_db_session)):
    """
    db = get_database()
    async with db.session() as session:
        yield session
