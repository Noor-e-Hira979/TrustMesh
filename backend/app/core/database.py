"""SQLAlchemy 2.0 engine and session wiring for the local SQLite database.

Design decisions
----------------
* **Synchronous SQLAlchemy, asynchronous call sites.** SQLite plus a single-process
  app does not benefit from an async driver, and the enforcement code in
  ``app.security`` / ``app.trust`` is deliberately written as pure synchronous
  functions over a ``Session`` so it stays unit-testable. Every route therefore hands
  its work to :func:`run_db`, which executes it on a worker thread. The asyncio event
  dispatcher is never blocked by disk I/O.

* **WAL plus a busy timeout.** The event dispatcher persists on a background task
  while request handlers write, so concurrent access is the normal case, not an edge
  case. ``journal_mode=WAL`` lets readers proceed during a write and
  ``busy_timeout`` makes a contended writer wait instead of raising immediately.

* **Foreign keys enforced.** SQLite disables FK enforcement per connection by default,
  so ``PRAGMA foreign_keys=ON`` is applied on every connect. Without it the integrity
  guarantees the schema implies would not actually hold.

* **Dialect-aware wiring.** ``connect_args`` and the PRAGMA hook are applied only for
  SQLite, so pointing ``DATABASE_URL`` at another SQLAlchemy-supported database works
  without editing this module. In-memory URLs additionally get a ``StaticPool`` so the
  schema and its data survive across sessions.

Referenced by: ``app.core.models``, every module under ``app.api.routes``,
``app.services.bootstrap``, ``app.orchestrator.demo_orchestrator``,
``app.blockchain.service``, ``scripts/verify_system.py``, ``scripts/verify_chain.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = logging.getLogger("trustmesh.database")

T = TypeVar("T")


class Base(DeclarativeBase):
    """Declarative base for every TrustMesh ORM model.

    Referenced by ``app.core.models`` and by ``create_all`` / ``drop_all`` below.
    """


# --------------------------------------------------------------------- dialect
_URL = settings.database_url
_IS_SQLITE = _URL.startswith("sqlite")
# ":memory:" and "sqlite://" (empty path) are per-connection databases. Without a
# static pool each session would get a fresh, empty database.
_IS_MEMORY_SQLITE = _IS_SQLITE and (":memory:" in _URL or _URL.rstrip("/") in {"sqlite:", "sqlite://"})

_connect_args: dict[str, Any] = {}
_engine_kwargs: dict[str, Any] = {}

if _IS_SQLITE:
    # ``check_same_thread`` must be off because sessions are created on one worker
    # thread and may be used on another; ``timeout`` maps to sqlite3's busy timeout.
    _connect_args = {"check_same_thread": False, "timeout": 30}
    if _IS_MEMORY_SQLITE:
        _engine_kwargs["poolclass"] = StaticPool

#: Process-wide engine. Created once at import; ``check_same_thread=False`` makes it
#: safe to use from the event loop's worker threads.
engine: Engine = create_engine(
    _URL,
    echo=settings.sql_echo,
    future=True,
    connect_args=_connect_args,
    **_engine_kwargs,
)

#: ``expire_on_commit=False`` keeps ORM instances readable after ``session.close()``,
#: which the orchestrator and the blockchain adapter both rely on.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

#: PRAGMAs applied to every new SQLite connection, in order.
_SQLITE_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "30000"),
    ("temp_store", "MEMORY"),
)


if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _record) -> None:
        """Apply the per-connection PRAGMAs SQLite requires.

        ``journal_mode`` and the timeouts must be set per connection; ``foreign_keys``
        in particular is connection-scoped state that defaults to OFF.
        """
        cursor = dbapi_connection.cursor()
        try:
            for pragma, value in _SQLITE_PRAGMAS:
                cursor.execute(f"PRAGMA {pragma}={value}")
        finally:
            cursor.close()


# ------------------------------------------------------------------- sessions
async def run_db(fn: Callable[[Session], T]) -> T:
    """Run ``fn(session)`` on a worker thread and return its result.

    This is the single entry point for database access from async code. ``fn`` receives
    a fresh session that is always closed, including on exception, so a failed request
    cannot leak a connection or hold a write lock.

    ``fn`` must commit its own writes (or discard them); this helper deliberately does
    not commit implicitly, so a caller that raises can roll back cleanly.

        rows = await run_db(lambda s: s.execute(select(Agent)).scalars().all())
    """

    def _call() -> T:
        session = SessionLocal()
        try:
            return fn(session)
        finally:
            session.close()

    return await asyncio.to_thread(_call)


async def init_db() -> None:
    """Create any missing tables.

    Safe to call on every boot: ``create_all`` issues ``CREATE TABLE IF NOT EXISTS``.
    Existing data is never touched.
    """
    await asyncio.to_thread(create_all)


def create_all() -> None:
    """Synchronous ``create_all``. Imports the models so mappers are registered."""
    from app.core import models  # noqa: F401  (side effect: mapper registration)

    Base.metadata.create_all(bind=engine)
    logger.debug("schema ensured for %s", _redacted_url())


def drop_all() -> None:
    """Drop every TrustMesh table.

    Destructive. Used by ``app.services.bootstrap.reset_registry`` tooling and by tests
    that need a guaranteed-empty schema.
    """
    from app.core import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    logger.warning("all TrustMesh tables dropped")


def reset_engine(runtime_url: str) -> None:
    """Rebind the engine to a different URL and reset its connection pool.

    Needed by tooling that has to switch to a throwaway database after import (the test
    suite and ``scripts/verify_system.py --temp-db`` do this via the environment, but a
    caller that only knows the URL at runtime can use this instead).
    """
    global engine, SessionLocal
    engine.dispose()
    engine = create_engine(
        runtime_url,
        echo=settings.sql_echo,
        future=True,
        connect_args=(
            {"check_same_thread": False, "timeout": 30}
            if runtime_url.startswith("sqlite")
            else {}
        ),
    )
    SessionLocal.configure(bind=engine)
    logger.info("engine rebound to %s", _redacted_url())


# --------------------------------------------------------------------- health
def ping() -> bool:
    """Cheap liveness probe: one round-trip, no ORM work.

    Returns ``False`` instead of raising so ``/api/health`` can report a degraded
    database without a 500.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - only on a broken file/permissions
        logger.warning("database ping failed: %s", exc)
        return False


def dialect_name() -> str:
    return engine.dialect.name


def journal_mode() -> str | None:
    """Report the active SQLite journal mode, or ``None`` on other dialects."""
    if not _IS_SQLITE:
        return None
    try:
        with engine.connect() as connection:
            return str(connection.execute(text("PRAGMA journal_mode")).scalar()).lower()
    except Exception:  # pragma: no cover
        return None


def table_names() -> list[str]:
    """Every mapped table name, sorted. Used by diagnostics and tests."""
    return sorted(Base.metadata.tables)


def _redacted_url() -> str:
    """URL with any embedded credentials removed, safe to log."""
    if "@" not in _URL:
        return _URL
    scheme, _, rest = _URL.partition("://")
    return f"{scheme}://***@{rest.rpartition('@')[2]}"


__all__ = [
    "Base",
    "SessionLocal",
    "create_all",
    "dialect_name",
    "drop_all",
    "engine",
    "init_db",
    "journal_mode",
    "ping",
    "reset_engine",
    "run_db",
    "table_names",
]
