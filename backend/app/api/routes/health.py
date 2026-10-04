"""Health, liveness and readiness routes.

The judge should be able to tell within one request whether the demo will work.
Reports the full component matrix: database, event bus, AI mode, chain mode.

Referenced by: ``app.main``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app import __version__
from app.agents.agent_registry import AGENTS, ORGANIZATIONS
from app.ai.gemini_client import gemini_client
from app.blockchain.service import blockchain_service
from app.core.config import settings
from app.core.database import ping as database_ping
from app.core.database import dialect_name as database_dialect
from app.core.database import journal_mode as database_journal_mode
from app.core.database import run_db
from app.core.events import dispatcher
from app.core.models import Agent
from app.core.schemas import ArchitectureOut, ComponentHealth, HealthOut, ReadinessOut

router = APIRouter(tags=["health"])

_STARTED_AT = time.monotonic()


def _probe_sync(session) -> tuple[bool, int]:
    session.execute(text("SELECT 1"))
    count = session.execute(select(func.count(Agent.id))).scalar()
    return True, int(count or 0)


async def _check_database() -> ComponentHealth:
    """Report the database component.

    Uses ``app.core.database.ping`` for the round-trip so the connection logic has one
    owner, and adds the journal mode because WAL is what lets the event dispatcher
    persist while request handlers read.
    """
    if not database_ping():
        return ComponentHealth(
            name="database",
            status="down",
            detail="SQLite did not answer a SELECT 1 round-trip",
            mode=database_dialect(),
        )
    try:
        _, agents = await run_db(_probe_sync)
    except Exception as exc:
        return ComponentHealth(
            name="database",
            status="down",
            detail=f"{type(exc).__name__}: {exc}",
            mode=database_dialect(),
        )

    journal = database_journal_mode()
    detail = f"{agents} agents registered"
    if journal:
        detail += f" · journal_mode={journal.upper()}"
    return ComponentHealth(
        name="database",
        status="up",
        detail=detail,
        mode=database_dialect(),
    )


async def _component_matrix() -> list[ComponentHealth]:
    """The five-component status matrix.

    Shared by ``GET /health`` and ``GET /overview`` so the dashboard's status cards and
    the health endpoint can never disagree about what is up.

    The chain is *probed* rather than read from cached state: without an actual RPC round
    trip the connection field is still its initial value, which would report a fully
    deployed, reachable contract as degraded.
    """
    db = await _check_database()
    chain_probe = await blockchain_service.probe()
    chain_status = blockchain_service.status()
    chain_status["connection"] = chain_probe.get("connection", chain_status["connection"])
    if chain_probe.get("latest_block") is not None:
        chain_status["latest_block"] = chain_probe["latest_block"]
    ai_status = gemini_client.status()
    bus = dispatcher.stats()

    contract_info = chain_status["contract"]
    contract_ready = bool(contract_info.get("ready"))
    chain_connected = chain_status["connection"] == "connected"

    return [
        db,
        ComponentHealth(
            name="event_dispatcher",
            status="up" if dispatcher.is_running else "down",
            detail=(
                f"seq={bus['seq']} · {dispatcher.subscriber_count} SSE subscriber(s) · "
                f"buffer={bus['history_size']}/{bus['buffer_size']}"
            ),
            mode="asyncio in-process bus",
        ),
        ComponentHealth(
            name="ai_reasoning",
            # The deterministic constraint solver is a fully functional reasoning
            # provider (spec §10), so this subsystem is available either way. Reporting
            # "degraded" for a missing API key would flag a designed mode as a fault.
            status="up",
            detail=(
                f"Gemini {ai_status['model']} · prompt {ai_status['prompt_version']}"
                if ai_status["configured"]
                else (
                    "Gemini key is a placeholder - deterministic solver active "
                    "(Demo Mode, spec §10)"
                    if settings.gemini_placeholder
                    else "Gemini not configured - deterministic solver active (Demo Mode, spec §10)"
                )
            ),
            mode=ai_status["last_mode"],
        ),
        ComponentHealth(
            name="policy_engine",
            status="up",
            detail="5 deterministic rules · no LLM in the enforcement path",
            mode="deterministic",
        ),
        ComponentHealth(
            name="blockchain",
            status="up" if chain_connected and contract_ready else "degraded",
            detail=(
                f"{chain_status['network']} · mode {chain_status['mode']} · "
                f"chain_id {chain_status['chain_id']} · contract "
                f"{contract_info['address'] or 'not deployed'} · "
                f"{contract_info['abi_entries']} ABI entries"
                + ("" if contract_ready else " · ABI unavailable")
            ),
            mode=chain_status["mode"],
        ),
    ]


@router.get("/health", response_model=HealthOut, summary="Liveness + component matrix")
async def health() -> HealthOut:
    components = await _component_matrix()
    db = next(c for c in components if c.name == "database")
    degraded = any(c.status != "up" for c in components)
    return HealthOut(
        status="degraded" if degraded else "ok",
        version=__version__,
        environment=settings.app_env,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 2),
        event_seq=dispatcher.stats()["seq"],
        sse_subscribers=dispatcher.subscriber_count,
        ai_mode=gemini_client.status()["last_mode"],
        blockchain_mode=blockchain_service.status()["mode"],
        database=db.detail,
        components=components,
        checked_at=datetime.now(timezone.utc),
    )


@router.get("/health/ready", response_model=ReadinessOut, summary="Readiness probe")
async def readiness() -> ReadinessOut:
    db_ok = await asyncio.to_thread(database_ping)
    registry_ok = len(AGENTS) > 0 and len(ORGANIZATIONS) > 0
    checks = {
        "database": db_ok,
        "event_dispatcher": dispatcher.is_running,
        "registry_seeded": registry_ok,
        "policy_engine": True,
        # AI and chain are explicitly NOT readiness gates: spec §34 requires the demo
        # to run without either.
        "ai_optional": True,
        "blockchain_optional": True,
    }
    return ReadinessOut(
        ready=all(v for k, v in checks.items() if not k.endswith("_optional")),
        checks=checks,
        checked_at=datetime.now(timezone.utc),
    )


@router.get(
    "/health/architecture",
    response_model=ArchitectureOut,
    summary="The enforcement pipeline, machine readable",
)
async def architecture() -> dict:
    """Feeds the About page's architecture diagram and the rule catalogue."""
    from app.security import capability_guard, policy_engine, threat_detection
    from app.trust.trust_engine import trust_engine

    return {
        "principle": "REASON -> ENFORCE -> VERIFY",
        "pipeline": list(trust_engine.pipeline),
        "authority": {
            "ai": "reasoning and negotiation only - never approves its own action",
            "trustmesh": (
                "identity, credentials, capabilities, authorization, deterministic policy, "
                "threat interception - the sole security authority"
            ),
            "blockchain": "tamper-evident commitment only - never a runtime decision",
        },
        "policy_rules": policy_engine.catalogue(),
        "threat_rules": threat_detection.catalogue(),
        "sentinel_capabilities": sorted(capability_guard.SENTINEL_CAPABILITIES),
        "known_capabilities": sorted(capability_guard.KNOWN_CAPABILITIES),
        "canonicalization": {
            "scheme": "RFC8785",
            "hash": "SHA-256",
            "implementation": "app.agreements.hashing (in-repo, dependency-free)",
        },
    }
