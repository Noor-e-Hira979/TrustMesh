"""Transaction routes: create, run the demo, inspect, verify.

Route map
---------
``POST   /transactions``                  create from an intent, return a real id
``POST   /transactions/demo``             run the whole deterministic demonstration
``GET    /transactions``                  paginated list, filterable by state
``GET    /transactions/{id}``             full lifecycle detail
``GET    /transactions/{id}/offers``      offer + counter-offer history
``GET    /transactions/{id}/decisions``   every deterministic decision, with evidence
``GET    /transactions/{id}/alerts``      security events raised for this transaction
``GET    /transactions/{id}/timeline``    three-layer annotated lifecycle
``POST   /transactions/{id}/verify``      recompute the hash, optionally read the chain
``GET    /transactions/hashing/describe`` how the canonical hash is produced

Design notes
------------
* **The write path returns a real id.** ``POST /transactions`` creates the row
  synchronously and then runs the orchestrator against that id in the background, so a
  client can follow the transaction from the first response instead of polling for
  "the newest one".
* **Long work never blocks the request.** Both POST routes return immediately and let
  the caller watch ``/events/stream``. The background task is tracked and logged, and
  the orchestrator itself never raises.
* **Collections are paginated** with a uniform :class:`Page` envelope.

Referenced by: ``app.main``, the frontend Transactions, Overview and Blockchain pages.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, TypeVar

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import Select, desc, func, select
from sqlalchemy.orm import Session

from app.agreements.agreement_engine import verify_artifact
from app.agreements.hashing import describe as describe_hashing
from app.blockchain.contract import transaction_explorer_url
from app.blockchain.service import blockchain_service
from app.core.database import run_db
from app.core.models import (
    AgreementCommitment,
    Offer,
    PolicyDecision,
    SecurityAlert,
    Transaction,
    TransactionState,
)
from app.core.schemas import (
    AgreementVerifyOut,
    CommitmentOut,
    DemoRunAccepted,
    DemoRunRequest,
    HashingInfoOut,
    OfferOut,
    Page,
    PolicyDecisionOut,
    ProcurementIntentIn,
    SecurityAlertOut,
    TimelineStep,
    TransactionDetailOut,
)
from app.orchestrator.demo_orchestrator import orchestrator

logger = logging.getLogger("trustmesh.transactions")

router = APIRouter(tags=["transactions"])

T = TypeVar("T")

#: Ordered lifecycle stages used to build the timeline (spec §19). The order is the
#: narrative order, not alphabetical, so the UI can render the story as a sequence.
TIMELINE_STAGES: tuple[tuple[str, str, str, str], ...] = (
    ("intent", "AGENT", "Intent", "Buyer intent received"),
    ("discovery", "TRUSTMESH", "Discovery", "Supplier agents discovered"),
    ("identity", "TRUSTMESH", "Identity verification", "Counterparty identities verified"),
    ("offer", "AGENT", "Offers", "Offers received"),
    ("negotiation", "AGENT", "Negotiation", "Constraint-aware negotiation"),
    ("policy", "TRUSTMESH", "Policy checks", "Deterministic policy decisions"),
    ("security", "TRUSTMESH", "Security events", "Threat interception and blocking"),
    ("agreement", "AGENT", "Agreement", "Final agreement created"),
    ("hash", "BLOCKCHAIN", "Hash", "RFC 8785 + SHA-256"),
    ("chain", "BLOCKCHAIN", "Commitment", "Blockchain commitment"),
)

#: Terminal states. A transaction in one of these is finished for good.
TERMINAL_STATES: frozenset[str] = frozenset(
    {
        TransactionState.COMPLETED.value,
        TransactionState.BLOCKED.value,
        TransactionState.FAILED.value,
    }
)


# ------------------------------------------------------------------- utilities
def _detail(txn: Transaction) -> TransactionDetailOut:
    """Project a transaction plus its eager relationships into the API contract."""
    return TransactionDetailOut(
        id=txn.id,
        intent_ref=txn.intent_ref,
        buyer_org_id=txn.buyer_org_id,
        buyer_agent_id=txn.buyer_agent_id,
        supplier_org_id=txn.supplier_org_id,
        supplier_agent_id=txn.supplier_agent_id,
        product=txn.product,
        quantity=txn.quantity,
        budget_cents=txn.budget_cents,
        max_delivery_days=txn.max_delivery_days,
        certification_required=txn.certification_required,
        notes=txn.notes,
        state=txn.state,
        ai_mode=txn.ai_mode,
        negotiation_round=txn.negotiation_round,
        block_count=txn.block_count,
        created_at=txn.created_at,
        updated_at=txn.updated_at,
        offers=[
            OfferOut.model_validate(o)
            for o in sorted(txn.offers, key=lambda r: (r.round_number, r.created_at))
        ],
        decisions=[
            PolicyDecisionOut.model_validate(d)
            for d in sorted(txn.decisions, key=lambda r: r.created_at)
        ],
        alerts=[
            SecurityAlertOut.model_validate(a)
            for a in sorted(txn.alerts, key=lambda r: r.created_at)
        ],
        commitment=CommitmentOut.from_row(txn.commitment) if txn.commitment else None,
    )


def _paginate(stmt: Select, session: Session, limit: int, offset: int) -> tuple[list[Any], int]:
    """Return one page of rows plus the unpaginated total for the same filter."""
    total = int(session.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar() or 0)
    rows = list(session.execute(stmt.limit(limit).offset(offset)).scalars().all())
    return rows, total


def _page(rows: list[T], total: int, limit: int, offset: int) -> Page:
    return Page.build(rows, total=total, limit=limit, offset=offset)


async def _spawn(coro: Coroutine[Any, Any, None], *, label: str) -> None:
    """Run ``coro`` in the background, logging any failure instead of losing it.

    An un-awaited background task that raises is invisible; wrapping it here means a
    broken demo run is always recorded in the log.
    """

    async def _runner() -> None:
        try:
            await coro
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:
            logger.exception("%s failed", label)

    asyncio.create_task(_runner())


def _require_transaction(session: Session, transaction_id: str) -> Transaction:
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found.",
        )
    return txn


# ----------------------------------------------------------------------- write
@router.post(
    "/transactions",
    response_model=DemoRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a procurement transaction from an intent",
)
async def create_transaction(payload: ProcurementIntentIn) -> DemoRunAccepted:
    """Validate the intent, create the transaction, then run the deterministic flow.

    The intent is fully validated by Pydantic before any row is written, and the buyer
    agent's own authority is verified by the Gateway inside the run - this endpoint only
    accepts the request, it does not approve anything.
    """

    def _create(session: Session) -> str:
        txn = Transaction(
            intent_ref="INTENT-PENDING",
            buyer_org_id=payload.buyer_org_id,
            buyer_agent_id=payload.buyer_agent_id,
            product=payload.product,
            quantity=payload.quantity,
            budget_cents=payload.max_budget_cents,
            max_delivery_days=payload.max_delivery_days,
            certification_required=payload.certification_required,
            notes=payload.notes,
            state=TransactionState.CREATED.value,
            ai_mode="gemini" if settings_gemini() else "demo",
        )
        session.add(txn)
        session.flush()
        txn.intent_ref = f"INTENT-{txn.id}"
        session.commit()
        return txn.id

    txn_id = await run_db(_create)

    await _spawn(
        orchestrator.run_demo(transaction_id=txn_id, step_delay=0.4, use_ai=True),
        label=f"orchestrator run for {txn_id}",
    )

    return DemoRunAccepted(
        transaction_id=txn_id,
        status="accepted",
        detail=(
            "Intent validated and transaction created. The deterministic orchestrator is "
            "running; follow /api/events/stream or poll "
            f"/api/transactions/{txn_id} for the live lifecycle."
        ),
    )


def settings_gemini() -> bool:
    """Whether the AI layer is configured, used only to label ``ai_mode``."""
    from app.core.config import settings

    return settings.gemini_configured


@router.post(
    "/transactions/demo",
    response_model=DemoRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the full deterministic demonstration (spec §10 Demo Mode)",
)
async def run_demo(payload: DemoRunRequest | None = None) -> DemoRunAccepted:
    """The one-call hackathon button.

    Runs the whole §23 sequence: intent, discovery, identity verification, Supplier A
    rejected on delivery, Supplier B negotiation, Supplier C blocked twice, agreement,
    RFC 8785 canonicalisation, SHA-256 and the blockchain commitment.
    """
    options = payload or DemoRunRequest()

    if options.fresh:
        # Start from a clean slate so a re-run's counters and timeline are unambiguous.
        await run_db(_clear_terminal_history)

    await _spawn(
        orchestrator.run_demo(
            step_delay=options.step_delay_seconds,
            use_ai=options.use_ai,
            commit_on_chain=options.commit_on_chain,
        ),
        label="demo run",
    )

    return DemoRunAccepted(
        transaction_id="pending",
        status="started",
        detail=(
            "Demo Mode started: buyer intent -> discovery -> identity verification -> "
            "Supplier A rejected -> Supplier B negotiation -> Supplier C blocked -> "
            "agreement -> RFC 8785 -> SHA-256 -> blockchain commitment. The new "
            "transaction id appears as the first frame on /api/events/stream."
        ),
    )


def _clear_terminal_history(session: Session) -> None:
    """Remove finished transactions so a fresh demo run is unambiguous.

    Only terminal states are removed; anything mid-flight is left alone so a ``fresh``
    run can never destroy a transaction that is still negotiating.
    """
    removed = (
        session.execute(
            select(Transaction).where(Transaction.state.in_(sorted(TERMINAL_STATES)))
        )
        .scalars()
        .all()
    )
    for txn in removed:
        session.delete(txn)
    session.commit()
    if removed:
        logger.info("cleared %d terminal transaction(s) for a fresh demo run", len(removed))


# ------------------------------------------------------------------------ read
@router.get("/transactions", response_model=Page, summary="List transactions")
async def list_transactions(
    state: str | None = Query(default=None, description="Filter by lifecycle state"),
    buyer_org_id: str | None = Query(default=None),
    supplier_org_id: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page:
    stmt = select(Transaction).order_by(desc(Transaction.created_at))
    if state:
        stmt = stmt.where(Transaction.state == state)
    if buyer_org_id:
        stmt = stmt.where(Transaction.buyer_org_id == buyer_org_id)
    if supplier_org_id:
        stmt = stmt.where(Transaction.supplier_org_id == supplier_org_id)

    def _query(session: Session) -> Page:
        rows, total = _paginate(stmt, session, limit, offset)
        return _page([_detail(t) for t in rows], total, limit, offset)

    return await run_db(_query)


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionDetailOut,
    summary="Full transaction detail",
)
async def get_transaction(transaction_id: str) -> TransactionDetailOut:
    def _query(session: Session) -> TransactionDetailOut:
        return _detail(_require_transaction(session, transaction_id))

    return await run_db(_query)


@router.get(
    "/transactions/{transaction_id}/offers",
    response_model=Page,
    summary="Offer and counter-offer history",
)
async def get_offers(
    transaction_id: str,
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page:
    stmt = (
        select(Offer)
        .where(Offer.transaction_id == transaction_id)
        .order_by(Offer.round_number.asc(), Offer.created_at.asc())
    )
    if agent_id:
        stmt = stmt.where(Offer.agent_id == agent_id)

    def _query(session: Session) -> Page:
        _require_transaction(session, transaction_id)
        rows, total = _paginate(stmt, session, limit, offset)
        return _page([OfferOut.model_validate(o) for o in rows], total, limit, offset)

    return await run_db(_query)


@router.get(
    "/transactions/{transaction_id}/decisions",
    response_model=Page,
    summary="Every deterministic enforcement decision (spec §31 audit)",
)
async def get_decisions(
    transaction_id: str,
    decision: str | None = Query(default=None, pattern="^(ALLOW|BLOCK)$"),
    stage: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page:
    """Answers §31 directly: who, which capability, which rule, allowed or blocked, why."""
    stmt = (
        select(PolicyDecision)
        .where(PolicyDecision.transaction_id == transaction_id)
        .order_by(PolicyDecision.created_at.asc())
    )
    if decision:
        stmt = stmt.where(PolicyDecision.decision == decision.upper())
    if stage:
        stmt = stmt.where(PolicyDecision.stage == stage)

    def _query(session: Session) -> Page:
        _require_transaction(session, transaction_id)
        rows, total = _paginate(stmt, session, limit, offset)
        return _page([PolicyDecisionOut.model_validate(d) for d in rows], total, limit, offset)

    return await run_db(_query)


@router.get(
    "/transactions/{transaction_id}/alerts",
    response_model=Page,
    summary="Security events raised for this transaction",
)
async def get_transaction_alerts(
    transaction_id: str,
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page:
    stmt = (
        select(SecurityAlert)
        .where(SecurityAlert.transaction_id == transaction_id)
        .order_by(desc(SecurityAlert.created_at))
    )
    if severity:
        stmt = stmt.where(SecurityAlert.severity == severity.upper())

    def _query(session: Session) -> Page:
        _require_transaction(session, transaction_id)
        rows, total = _paginate(stmt, session, limit, offset)
        return _page([SecurityAlertOut.model_validate(a) for a in rows], total, limit, offset)

    return await run_db(_query)


@router.get(
    "/transactions/{transaction_id}/timeline",
    response_model=list[TimelineStep],
    summary="Three-layer lifecycle timeline (spec §17/§19)",
)
async def get_timeline(transaction_id: str) -> list[TimelineStep]:
    """Assemble the timeline from persisted offers, decisions, alerts and the commitment.

    Each step is attributed to AGENT ("what the AI wants to do"), TRUSTMESH ("whether it
    is authorized and policy-compliant") or BLOCKCHAIN ("what was actually recorded"), so
    the three-layer distinction from spec §17 is explicit in the data rather than only in
    the styling.
    """

    def _query(session: Session) -> list[TimelineStep]:
        txn = _require_transaction(session, transaction_id)
        offers = list(
            session.execute(
                select(Offer)
                .where(Offer.transaction_id == transaction_id)
                .order_by(Offer.round_number.asc(), Offer.created_at.asc())
            )
            .scalars()
            .all()
        )
        decisions = list(
            session.execute(
                select(PolicyDecision)
                .where(PolicyDecision.transaction_id == transaction_id)
                .order_by(PolicyDecision.created_at.asc())
            )
            .scalars()
            .all()
        )
        alerts = list(
            session.execute(
                select(SecurityAlert)
                .where(SecurityAlert.transaction_id == transaction_id)
                .order_by(SecurityAlert.created_at.asc())
            )
            .scalars()
            .all()
        )
        commitment = session.execute(
            select(AgreementCommitment).where(
                AgreementCommitment.transaction_id == transaction_id
            )
        ).scalar_one_or_none()
        return _build_timeline(txn, offers, decisions, alerts, commitment)

    return await run_db(_query)


def _build_timeline(
    txn: Transaction,
    offers: list[Offer],
    decisions: list[PolicyDecision],
    alerts: list[SecurityAlert],
    commitment: AgreementCommitment | None,
) -> list[TimelineStep]:
    """Fold every persisted record into one ordered, annotated narrative."""
    steps: list[TimelineStep] = []

    def add(
        layer: str,
        stage: str,
        title: str,
        detail: str,
        outcome: str,
        ts: Any,
        agent_id: str | None = None,
        event_type: str | None = None,
        **meta: Any,
    ) -> None:
        steps.append(
            TimelineStep(
                seq=0,
                layer=layer,
                stage=stage,
                title=title,
                detail=detail,
                outcome=outcome,
                agent_id=agent_id,
                event_type=event_type,
                ts=ts,
                meta=meta,
            )
        )

    add(
        "AGENT",
        "intent",
        "Intent",
        (
            f"{txn.quantity:,} {txn.product} · budget ≤ ${txn.budget_cents / 100:,.2f} · "
            f"delivery ≤ {txn.max_delivery_days} days · certification "
            f"{txn.certification_required}"
        ),
        "INFO",
        txn.created_at,
        agent_id=txn.buyer_agent_id,
        event_type="INTENT_RECEIVED",
        intent_ref=txn.intent_ref,
        budget_cents=txn.budget_cents,
        quantity=txn.quantity,
    )

    identity_ok = [d for d in decisions if d.rule_id == "IDENTITY_VERIFIED"]
    identity_bad = [d for d in decisions if d.stage == "identity" and d.decision == "BLOCK"]
    if identity_ok or identity_bad:
        last = (identity_ok or identity_bad)[-1]
        add(
            "TRUSTMESH",
            "identity",
            "Identity verification",
            (
                f"{len(identity_ok)} counterparty identit{'y' if len(identity_ok) == 1 else 'ies'} "
                f"verified, {len(identity_bad)} rejected. Claims are checked against the "
                "registry and its credential bindings, never self-asserted."
            ),
            "ALLOW" if identity_ok else "BLOCK",
            last.created_at,
            event_type="IDENTITY_VERIFIED" if identity_ok else "IDENTITY_REJECTED",
            verified=len(identity_ok),
            rejected=len(identity_bad),
        )

    blocked_agents: set[str] = set()
    for offer in offers:
        paired = next(
            (
                d
                for d in decisions
                if d.capability in {"negotiate", "propose"}
                and d.decision == "BLOCK"
                and d.agent_id == offer.agent_id
                and abs((d.created_at - offer.created_at).total_seconds()) < 30
            ),
            None,
        )
        blocked = paired is not None
        if blocked:
            blocked_agents.add(offer.agent_id)
        is_initial = offer.offer_type == "initial"
        add(
            "TRUSTMESH" if blocked else "AGENT",
            "offer" if is_initial else "negotiation",
            (
                f"{'Initial offer' if is_initial else 'Counter-offer'} from {offer.agent_id}"
            ),
            (
                f"${offer.price_cents / 100:,.2f} · {offer.delivery_days} days · "
                f"{offer.certification} certification"
                + (f" — {offer.rationale}" if offer.rationale else "")
            ),
            "BLOCK" if blocked else "INFO",
            offer.created_at,
            agent_id=offer.agent_id,
            event_type="OFFER_RECEIVED" if is_initial else "OFFER_UPDATED",
            offer_id=offer.id,
            price_cents=offer.price_cents,
            delivery_days=offer.delivery_days,
            quantity=offer.quantity,
            round=offer.round_number,
            policy_reason=paired.reason if paired else None,
            policy_rule_id=paired.rule_id if paired else None,
        )

    for decision in decisions:
        if decision.stage in {"identity", "schema"} or decision.decision != "BLOCK":
            continue
        if decision.agent_id in blocked_agents:
            continue  # already represented by the offer that triggered it
        add(
            "TRUSTMESH",
            "policy",
            f"Policy block · {decision.rule_id}",
            decision.reason,
            "BLOCK",
            decision.created_at,
            agent_id=decision.agent_id,
            event_type="POLICY_BLOCKED",
            enforcement_stage=decision.stage,
            rule_id=decision.rule_id,
            capability=decision.capability,
        )

    for alert in alerts:
        add(
            "TRUSTMESH",
            "security",
            f"Security event · {alert.alert_type}",
            alert.reason,
            "BLOCK",
            alert.created_at,
            agent_id=alert.agent_id,
            event_type="SECURITY_ALERT",
            severity=alert.severity,
            alert_id=alert.id,
            requested_capability=alert.requested_capability,
            requested_action=alert.requested_action,
            is_simulation=alert.is_simulation,
        )

    if commitment is not None:
        add(
            "BLOCKCHAIN",
            "hash",
            "SHA-256 agreement hash",
            (
                f"{commitment.agreement_hash} · {commitment.canonicalization} → "
                f"{commitment.algorithm}. The canonical JSON is stored off-chain so the "
                "digest can be reproduced byte-for-byte."
            ),
            "SUCCESS",
            commitment.created_at,
            event_type="HASH_GENERATED",
            algorithm=commitment.algorithm,
            canonicalization=commitment.canonicalization,
            canonical_json=commitment.canonical_json,
            agreement_hash=commitment.agreement_hash,
        )
        chain_outcome = {
            "CONFIRMED": "SUCCESS",
            "SIMULATED": "WARNING",
            "PENDING": "WARNING",
            "FAILED": "ERROR",
        }.get(commitment.status, "INFO")
        add(
            "BLOCKCHAIN",
            "chain",
            f"Commitment {commitment.status.lower()}",
            (
                f"network={commitment.network} · chain_id={commitment.chain_id} · "
                f"tx={commitment.tx_hash or 'n/a'} · confirmations={commitment.confirmations}"
                + (f" · {commitment.error_message}" if commitment.error_message else "")
            ),
            chain_outcome,
            commitment.confirmed_at or commitment.submitted_at or commitment.created_at,
            event_type=f"BLOCKCHAIN_{commitment.status}",
            status=commitment.status,
            tx_hash=commitment.tx_hash,
            block_number=commitment.block_number,
            explorer_url=transaction_explorer_url(commitment.network, commitment.tx_hash),
        )

    steps.sort(key=lambda s: (s.ts, s.stage))
    for index, step in enumerate(steps, start=1):
        step.seq = index
    return steps


@router.post(
    "/transactions/{transaction_id}/verify",
    response_model=AgreementVerifyOut,
    summary="Recompute the hash and, when possible, read it back from the chain",
)
async def verify_transaction(transaction_id: str) -> AgreementVerifyOut:
    """Independent verification.

    The digest is recomputed locally from the stored canonical bytes - the stored hash
    is never trusted. When the commitment reached a terminal state and a node is
    reachable, the digest is also read back from ``TrustMeshRegistry`` and compared.
    """

    def _fetch(session: Session) -> AgreementCommitment:
        _require_transaction(session, transaction_id)
        commitment = session.execute(
            select(AgreementCommitment).where(
                AgreementCommitment.transaction_id == transaction_id
            )
        ).scalar_one_or_none()
        if commitment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This transaction has no agreement commitment yet. Verification becomes "
                    "available once the agreement has been canonicalised and hashed."
                ),
            )
        return commitment

    commitment = await run_db(_fetch)
    matches, recomputed = verify_artifact(commitment.canonical_json, commitment.agreement_hash)

    notes = [
        "Recomputed locally from the stored RFC 8785 canonical JSON; the stored hash was "
        "not trusted.",
        f"algorithm={commitment.algorithm} · canonicalization={commitment.canonicalization}",
    ]

    on_chain_present: bool | None = None
    on_chain_matches: bool | None = None
    if commitment.status in {"CONFIRMED", "SIMULATED"}:
        readback = await blockchain_service.read_commitment(transaction_id)
        if readback.get("available") and readback.get("matches"):
            on_chain_present = bool(readback.get("present"))
            on_chain_matches = (
                str(readback["matches"]).lower() == commitment.agreement_hash.lower()
            )
            notes.append(f"Chain readback: {readback.get('detail')}")
        else:
            notes.append(
                "Chain readback unavailable: " + str(readback.get("detail") or "no node connection")
            )
    else:
        notes.append(f"Commitment status is {commitment.status}; no chain readback attempted.")

    return AgreementVerifyOut(
        transaction_id=transaction_id,
        agreement_hash=commitment.agreement_hash,
        recomputed_hash=recomputed,
        hash_matches=matches,
        canonicalization=commitment.canonicalization,
        algorithm=commitment.algorithm,
        canonical_json=commitment.canonical_json,
        on_chain_present=on_chain_present,
        on_chain_matches=on_chain_matches,
        chain_status=commitment.status,
        explorer_url=transaction_explorer_url(commitment.network, commitment.tx_hash),
        verified_at=datetime.now(timezone.utc),
        notes=notes,
    )


@router.get(
    "/transactions/hashing/describe",
    response_model=HashingInfoOut,
    summary="How the canonical agreement hash is produced",
)
async def hashing_description() -> dict:
    from app.agreements.hashing import rfc8785_test_vectors, selftest
    from app.blockchain.contract import NEVER_STORED_ON_CHAIN, ON_CHAIN_PAYLOAD

    # The on-chain payload list comes from ``app.blockchain.contract`` so this endpoint
    # and ``/api/blockchain/hashing`` can never describe the registry differently.
    return {
        **describe_hashing(),
        "selftest_passed": selftest(),
        "vectors": rfc8785_test_vectors(),
        "on_chain_payload": list(ON_CHAIN_PAYLOAD),
        "never_stored_on_chain": list(NEVER_STORED_ON_CHAIN),
    }
