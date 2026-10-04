"""Demo Orchestrator - the deterministic spine of the whole product.

This is the single stateful owner of the §23 demonstration sequence. Every beat of
the hackathon story is an explicit, ordered step here:

    intent -> discovery -> identity verification -> Supplier A offer -> rejection
    -> Supplier B offer -> negotiation -> Supplier C attacks -> blocks
    -> agreement -> RFC 8785 -> SHA-256 -> blockchain commitment

Determinism (spec §10):
* The numbers the Policy Engine judges come from the constraint solver, never from the
  LLM. Gemini only supplies rationale language.
* Every Gemini call has a deterministic fallback in the same typed interface, so the
  run cannot fail because of model output.
* A security event never aborts the legitimate transaction (TEST 2).

Referenced by: ``app.api.routes.transactions``, ``app.api.routes.security``,
``scripts/verify_system.py``, ``tests/test_demo_flow.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent_registry import (
    AGENTS_BY_ID,
    BUYER_AGENT_ID,
    DEMO_INTENT,
    IMPOSTOR_AGENT_ID,
    SUPPLIER_A_AGENT_ID,
    SUPPLIER_B_AGENT_ID,
    SUPPLIER_C_AGENT_ID,
    behaviour,
    credential_for,
    name_of,
)
from app.agents.buyer_agent import BuyerAgent, BuyerIntent
from app.agents.impersonator_agent import ImpersonatorAgent
from app.agents.negotiation import NegotiationEngine, scripted_over_budget_offer
from app.agents.supplier_agent import OfferTerms, make_supplier
from app.ai.gemini_client import gemini_client
from app.agreements.agreement_engine import (
    build_agreement,
    canonicalize_agreement,
    persist_commitment,
)
from app.blockchain.service import blockchain_service
from app.core.config import settings
from app.core.database import run_db
from app.core.events import EventType, dispatcher
from app.core.models import (
    Offer,
    Transaction,
    TransactionState,
    utcnow,
)
from app.security.policy_engine import ProposedTerms, TransactionConstraints

logger = logging.getLogger("trustmesh.orchestrator")


@dataclass(slots=True)
class StepRecord:
    index: int
    stage: str
    layer: str
    title: str
    detail: str
    outcome: str
    agent_id: str | None = None
    event_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "stage": self.stage,
            "layer": self.layer,
            "title": self.title,
            "detail": self.detail,
            "outcome": self.outcome,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "meta": self.meta,
        }


@dataclass(slots=True)
class DemoRunResult:
    transaction_id: str
    state: str
    steps: list[StepRecord]
    blocked_actions: int
    security_alerts: int
    agreement_hash: str | None
    commitment_status: str | None
    ai_modes: list[str]
    errors: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "state": self.state,
            "steps": [s.as_dict() for s in self.steps],
            "blocked_actions": self.blocked_actions,
            "security_alerts": self.security_alerts,
            "agreement_hash": self.agreement_hash,
            "commitment_status": self.commitment_status,
            "ai_modes": sorted(set(self.ai_modes)),
            "errors": self.errors,
        }


class DemoOrchestrator:
    """Runs the full procurement + security + agreement narrative."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    # ------------------------------------------------------------- utilities
    @staticmethod
    async def _sleep(seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)

    @staticmethod
    def _constraints(txn: Transaction) -> TransactionConstraints:
        return TransactionConstraints.from_transaction(txn)

    async def _set_state(
        self,
        txn_id: str,
        state: TransactionState,
        note: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        def _apply(session: Session) -> TransactionState:
            txn = session.get(Transaction, txn_id)
            if txn is None:
                return state
            txn.state = state.value
            txn.updated_at = utcnow()
            session.commit()
            return state

        applied = await run_db(_apply)
        await dispatcher.publish(
            EventType.TRANSACTION_STATE_CHANGED,
            transaction_id=txn_id,
            agent_id=agent_id,
            payload={"state": applied.value, "note": note},
        )

    async def _record_offer(
        self,
        txn_id: str,
        agent_id: str,
        org_id: str,
        terms: OfferTerms,
        ai_mode: str,
    ) -> str:
        def _apply(session: Session) -> str:
            row = Offer(
                transaction_id=txn_id,
                agent_id=agent_id,
                organization_id=org_id,
                round_number=terms.round_number,
                price_cents=terms.price_cents,
                delivery_days=terms.delivery_days,
                quantity=terms.quantity,
                certification=terms.certification,
                offer_type=terms.offer_type,
                rationale=terms.rationale,
                ai_mode=ai_mode,
            )
            session.add(row)
            session.commit()
            return row.id

        return await run_db(_apply)

    async def _bump_blocks(self, txn_id: str, delta: int = 1) -> None:
        def _apply(session: Session) -> None:
            txn = session.get(Transaction, txn_id)
            if txn is not None:
                txn.block_count = (txn.block_count or 0) + delta
                txn.updated_at = utcnow()
                session.commit()

        await run_db(_apply)

    async def _set_round(self, txn_id: str, round_number: int) -> None:
        def _apply(session: Session) -> None:
            txn = session.get(Transaction, txn_id)
            if txn is not None:
                txn.negotiation_round = round_number
                session.commit()

        await run_db(_apply)

    # ------------------------------------------------------------ entrypoint
    async def run_demo(
        self,
        *,
        step_delay: float | None = None,
        use_ai: bool = True,
        commit_on_chain: bool = True,
        intent: dict[str, Any] | None = None,
        transaction_id: str | None = None,
    ) -> DemoRunResult:
        """Execute the whole §23 sequence. Never raises.

        ``transaction_id`` runs the demo against an already-created transaction row, so
        the API can return a real, followable id the moment the request is accepted
        instead of a placeholder. When omitted, a transaction is created here.
        """
        delay = settings.demo_step_delay_seconds if step_delay is None else step_delay
        intent_data = {**DEMO_INTENT, **(intent or {})}
        steps: list[StepRecord] = []
        ai_modes: list[str] = []
        errors: list[str] = []
        counter = 0

        def step(
            stage: str,
            layer: str,
            title: str,
            detail: str,
            outcome: str = "INFO",
            agent_id: str | None = None,
            event_type: str | None = None,
            **meta: Any,
        ) -> None:
            nonlocal counter
            counter += 1
            steps.append(
                StepRecord(
                    index=counter,
                    stage=stage,
                    layer=layer,
                    title=title,
                    detail=detail,
                    outcome=outcome,
                    agent_id=agent_id,
                    event_type=event_type,
                    meta=meta,
                )
            )

        try:
            if transaction_id:
                # Run against a transaction the API already created, so the caller could
                # return a real, followable id the instant the request was accepted.
                txn_id = transaction_id
                loaded = await run_db(lambda s: _load_intent(s, txn_id))
                if loaded:
                    intent_data = loaded
            else:
                txn_id = await self._create_transaction(intent_data, use_ai=use_ai)
        except Exception as exc:
            logger.exception("failed to create demo transaction")
            errors.append(f"transaction creation failed: {exc}")
            return DemoRunResult(
                transaction_id="",
                state="failed",
                steps=steps,
                blocked_actions=0,
                security_alerts=0,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        buyer_intent = BuyerIntent(
            product=intent_data["product"],
            quantity=intent_data["quantity"],
            max_budget_cents=intent_data["max_budget_cents"],
            max_delivery_days=intent_data["max_delivery_days"],
            certification_required=intent_data.get("certification_required", "VALID"),
            intent_ref=f"INTENT-{txn_id}",
            transaction_id=txn_id,
        )
        buyer = BuyerAgent(buyer_intent)

        await dispatcher.publish(
            EventType.INTENT_RECEIVED,
            transaction_id=txn_id,
            agent_id=BUYER_AGENT_ID,
            payload={
                "buyer_agent_id": BUYER_AGENT_ID,
                "buyer_org_id": intent_data["buyer_org_id"],
                "intent": buyer_intent.as_dict(),
            },
        )
        step(
            "intent",
            "AGENT",
            "Buyer intent received",
            (
                f"{buyer_intent.quantity:,} {buyer_intent.product} · budget  "
                f"${buyer_intent.max_budget_cents / 100:,.2f} · delivery  "
                f"{buyer_intent.max_delivery_days} days · certification "
                f"{buyer_intent.certification_required}"
            ),
            event_type=EventType.INTENT_RECEIVED.value,
            agent_id=BUYER_AGENT_ID,
            intent=buyer_intent.as_dict(),
        )
        await self._sleep(delay)

        await self._set_state(
            txn_id,
            TransactionState.DISCOVERING,
            "buyer searching for suppliers",
            agent_id=BUYER_AGENT_ID,
        )

        # ---- 1. discovery -------------------------------------------------
        def _discover(session: Session) -> None:
            from app.trust import identity as identity_mod

            identity_mod.touch(
                session,
                BUYER_AGENT_ID,
                "searching supplier marketplace for packaging capacity",
            )
            session.commit()

        await run_db(_discover)
        await buyer.search_suppliers(
            await self._session(),
            [SUPPLIER_A_AGENT_ID, SUPPLIER_B_AGENT_ID, SUPPLIER_C_AGENT_ID],
        )
        step(
            "discovery",
            "TRUSTMESH",
            "Supplier agents discovered",
            (
                "Capability 'search' was validated against the buyer's grants before the "
                "lookup ran. Supplier A, Supplier B and Supplier C were returned."
            ),
            outcome="ALLOW",
            event_type=EventType.AGENT_DISCOVERED.value,
            agent_id=BUYER_AGENT_ID,
            discovered=[SUPPLIER_A_AGENT_ID, SUPPLIER_B_AGENT_ID, SUPPLIER_C_AGENT_ID],
        )
        await self._sleep(delay)

        # ---- 2. identity verification (incl. the impostor) ----------------
        await self._set_state(
            txn_id, TransactionState.VERIFYING_IDENTITY, "verifying counterparty identities"
        )
        verified: list[str] = []
        rejected: list[str] = []
        for counterparty in (SUPPLIER_A_AGENT_ID, SUPPLIER_B_AGENT_ID, SUPPLIER_C_AGENT_ID):
            decision = await self._verify_counterparty(txn_id, counterparty)
            (verified if decision else rejected).append(counterparty)
            await self._sleep(delay * 0.4)

        step(
            "identity",
            "TRUSTMESH",
            "Counterparty identities verified",
            (
                f"{len(verified)} of 3 counterparty agents produced a valid identity + "
                "credential binding. Identity is checked against the registry, never "
                "self-asserted."
            ),
            outcome="ALLOW",
            event_type=EventType.IDENTITY_VERIFIED.value,
            verified=verified,
        )

        impostor_blocked = await self._run_impersonation(txn_id)
        step(
            "identity",
            "TRUSTMESH",
            "Identity mismatch / impersonation blocked",
            (
                "An unregistered agent claimed to represent Supplier A with a better offer. "
                "TrustMesh compared the claim against the registry and blocked it."
            ),
            outcome="BLOCK",
            event_type=EventType.IDENTITY_REJECTED.value,
            agent_id=IMPOSTOR_AGENT_ID,
            blocked=impostor_blocked,
        )
        await self._sleep(delay)

        if SUPPLIER_A_AGENT_ID in rejected or SUPPLIER_B_AGENT_ID in rejected:
            await self._set_state(
                txn_id, TransactionState.BLOCKED, "no verified counterparty available"
            )
            errors.append("no verified counterparty remained after identity verification")
            return DemoRunResult(
                transaction_id=txn_id,
                state=TransactionState.BLOCKED.value,
                steps=steps,
                blocked_actions=len(rejected) + (1 if impostor_blocked else 0),
                security_alerts=0,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        # ---- 3. quote request ---------------------------------------------
        await buyer.request_quote(await self._session(), SUPPLIER_A_AGENT_ID, txn_id)
        await buyer.request_quote(await self._session(), SUPPLIER_B_AGENT_ID, txn_id)

        # ---- 4. Supplier A: offer rejected on delivery ---------------------
        await self._set_state(txn_id, TransactionState.EVALUATING, "evaluating Supplier A")
        supplier_a = make_supplier(SUPPLIER_A_AGENT_ID, "org_supplier_a", buyer_intent.quantity)
        offer_a = supplier_a.initial_offer()
        if use_ai:
            ai_result = await gemini_client.supplier_rationale(
                supplier_name=name_of(SUPPLIER_A_AGENT_ID),
                constraints=buyer.constraints,
                price_cents=offer_a.price_cents,
                delivery_days=offer_a.delivery_days,
                certification=offer_a.certification,
                base_rationale=offer_a.rationale,
            )
            offer_a.rationale = str(ai_result.value.rationale)
            ai_modes.append(ai_result.mode)
            await self._emit_ai(
                txn_id,
                SUPPLIER_A_AGENT_ID,
                ai_result.mode,
                offer_a.rationale,
                ai_result.fallback_reason,
            )
        await self._record_offer(
            txn_id, SUPPLIER_A_AGENT_ID, "org_supplier_a", offer_a, "gemini" if use_ai else "demo"
        )
        decision_a = await supplier_a.submit_offer(
            await self._session(),
            txn_id,
            offer_a,
            buyer_agent_id=BUYER_AGENT_ID,
            buyer_org_id="org_buyer",
            constraints=buyer.constraints,
        )
        if decision_a.blocked:
            await self._bump_blocks(txn_id)
        step(
            "offer",
            "AGENT",
            "Supplier A offer: $1,850 / 10 days",
            offer_a.rationale,
            outcome="INFO",
            agent_id=SUPPLIER_A_AGENT_ID,
            offer=offer_a.as_dict(),
        )
        step(
            "policy",
            "TRUSTMESH",
            "Supplier A rejected by deterministic policy",
            decision_a.reason,
            outcome="BLOCK",
            event_type=EventType.POLICY_BLOCKED.value,
            agent_id=SUPPLIER_A_AGENT_ID,
            rule_id=decision_a.rule_id,
        )
        await self._sleep(delay)

        # ---- 5. Supplier B: negotiation -----------------------------------
        await self._set_state(txn_id, TransactionState.NEGOTIATING, "negotiating with Supplier B")
        supplier_b = make_supplier(SUPPLIER_B_AGENT_ID, "org_supplier_b", buyer_intent.quantity)
        offer_b = supplier_b.initial_offer()
        if use_ai:
            ai_result = await gemini_client.supplier_rationale(
                supplier_name=name_of(SUPPLIER_B_AGENT_ID),
                constraints=buyer.constraints,
                price_cents=offer_b.price_cents,
                delivery_days=offer_b.delivery_days,
                certification=offer_b.certification,
                base_rationale=offer_b.rationale,
            )
            offer_b.rationale = str(ai_result.value.rationale)
            ai_modes.append(ai_result.mode)
            await self._emit_ai(
                txn_id,
                SUPPLIER_B_AGENT_ID,
                ai_result.mode,
                offer_b.rationale,
                ai_result.fallback_reason,
            )
        await self._record_offer(
            txn_id, SUPPLIER_B_AGENT_ID, "org_supplier_b", offer_b, "gemini" if use_ai else "demo"
        )
        decision_b = await supplier_b.submit_offer(
            await self._session(),
            txn_id,
            offer_b,
            buyer_agent_id=BUYER_AGENT_ID,
            buyer_org_id="org_buyer",
            constraints=buyer.constraints,
        )
        step(
            "offer",
            "AGENT",
            "Supplier B opening offer: $2,050 / 6 days",
            offer_b.rationale,
            outcome="INFO",
            agent_id=SUPPLIER_B_AGENT_ID,
            offer=offer_b.as_dict(),
        )
        step(
            "policy",
            "TRUSTMESH",
            "Opening offer exceeds authorized budget",
            decision_b.reason,
            outcome="BLOCK",
            event_type=EventType.POLICY_BLOCKED.value,
            agent_id=SUPPLIER_B_AGENT_ID,
            rule_id=decision_b.rule_id,
        )
        await self._sleep(delay)

        engine = NegotiationEngine(buyer.constraints, supplier_b)
        await dispatcher.publish(
            EventType.NEGOTIATION_STARTED,
            transaction_id=txn_id,
            agent_id=BUYER_AGENT_ID,
            payload={
                "supplier_agent_id": SUPPLIER_B_AGENT_ID,
                "feasible_band_cents": list(engine.band()),
                "buyer_ceiling_cents": buyer_intent.max_budget_cents,
                "supplier_floor_cents": supplier_b.floor_cents,
                "note": "Negotiation is clamped to the feasible band by construction.",
            },
        )
        outcome = engine.run(max_rounds=3)
        await self._set_round(txn_id, outcome.round_number)

        allowed_rounds = 0
        for record in outcome.transcript:
            if record["round"] == 0:
                continue
            role = record["role"]
            offer_data = record["offer"]
            terms = OfferTerms(
                price_cents=int(offer_data["price_cents"]),
                delivery_days=int(offer_data["delivery_days"]),
                certification=str(offer_data["certification"]),
                quantity=int(offer_data["quantity"]),
                round_number=int(offer_data["round_number"]),
                offer_type=str(offer_data["offer_type"]),
                rationale=str(offer_data["rationale"]),
            )
            if role == "buyer":
                decision = await buyer.propose_terms(
                    await self._session(),
                    txn_id,
                    price_cents=terms.price_cents,
                    delivery_days=terms.delivery_days,
                    certification=terms.certification,
                    quantity=terms.quantity,
                    supplier_agent_id=SUPPLIER_B_AGENT_ID,
                )
                await self._record_offer(
                    txn_id,
                    BUYER_AGENT_ID,
                    "org_buyer",
                    terms,
                    "deterministic-solver",
                )
                actor_label = "Buyer"
            else:
                decision = await supplier_b.submit_offer(
                    await self._session(),
                    txn_id,
                    terms,
                    buyer_agent_id=BUYER_AGENT_ID,
                    buyer_org_id="org_buyer",
                    constraints=buyer.constraints,
                )
                await self._record_offer(
                    txn_id,
                    SUPPLIER_B_AGENT_ID,
                    "org_supplier_b",
                    terms,
                    "deterministic-solver",
                )
                actor_label = "Supplier B"

            await dispatcher.publish(
                EventType.OFFER_UPDATED,
                transaction_id=txn_id,
                agent_id=BUYER_AGENT_ID if role == "buyer" else SUPPLIER_B_AGENT_ID,
                payload={
                    "round": record["round"],
                    "role": role,
                    "offer": terms.as_dict(),
                    "decision": decision.decision,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "feasible_band_cents": list(engine.band()),
                },
            )
            if decision.allowed:
                allowed_rounds += 1
            await self._sleep(delay * 0.6)

        step(
            "negotiation",
            "AGENT",
            f"Negotiation completed in {outcome.round_number} round(s)",
            outcome.termination_reason,
            outcome="ALLOW" if outcome.agreed else "BLOCK",
            event_type=EventType.NEGOTIATION_STARTED.value,
            agent_id=SUPPLIER_B_AGENT_ID,
            negotiation=outcome.as_dict(),
            allowed_rounds=allowed_rounds,
        )
        await self._sleep(delay)

        # ---- 6. Supplier C: blocked security scenarios --------------------
        await self._run_malicious_scenarios(txn_id, buyer_intent)
        await self._bump_blocks(txn_id, 2)
        step(
            "security",
            "TRUSTMESH",
            "Supplier C blocked on both scenarios",
            (
                "SCENARIO 1: a request for the buyer's API credentials was blocked on "
                "capability + threat interception. SCENARIO 2: a $2,500 proposal was blocked "
                "by the deterministic budget rule. The legitimate transaction continued."
            ),
            outcome="BLOCK",
            event_type=EventType.SECURITY_ALERT.value,
            agent_id=SUPPLIER_C_AGENT_ID,
            scenarios=["unauthorized_capability", "policy_violation"],
        )
        await self._sleep(delay)

        # ---- 7. agreement + hash ------------------------------------------
        final = outcome.final_terms
        if final is None or not outcome.agreed:
            await self._set_state(
                txn_id, TransactionState.BLOCKED, "no compliant agreement could be reached"
            )
            errors.append("negotiation did not converge on compliant terms")
            return DemoRunResult(
                transaction_id=txn_id,
                state=TransactionState.BLOCKED.value,
                steps=steps,
                blocked_actions=3,
                security_alerts=3,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        await self._set_state(txn_id, TransactionState.AGREED, "final terms locked")

        def _accept(session: Session) -> dict[str, Any]:
            txn = session.get(Transaction, txn_id)
            if txn is None:
                return {}
            txn.supplier_agent_id = SUPPLIER_B_AGENT_ID
            txn.supplier_org_id = "org_supplier_b"
            session.commit()
            return {
                "budget_cents": txn.budget_cents,
                "max_delivery_days": txn.max_delivery_days,
                "quantity": txn.quantity,
                "product": txn.product,
            }

        txn_facts = await run_db(_accept)
        if not txn_facts:
            errors.append("transaction disappeared before agreement creation")
            return DemoRunResult(
                transaction_id=txn_id,
                state=TransactionState.FAILED.value,
                steps=steps,
                blocked_actions=3,
                security_alerts=3,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        accept_decision = await buyer.accept_agreement(
            await self._session(),
            txn_id,
            ProposedTerms(
                price_cents=final.price_cents,
                delivery_days=final.delivery_days,
                certification=final.certification,
                quantity=buyer_intent.quantity,
            ),
        )
        if accept_decision.blocked:
            errors.append(f"buyer acceptance was blocked: {accept_decision.reason}")
            await self._set_state(txn_id, TransactionState.BLOCKED, "acceptance blocked")
            return DemoRunResult(
                transaction_id=txn_id,
                state=TransactionState.BLOCKED.value,
                steps=steps,
                blocked_actions=4,
                security_alerts=4,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        await self._record_offer(
            txn_id,
            SUPPLIER_B_AGENT_ID,
            "org_supplier_b",
            final,
            "deterministic-solver",
        )
        step(
            "agreement",
            "AGENT",
            f"Final agreement: ${final.price_cents / 100:,.2f} / {final.delivery_days} days",
            final.rationale,
            outcome="ALLOW",
            event_type=EventType.AGENT_ACTION_EXECUTED.value,
            agent_id=SUPPLIER_B_AGENT_ID,
            terms=final.as_dict(),
        )

        artifact = await run_db(
            lambda session: build_artifact(session, txn_id, final)  # type: ignore[arg-type]
        )
        if artifact is None:
            errors.append("agreement construction failed")
            return DemoRunResult(
                transaction_id=txn_id,
                state=TransactionState.FAILED.value,
                steps=steps,
                blocked_actions=3,
                security_alerts=3,
                agreement_hash=None,
                commitment_status=None,
                ai_modes=ai_modes,
                errors=errors,
            )

        await dispatcher.publish(
            EventType.AGREEMENT_CREATED,
            transaction_id=txn_id,
            agent_id=SUPPLIER_B_AGENT_ID,
            payload={
                "schema_version": artifact.agreement.schema_version,
                "terms": dataclasses.asdict(artifact.agreement.terms),
                "policy": artifact.agreement.policy,
                "negotiated_at": artifact.agreement.negotiated_at,
                "canonical_json": artifact.canonical_json,
            },
        )
        step(
            "agreement",
            "BLOCKCHAIN",
            "Agreement canonicalised (RFC 8785)",
            (
                "The agreement was serialised to RFC 8785 canonical JSON: members sorted by "
                "UTF-16 code units, minimal escaping, ECMAScript number formatting, no "
                "whitespace. The canonical bytes are stored off-chain for reproducibility."
            ),
            outcome="SUCCESS",
            event_type=EventType.AGREEMENT_CREATED.value,
            canonicalization="RFC8785",
            canonical_json=artifact.canonical_json,
        )

        await dispatcher.publish(
            EventType.HASH_GENERATED,
            transaction_id=txn_id,
            agent_id=SUPPLIER_B_AGENT_ID,
            payload={
                "agreement_hash": artifact.agreement_hash,
                "algorithm": artifact.algorithm,
                "canonicalization": artifact.canonicalization,
            },
        )
        step(
            "hash",
            "BLOCKCHAIN",
            "SHA-256 agreement hash generated",
            artifact.agreement_hash,
            outcome="SUCCESS",
            event_type=EventType.HASH_GENERATED.value,
            agreement_hash=artifact.agreement_hash,
            algorithm="SHA-256",
        )
        await self._set_state(txn_id, TransactionState.HASHED, "agreement hashed")

        # ---- 8. blockchain commitment --------------------------------------
        commitment_status = None
        if commit_on_chain:
            commitment_status = await self._commit(txn_id, artifact)
            step(
                "chain",
                "BLOCKCHAIN",
                f"Commitment {str(commitment_status).lower()}",
                (
                    f"Network {settings.blockchain_network} · mode "
                    f"{blockchain_service.mode}. "
                    + (
                        "The commitment is confirmed on chain and can be verified by anyone."
                        if commitment_status == "CONFIRMED"
                        else (
                            "This is a SIMULATED receipt because no EVM RPC is configured. "
                            "The hash is still independently verifiable from the stored "
                            "canonical JSON."
                            if commitment_status == "SIMULATED"
                            else "The commitment is pending or failed. Core TrustMesh "
                            "enforcement is unaffected."
                        )
                    )
                ),
                outcome=(
                    "SUCCESS"
                    if commitment_status in {"CONFIRMED", "SIMULATED"}
                    else "WARNING"
                ),
                event_type=EventType.BLOCKCHAIN_CONFIRMED.value,
                commitment_status=commitment_status,
            )
        await self._set_state(txn_id, TransactionState.COMPLETED, "demonstration complete")

        stats = await self._stats(txn_id)
        return DemoRunResult(
            transaction_id=txn_id,
            state=TransactionState.COMPLETED.value,
            steps=steps,
            blocked_actions=stats["blocked"],
            security_alerts=stats["alerts"],
            agreement_hash=artifact.agreement_hash,
            commitment_status=commitment_status,
            ai_modes=ai_modes,
            errors=errors,
        )

    # ------------------------------------------------------------- internals
    async def _session(self):
        """A short-lived session bound to a worker thread for gateway calls."""
        return SessionProxy()

    async def _create_transaction(self, intent: dict[str, Any], *, use_ai: bool) -> str:
        def _apply(session: Session) -> str:
            txn = Transaction(
                intent_ref=f"INTENT-PENDING",
                buyer_org_id=intent["buyer_org_id"],
                buyer_agent_id=intent["buyer_agent_id"],
                product=intent["product"],
                quantity=intent["quantity"],
                budget_cents=intent["max_budget_cents"],
                max_delivery_days=intent["max_delivery_days"],
                certification_required=intent.get("certification_required", "VALID"),
                notes=intent.get("notes", ""),
                state=TransactionState.CREATED.value,
                ai_mode="gemini" if use_ai else "demo",
            )
            session.add(txn)
            session.flush()
            txn.intent_ref = f"INTENT-{txn.id}"
            session.commit()
            return txn.id

        return await run_db(_apply)

    async def _verify_counterparty(self, txn_id: str, agent_id: str) -> bool:
        """Identity verification expressed as a real Gateway capability request."""
        from app.trust.trust_engine import ActionRequest, trust_engine

        def _touch(session: Session) -> None:
            from app.trust import identity as identity_mod

            identity_mod.touch(session, agent_id, "presenting credential for verification")
            session.commit()

        await run_db(_touch)
        decision = await trust_engine.request_action(
            await self._session(),
            ActionRequest(
                agent_id=agent_id,
                capability="search",
                action="present_identity_for_verification",
                presented_secret=credential_for(agent_id),
                claimed_org_id=AGENTS_BY_ID[agent_id].org_id,
                transaction_id=txn_id,
            ),
        )
        if decision.allowed:
            await dispatcher.publish(
                EventType.AGENT_ACTION_EXECUTED,
                transaction_id=txn_id,
                agent_id=agent_id,
                payload={
                    "action": "present_identity_for_verification",
                    "capability": "search",
                    "decision": decision.decision,
                    "rule_id": decision.rule_id,
                    "result": identity_factors(decision),
                    "why": decision.reason,
                },
            )
        return decision.allowed

    async def _run_impersonation(self, txn_id: str) -> bool:
        config = behaviour(IMPOSTOR_AGENT_ID)
        impersonator = ImpersonatorAgent()
        attempt = impersonator.build_attempt(config)
        decision = await impersonator.attempt(await self._session(), attempt, txn_id)
        if decision.blocked:
            await self._bump_blocks(txn_id)
        return decision.blocked

    async def _run_malicious_scenarios(
        self, txn_id: str, buyer_intent: BuyerIntent
    ) -> list[dict[str, Any]]:
        """Run spec §5 SCENARIO 1 and SCENARIO 2 through the Gateway."""
        from app.trust.trust_engine import ActionRequest, trust_engine

        results: list[dict[str, Any]] = []
        config = behaviour(SUPPLIER_C_AGENT_ID)
        attacks = list(config.get("scripted_attacks", []))

        for attack in attacks:
            capability = str(attack["capability"])
            parameters = dict(attack.get("parameters", {}))
            action = str(attack["action"])

            if capability == "exceed_authorized_budget":
                # Route the malicious price claim through the deterministic budget
                # rule by submitting it as a proposal from the compromised agent.
                malicious = scripted_over_budget_offer(
                    buyer_intent.constraints(),
                    int(parameters.get("price_cents", buyer_intent.max_budget_cents + 50_000)),
                )
                decision = await trust_engine.request_action(
                    await self._session(),
                    ActionRequest(
                        agent_id=SUPPLIER_C_AGENT_ID,
                        capability="propose",
                        action=action,
                        presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                        claimed_org_id="org_supplier_c",
                        transaction_id=txn_id,
                        parameters={
                            "price_cents": malicious.price_cents,
                            "delivery_days": malicious.delivery_days,
                            "certification": malicious.certification,
                            "quantity": malicious.quantity,
                            "message": parameters.get("message", ""),
                        },
                    ),
                    constraints=buyer_intent.constraints(),
                )
            else:
                decision = await trust_engine.request_action(
                    await self._session(),
                    ActionRequest(
                        agent_id=SUPPLIER_C_AGENT_ID,
                        capability=capability,
                        action=action,
                        presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                        claimed_org_id="org_supplier_c",
                        transaction_id=txn_id,
                        parameters=parameters,
                    ),
                )
            results.append(decision.as_dict())
            await self._sleep(0.3)
        return results

    async def _emit_ai(
        self,
        txn_id: str,
        agent_id: str,
        mode: str,
        rationale: str,
        fallback_reason: str | None,
    ) -> None:
        await dispatcher.publish(
            EventType.AI_REASONING,
            transaction_id=txn_id,
            agent_id=agent_id,
            payload={
                "mode": mode,
                "model": settings.gemini_model if mode == "gemini" else "deterministic",
                "rationale": rationale,
                "fallback_reason": fallback_reason,
                "authority": "non-authoritative: supplies language only, never a verdict",
            },
        )

    async def _commit(self, txn_id: str, artifact: Any) -> str | None:
        from app.core.models import AgreementCommitment

        def _persist(session: Session) -> str | None:
            txn = session.get(Transaction, txn_id)
            if txn is None:
                return None
            existing = session.execute(
                select(AgreementCommitment).where(
                    AgreementCommitment.transaction_id == txn_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing.id
            row = persist_commitment(
                session,
                txn,
                artifact,
                network=settings.blockchain_network,
                chain_id=settings.effective_chain_id,
                contract_address=settings.blockchain_contract_address,
            )
            session.commit()
            return row.id

        commitment_id = await run_db(_persist)
        if commitment_id is None:
            return None

        # Re-fetch inside the session the blockchain service will use, so its updates
        # are written by the session that owns the row.
        chain_session = await self._session()
        commitment = chain_session.get(AgreementCommitment, commitment_id)
        if commitment is None:
            chain_session.close()
            return None

        result = await blockchain_service.commit_agreement(
            chain_session,
            commitment,
            agreement_hash=artifact.agreement_hash,
            transaction_id=txn_id,
            buyer_agent_id=artifact.agreement.buyer_agent_id,
            supplier_agent_id=artifact.agreement.supplier_agent_id,
        )
        chain_session.close()
        return result.status

    async def _stats(self, txn_id: str) -> dict[str, int]:
        from sqlalchemy import func

        from app.core.models import SecurityAlert

        def _query(session: Session) -> dict[str, int]:
            blocked = (
                session.execute(
                    select(func.count(SecurityAlert.id)).where(
                        SecurityAlert.transaction_id == txn_id
                    )
                ).scalar()
                or 0
            )
            return {"blocked": int(blocked), "alerts": int(blocked)}

        return await run_db(_query)


class SessionProxy:
    """A synchronous Session opened on the current worker thread.

    ``TrustEngine`` and the agent runtimes are written against a synchronous
    ``Session`` (they are pure enforcement logic, not I/O loops). The orchestrator
    calls them from the event loop, so each call gets a proxy that lazily opens a
    session bound to the thread that actually uses it and always closes it.
    """

    __slots__ = ("_session",)

    def __init__(self) -> None:
        self._session = None

    def _ensure(self):
        if self._session is None:
            from app.core.database import SessionLocal

            self._session = SessionLocal()
        return self._session

    def __getattr__(self, item: str):
        return getattr(self._ensure(), item)

    def __enter__(self):
        return self._ensure()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


def _load_intent(session: Session, txn_id: str) -> dict[str, Any] | None:
    """Read a stored intent back so a resumed run reflects what was actually submitted."""
    txn = session.get(Transaction, txn_id)
    if txn is None:
        return None
    return {
        "product": txn.product,
        "quantity": txn.quantity,
        "max_budget_cents": txn.budget_cents,
        "max_delivery_days": txn.max_delivery_days,
        "certification_required": txn.certification_required,
        "buyer_org_id": txn.buyer_org_id,
        "buyer_agent_id": txn.buyer_agent_id,
        "notes": txn.notes,
    }


def build_artifact(session: Session, txn_id: str, final: OfferTerms):
    """Build + canonicalise the final agreement inside a session."""
    txn = session.get(Transaction, txn_id)
    if txn is None:
        return None
    agreement = build_agreement(
        txn,
        supplier_agent_id=SUPPLIER_B_AGENT_ID,
        supplier_org_id="org_supplier_b",
        unit_price_cents=final.price_cents,
        delivery_days=final.delivery_days,
        certification=final.certification,
    )
    session.commit()
    return canonicalize_agreement(agreement)


def identity_factors(decision: Any) -> dict[str, Any]:
    if decision.identity is None:
        return {}
    return dict(decision.identity.factors)


orchestrator = DemoOrchestrator()

__all__ = ["DemoOrchestrator", "DemoRunResult", "StepRecord", "orchestrator"]
