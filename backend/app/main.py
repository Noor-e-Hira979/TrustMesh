"""TrustMesh FastAPI application factory.

Wires the routers, owns the lifespan (database init -> dispatcher start -> registry
seed), installs a redacting log filter so secrets can never reach stdout, and mounts
the CORS policy for the Vite dev server.

Start with:  python -m uvicorn app.main:app --reload --port 8000

Referenced by: ``backend/scripts/verify_system.py``, the frontend dev server.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import ROUTERS
from app.core.config import settings
from app.core.database import init_db
from app.core.events import EventType, dispatcher
from app.services.bootstrap import seed_registry

logger = logging.getLogger("trustmesh")

# --------------------------------------------------------------- log redaction
_SECRET_PATTERNS = [
    re.compile(r"(0x)?[0-9a-fA-F]{64}"),  # any 32-byte hex blob, incl. private keys
    re.compile(r"(?i)(private_key|api_key|secret|token|password)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]


class RedactingFilter(logging.Filter):
    """Last line of defence for the 'never log private keys' requirement."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    for noisy in ("httpx", "httpcore", "urllib3", "web3", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    started = time.monotonic()
    logger.info("TrustMesh %s starting in %s mode", __version__, settings.app_env)

    await init_db()
    seeded = await seed_registry()
    await dispatcher.start()
    await dispatcher.publish(
        EventType.SYSTEM_READY,
        payload={
            "version": __version__,
            "environment": settings.app_env,
            "ai_mode": "gemini" if settings.gemini_configured else "demo",
            "blockchain_mode": settings.effective_blockchain_mode,
            "seeded": seeded,
            "startup_seconds": round(time.monotonic() - started, 3),
        },
    )
    logger.info(
        "TrustMesh ready in %.2fs · ai=%s · chain=%s · %s agents",
        time.monotonic() - started,
        "gemini" if settings.gemini_configured else "demo(fallback)",
        settings.effective_blockchain_mode,
        seeded["total_agents"],
    )
    try:
        yield
    finally:
        await dispatcher.stop()
        logger.info("TrustMesh stopped")


DESCRIPTION = """
**TrustMesh** — Secure Trust Infrastructure for Autonomous AI Agents.

`REASON → ENFORCE → VERIFY`

* **AI / Gemini** reasons and proposes. It is never the security authority.
* **TrustMesh Gateway** verifies identity, credentials, capabilities and
  authorization, then applies a deterministic policy engine that emits ALLOW or BLOCK.
* **Blockchain** records a tamper-evident SHA-256 commitment of the RFC 8785 canonical
  agreement. It never stores sensitive data and never decides anything.

This is a hackathon prototype. It is not a production-certified system and makes no
such claim.
"""

app = FastAPI(
    title="TrustMesh Control Plane",
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Accel-Buffering"],
)

for _router in ROUTERS:
    app.include_router(_router, prefix=settings.api_prefix)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace or a secret to the client."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "TrustMesh hit an internal error. The enforcement path fails closed, so no "
                "unauthorised action was executed."
            ),
            "code": type(exc).__name__,
            "path": request.url.path,
        },
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "TrustMesh",
        "tagline": "Secure Trust Infrastructure for Autonomous AI Agents",
        "principle": "REASON -> ENFORCE -> VERIFY",
        "version": __version__,
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "sse": f"{settings.api_prefix}/events/stream",
        "demo": f"POST {settings.api_prefix}/transactions/demo",
        "disclaimer": (
            "Hackathon prototype. Simulated threat scenarios only. No production readiness "
            "or certification is claimed."
        ),
    }
