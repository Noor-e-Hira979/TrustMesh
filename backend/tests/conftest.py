"""Shared pytest fixtures.

Every test runs against a throwaway SQLite database so the suite never touches the
developer's real ``trustmesh.db``.

Referenced by: all modules under ``backend/tests``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_TMP_DB.name).as_posix()}")
os.environ.setdefault("BLOCKCHAIN_MODE", "simulated")
os.environ.setdefault("BLOCKCHAIN_SIMULATED_LATENCY_SECONDS", "0")
os.environ.setdefault("GEMINI_API_KEY", "")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.core.events import dispatcher  # noqa: E402
from app.services.bootstrap import reset_registry, seed_registry  # noqa: E402


@pytest_asyncio.fixture(scope="function")
async def db() -> None:
    await init_db()

    def _clean(session):
        reset_registry(session)

    from app.core.database import run_db

    await run_db(_clean)
    await seed_registry()
    yield


@pytest_asyncio.fixture(scope="function")
async def bus() -> None:
    await dispatcher.start()
    yield
    await dispatcher.stop()


@pytest_asyncio.fixture(scope="function")
async def session(db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
