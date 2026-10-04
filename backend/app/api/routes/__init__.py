"""API route modules.

Every router in the TrustMesh control plane is listed here exactly once so
``app.main`` has a single mount point and route ordering stays explicit. Order matters:
``agents.registry-catalogue`` must be matched before ``agents/{agent_id}``, which each
module handles internally by declaring literal paths first.

Adding a router means adding it to ``ROUTERS`` and mounting it under the configured
API prefix.
"""

from fastapi import APIRouter

from app.api.routes import (
    agents,
    blockchain,
    events,
    health,
    overview,
    security,
    transactions,
)

#: Mounted in this order under ``settings.api_prefix``.
ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    overview.router,
    agents.router,
    transactions.router,
    security.router,
    blockchain.router,
    events.router,
)

__all__ = [
    "ROUTERS",
    "agents",
    "blockchain",
    "events",
    "health",
    "overview",
    "security",
    "transactions",
]
