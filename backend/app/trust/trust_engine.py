"""TrustMesh Gateway - the security authority.

This is the only component in the system that may return ALLOW for a consequential
action. It runs the seven-stage pipeline from spec §6 in a fixed order:

    schema -> identity -> credential -> capability -> authorization
           -> threat interception -> deterministic policy

Two structural guarantees are implemented here:

1. **No import path from ``app.ai`` to execution.** The AI layer returns data only;
   it can never reach :meth:`TrustEngine.request_action`'s caller without passing
   through this module.
2. **Fail closed.** Any unexpected exception inside the pipeline produces a BLOCK,
   never an ALLOW.

Referenced by: ``app.agents.*``, ``app.orchestrator.demo_orchestrator``,
``app.api.routes.security``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.events import EventType, dispatcher
from app.core.models import (
    Agent,
    Decision,
    PolicyDecision,
    PolicyStage,
    Transaction,
    new_id,
)
from app.security import capability_guard, threat_detection
from app.security.policy_engine import PolicyResult, ProposedTerms, TransactionConstraints
from app.security.security_events import AlertDraft, raise_alert, severity_for_rule
from app.trust import identity as identity_mod
from app.trust.credentials import verify_credential
from app.trust.trust_evaluator import TrustEvaluation, evaluate as evaluate_trust

logger = logging.getLogger("trustmesh.gateway")

# Actions that mutate state or create obligations and therefore must be enforced.
ENFORCED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "search",
        "request_quote",
        "negotiate",
        "propose",
        "accept_agreement",
        "submit_order",
    }
)


@dataclass(slots=True)
class ActionRequest:
    """A request from an agent to do something. Untrusted by construction."""

    agent_id: str
    capability: str
    action: str
    presented_secret: str | None = None
    claimed_org_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    transaction_id: str | None = None
    intent_ref: str = ""
    target_resource: str | None = None
    require_credential: bool = True
    action_id: str = field(default_factory=lambda: f"ACT_{uuid.uuid4().hex[:10]}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "action": self.action,
            "claimed_org_id": self.claimed_org_id,
            "transaction_id": self.transaction_id,
            "intent_ref": self.intent_ref,
            "target_resource": self.target_resource,
            "parameters": self.parameters,
        }


@dataclass(slots=True)
class GatewayDecision:
    """The auditable output of the enforcement pipeline."""

    decision: str
    action_id: str
    agent_id: str
    capability: str
    stage: str
    rule_id: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    identity: identity_mod.IdentityResult | None = None
    policy: PolicyResult | None = None
    trust: TrustEvaluation | None = None
    alert_id: str | None = None
    severity: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW.value

    @property
    def blocked(self) -> bool:
        return self.decision == Decision.BLOCK.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "decision": self.decision,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "stage": self.stage,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "alert_id": self.alert_id,
            "severity": self.severity,
            "identity": self.identity.as_evidence() if self.identity else None,
            "policy": self.policy.as_evidence() if self.policy else None,
            "trust": self.trust.as_dict() if self.trust else None,
            "evidence": self.evidence,
        }


class TrustEngine:
    """Stateless façade over the enforcement stages (one instance per process)."""

    def __init__(self) -> None:
        self.pipeline = (
            "schema",
            "identity",
            "credential",
            "capability",
            "authorization",
            "threat",
            "policy",
        )

    # ------------------------------------------------------------------ main
    async def request_action(
        self,
        session: Session,
        request: ActionRequest,
        *,
        constraints: TransactionConstraints | None = None,
        allow_unauthenticated: bool = False,
    ) -> GatewayDecision:
        """Run the full pipeline. Never raises; fails closed on error."""
        try:
            return await self._run(session, request, constraints, allow_unauthenticated)
        except Exception as exc:  # pragma: no cover - defensive, fail closed
            logger.exception("gateway pipeline error, failing closed")
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                stage=PolicyStage.POLICY.value,
                rule_id="GATEWAY_INTERNAL_ERROR",
                reason=f"Enforcement pipeline failed closed: {type(exc).__name__}.",
                evidence={"error_type": type(exc).__name__},
            )
            await self._record(session, request, decision)
            session.commit()
            return decision

    async def _run(
        self,
        session: Session,
        request: ActionRequest,
        constraints: TransactionConstraints | None,
        allow_unauthenticated: bool,
    ) -> GatewayDecision:
        agent = identity_mod.get_agent(session, request.agent_id)

        # ---- stage 1: schema -------------------------------------------------
        if not request.capability or not request.action:
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability or "",
                stage=PolicyStage.SCHEMA.value,
                rule_id="SCHEMA_INVALID",
                reason="Action request is missing a capability or action name.",
            )
            await self._finish_block(session, request, decision, agent)
            return decision

        if agent is None:
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                # An unregistered agent is an identity failure, not a malformed request.
                stage=PolicyStage.IDENTITY.value,
                rule_id="IDENTITY_UNKNOWN_AGENT",
                reason=(
                    f"Agent '{request.agent_id}' is not present in the TrustMesh registry "
                    "and cannot hold capabilities."
                ),
                evidence={"agent_id": request.agent_id},
            )
            await self._finish_block(session, request, decision, None)
            return decision

        # ---- stage 2: identity ----------------------------------------------
        await dispatcher.publish(
            EventType.POLICY_CHECK_STARTED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload={
                "action_id": request.action_id,
                "stage": "identity",
                "capability": request.capability,
                "action": request.action,
                "pipeline": list(self.pipeline),
            },
        )

        identity_result = identity_mod.verify_identity(
            session,
            agent_id=request.agent_id,
            claimed_org_id=request.claimed_org_id,
            presented_secret=request.presented_secret,
            require_credential=request.require_credential and not allow_unauthenticated,
        )

        if not identity_result.verified:
            identity_mod.mark_verified(session, request.agent_id, False)
            session.commit()
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                stage=PolicyStage.IDENTITY.value,
                rule_id=identity_result.rule_id,
                reason=identity_result.reason,
                evidence=identity_result.as_evidence(),
                identity=identity_result,
            )
            await self._finish_block(session, request, decision, agent)
            return decision

        identity_mod.mark_verified(session, request.agent_id, True)
        session.commit()
        await dispatcher.publish(
            EventType.IDENTITY_VERIFIED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload={
                "action_id": request.action_id,
                "agent_id": request.agent_id,
                "organization_id": identity_result.registered_org_id,
                "organization_name": identity_result.organization_name,
                "rule_id": identity_result.rule_id,
                "factors": identity_result.factors,
            },
        )

        # ---- stage 3: credential --------------------------------------------
        if not allow_unauthenticated:
            credential_result = verify_credential(
                session,
                agent_id=request.agent_id,
                presented_secret=request.presented_secret or "",
                org_id=identity_result.registered_org_id,
            )
            if not credential_result.valid:
                decision = GatewayDecision(
                    decision=Decision.BLOCK.value,
                    action_id=request.action_id,
                    agent_id=request.agent_id,
                    capability=request.capability,
                    stage=PolicyStage.CREDENTIAL.value,
                    rule_id=credential_result.rule_id,
                    reason=credential_result.reason,
                    evidence={
                        "credential_rule_id": credential_result.rule_id,
                        "credential_id": credential_result.credential_id,
                    },
                    identity=identity_result,
                )
                await self._finish_block(session, request, decision, agent)
                return decision

        # ---- stage 4: capability --------------------------------------------
        capability_result = capability_guard.check(
            session, agent_id=request.agent_id, capability=request.capability, agent=agent
        )
        await dispatcher.publish(
            EventType.CAPABILITY_CHECKED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload={
                "action_id": request.action_id,
                "requested_capability": request.capability,
                "allowed": capability_result.allowed,
                "rule_id": capability_result.rule_id,
                "reason": capability_result.reason,
                **capability_result.as_evidence(),
            },
        )
        if not capability_result.allowed:
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                stage=PolicyStage.CAPABILITY.value,
                rule_id=capability_result.rule_id,
                reason=capability_result.reason,
                evidence=capability_result.as_evidence(),
                identity=identity_result,
            )
            await self._finish_block(session, request, decision, agent)
            return decision

        # ---- stage 5: authorization -----------------------------------------
        authorization = self._check_authorization(session, agent, request, identity_result)
        if not authorization.allowed:
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                stage=PolicyStage.AUTHORIZATION.value,
                rule_id=authorization.rule_id,
                reason=authorization.reason,
                evidence=authorization.as_evidence(),
                identity=identity_result,
            )
            await self._finish_block(session, request, decision, agent)
            return decision

        # ---- stage 6: threat interception -----------------------------------
        threat_result = threat_detection.evaluate(
            capability=request.capability,
            action=request.action,
            parameters=request.parameters,
        )
        if threat_result.malicious:
            decision = GatewayDecision(
                decision=Decision.BLOCK.value,
                action_id=request.action_id,
                agent_id=request.agent_id,
                capability=request.capability,
                stage=PolicyStage.THREAT.value,
                rule_id=threat_result.rule_id,
                reason=threat_result.reason,
                evidence=threat_result.as_evidence(),
                identity=identity_result,
                severity=threat_result.signals[0].severity if threat_result.signals else None,
            )
            await self._finish_block(session, request, decision, agent)
            return decision

        # ---- stage 7: deterministic policy ----------------------------------
        policy_result: PolicyResult | None = None
        if constraints is not None:
            await dispatcher.publish(
                EventType.POLICY_CHECK_STARTED,
                transaction_id=request.transaction_id,
                agent_id=request.agent_id,
                payload={
                    "action_id": request.action_id,
                    "stage": "policy",
                    "rule_set": [name for name, _ in policy_engine_rules()],
                    "constraints": constraints.as_dict(),
                },
            )
            terms = ProposedTerms(
                price_cents=_as_int(request.parameters.get("price_cents")),
                delivery_days=_as_int(request.parameters.get("delivery_days")),
                certification=_as_str(request.parameters.get("certification")),
                quantity=_as_int(request.parameters.get("quantity")),
                product=_as_str(request.parameters.get("product")),
            )
            policy_result = self.evaluate_policy(
                constraints, terms, identity_verified=identity_result.verified
            )
            if not policy_result.allowed:
                decision = GatewayDecision(
                    decision=Decision.BLOCK.value,
                    action_id=request.action_id,
                    agent_id=request.agent_id,
                    capability=request.capability,
                    stage=PolicyStage.POLICY.value,
                    rule_id=policy_result.rule_id,
                    reason=policy_result.reason,
                    evidence=policy_result.as_evidence(),
                    identity=identity_result,
                    policy=policy_result,
                )
                await self._finish_block(session, request, decision, agent)
                return decision

        trust = evaluate_trust(
            session,
            agent,
            identity_status="VERIFIED",
            credential_status="VALID" if agent.credential_id else "MISSING",
            capability_status="AUTHORIZED",
            authorization_status="GRANTED",
            threat_status="CLEAN",
            policy_status="COMPLIANT" if policy_result else "NOT_EVALUATED",
            requested_capability=request.capability,
        )
        await dispatcher.publish(
            EventType.TRUST_EVALUATED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload=trust.as_dict(),
        )

        decision = GatewayDecision(
            decision=Decision.ALLOW.value,
            action_id=request.action_id,
            agent_id=request.agent_id,
            capability=request.capability,
            stage="complete",
            rule_id="GATEWAY_ALLOW",
            reason=(
                "Identity, credential, capability, authorization, threat and deterministic "
                "policy checks all passed."
            ),
            evidence={
                "authorization": authorization.as_evidence(),
                "identity": identity_result.as_evidence(),
                "capability": capability_result.as_evidence(),
                "threat": threat_result.as_evidence(),
                "pipeline": list(self.pipeline),
            },
            identity=identity_result,
            policy=policy_result,
            trust=trust,
        )
        await self._record(session, request, decision)
        session.commit()
        await dispatcher.publish(
            EventType.POLICY_ALLOWED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload={
                "action_id": request.action_id,
                "capability": request.capability,
                "action": request.action,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "policy": policy_result.as_evidence() if policy_result else None,
                "trust_tier": trust.tier,
            },
        )
        return decision

    # ---------------------------------------------------------- authorization
    def _check_authorization(
        self,
        session: Session,
        agent: Agent,
        request: ActionRequest,
        identity_result: identity_mod.IdentityResult,
    ) -> capability_guard.CapabilityResult:
        """Scope and cross-organization authorization for the specific action."""
        if request.capability not in ENFORCED_CAPABILITIES:
            return capability_guard.CapabilityResult(
                allowed=False,
                capability=request.capability,
                rule_id="AUTHORIZATION_SCOPE_VIOLATION",
                reason=(
                    f"Capability '{request.capability}' is not in the enforceable action scope."
                ),
                declared=capability_guard.declared_capabilities(agent),
                effective=capability_guard.effective_capabilities(agent),
                missing=[request.capability],
            )

        declared = set(capability_guard.declared_capabilities(agent))
        if request.capability not in declared:
            return capability_guard.CapabilityResult(
                allowed=False,
                capability=request.capability,
                rule_id="AUTHORIZATION_SCOPE_VIOLATION",
                reason=(
                    f"Agent '{agent.id}' is not authorized to perform '{request.capability}' "
                    "on any resource."
                ),
                declared=sorted(declared),
                effective=capability_guard.effective_capabilities(agent),
                missing=[request.capability],
            )

        # Cross-organization rule: an agent may never name a resource owned by an
        # organization other than its own unless the request is inbound to it.
        target_org = _as_str(request.parameters.get("target_org_id"))
        if target_org and target_org != agent.org_id:
            inbound = bool(request.parameters.get("inbound_to_agent"))
            if not inbound:
                return capability_guard.CapabilityResult(
                    allowed=False,
                    capability=request.capability,
                    rule_id="AUTHORIZATION_CROSS_ORG",
                    reason=(
                        f"Agent '{agent.id}' (org '{agent.org_id}') may not act on resources "
                        f"owned by organization '{target_org}'. Cross-organization access must "
                        "arrive as an inbound request to the owning agent."
                    ),
                    declared=sorted(declared),
                    effective=capability_guard.effective_capabilities(agent),
                    missing=[request.capability],
                )

        return capability_guard.CapabilityResult(
            allowed=True,
            capability=request.capability,
            rule_id="AUTHORIZATION_GRANTED",
            reason=(
                f"Agent '{agent.id}' is authorized for '{request.capability}' within its "
                f"organization scope."
            ),
            declared=sorted(declared),
            effective=capability_guard.effective_capabilities(agent),
        )

    # ------------------------------------------------------------------ policy
    @staticmethod
    def evaluate_policy(
        constraints: TransactionConstraints,
        terms: ProposedTerms,
        *,
        identity_verified: bool = True,
    ) -> PolicyResult:
        from app.security import policy_engine

        return policy_engine.evaluate(
            constraints, terms, identity_verified=identity_verified
        )

    # --------------------------------------------------------------- recording
    async def _finish_block(
        self,
        session: Session,
        request: ActionRequest,
        decision: GatewayDecision,
        agent: Agent | None,
    ) -> None:
        """Record a block: policy decision row + security alert + POLICY_BLOCKED."""
        await self._record(session, request, decision)

        org = agent.org_id if agent else (request.claimed_org_id or "unknown")
        org_name = ""
        if agent is not None and agent.organization is not None:
            org_name = agent.organization.legal_name

        alert = await raise_alert(
            session,
            AlertDraft(
                agent_id=request.agent_id,
                agent_name=agent.display_name if agent else request.agent_id,
                organization_id=org,
                organization_name=org_name,
                alert_type=decision.rule_id,
                severity=decision.severity or severity_for_rule(decision.rule_id),
                requested_action=request.action,
                requested_capability=request.capability,
                reason=decision.reason,
                rule_id=decision.rule_id,
                stage=decision.stage,
                evidence=decision.evidence,
                transaction_id=request.transaction_id,
                decision=Decision.BLOCK.value,
                is_simulation=bool(agent.is_malicious_simulation) if agent else True,
            ),
        )
        session.commit()
        decision.alert_id = alert.id
        decision.severity = alert.severity

        if decision.stage == PolicyStage.IDENTITY.value:
            await dispatcher.publish(
                EventType.IDENTITY_REJECTED,
                transaction_id=request.transaction_id,
                agent_id=request.agent_id,
                payload={
                    "action_id": request.action_id,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "alert_id": alert.id,
                    "evidence": decision.evidence,
                },
            )

        await dispatcher.publish(
            EventType.POLICY_BLOCKED,
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            payload={
                "action_id": request.action_id,
                "capability": request.capability,
                "action": request.action,
                "stage": decision.stage,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "severity": alert.severity,
                "alert_id": alert.id,
                "evidence": decision.evidence,
                "pipeline": list(self.pipeline),
            },
        )

    @staticmethod
    async def _record(
        session: Session, request: ActionRequest, decision: GatewayDecision
    ) -> PolicyDecision:
        row = PolicyDecision(
            id=new_id("PD"),
            transaction_id=request.transaction_id,
            agent_id=request.agent_id,
            organization_id=decision.identity.registered_org_id
            if decision.identity
            else request.claimed_org_id,
            action_id=request.action_id,
            capability=request.capability,
            decision=decision.decision,
            stage=decision.stage,
            rule_id=decision.rule_id,
            reason=decision.reason,
            evidence=decision.evidence or {},
            intent_ref=request.intent_ref,
        )
        session.add(row)
        session.flush()
        return row


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def policy_engine_rules() -> tuple[tuple[str, Any], ...]:
    from app.security.policy_engine import RULES

    return RULES


trust_engine = TrustEngine()

__all__ = [
    "ActionRequest",
    "ENFORCED_CAPABILITIES",
    "GatewayDecision",
    "TrustEngine",
    "trust_engine",
]
