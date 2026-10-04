"""Capability Guard.

Stage 3 of the enforcement pipeline (spec §5 SCENARIO 1, §6). Answers:
"which capabilities does this agent possess, and is the requested one among them?"

Two independent gates:
* **Grant gate** - the requested capability must be in the agent's declared grant list.
* **Sentinel gate** - a set of sensitive capabilities is *never* grantable in this
  prototype, even if a registry entry were somehow corrupted to include it.
  Defence in depth: an allow-list bug cannot escalate into a credential read.

Referenced by: ``app.trust.trust_engine``, ``app.agents.*``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.models import Agent

# Capabilities that exist in the domain model.
KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "search",
        "request_quote",
        "negotiate",
        "propose",
        "accept_agreement",
        "submit_order",
        "read_public_catalog",
        # Deliberately restricted surface used by the controlled simulations.
        "buyer_api_credentials",
        "access_unrelated_company_data",
        "impersonate_organization",
        "exceed_authorized_budget",
        "unauthorized_payment",
    }
)

# Never grantable. A capability appearing here is blocked even if declared.
SENTINEL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "buyer_api_credentials",
        "access_unrelated_company_data",
        "impersonate_organization",
        "exceed_authorized_budget",
        "unauthorized_payment",
    }
)

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(slots=True)
class CapabilityResult:
    allowed: bool
    capability: str
    rule_id: str
    reason: str
    declared: list[str] = field(default_factory=list)
    effective: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_evidence(self) -> dict[str, object]:
        return {
            "requested_capability": self.capability,
            "declared_capabilities": self.declared,
            "effective_capabilities": self.effective,
            "missing_from_grant": self.missing,
            "rule_id": self.rule_id,
        }


def declared_capabilities(agent: Agent) -> list[str]:
    raw = agent.capabilities or []
    return [str(item) for item in raw]


def effective_capabilities(agent: Agent) -> list[str]:
    """Declared minus sentinel. This is the real authority ceiling."""
    declared = set(declared_capabilities(agent))
    return sorted(declared - SENTINEL_CAPABILITIES)


def check(
    session: Session,
    *,
    agent_id: str,
    capability: str,
    agent: Agent | None = None,
) -> CapabilityResult:
    """Validate one capability request against the agent's grants."""
    from app.trust.identity import get_agent

    if agent is None:
        agent = get_agent(session, agent_id)
    if agent is None:
        return CapabilityResult(
            allowed=False,
            capability=capability,
            rule_id="CAPABILITY_AGENT_UNKNOWN",
            reason="Capability cannot be validated: agent is not registered.",
        )

    declared = declared_capabilities(agent)
    effective = effective_capabilities(agent)

    if not capability or not _SNAKE_CASE.match(capability):
        return CapabilityResult(
            allowed=False,
            capability=capability or "",
            rule_id="CAPABILITY_MALFORMED",
            reason=f"'{capability}' is not a well-formed capability identifier.",
            declared=declared,
            effective=effective,
            missing=[capability or ""],
        )

    if capability in SENTINEL_CAPABILITIES:
        return CapabilityResult(
            allowed=False,
            capability=capability,
            rule_id="CAPABILITY_SENTINEL_DENY",
            reason=(
                f"Capability '{capability}' is a protected resource that no agent may hold. "
                "This is enforced independently of the agent's declared grants."
            ),
            declared=declared,
            effective=effective,
            missing=[capability],
        )

    if capability not in declared:
        return CapabilityResult(
            allowed=False,
            capability=capability,
            rule_id="CAPABILITY_NOT_AUTHORIZED",
            reason=(
                f"Capability '{capability}' is outside the capabilities authorized for "
                f"agent '{agent_id}'. Authorized: {', '.join(effective) or 'none'}."
            ),
            declared=declared,
            effective=effective,
            missing=[capability],
        )

    return CapabilityResult(
        allowed=True,
        capability=capability,
        rule_id="CAPABILITY_AUTHORIZED",
        reason=f"Capability '{capability}' is within the authorized capability set.",
        declared=declared,
        effective=effective,
    )
