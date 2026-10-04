"""Overview route: the single aggregate the dashboard needs on first paint.

Spec §18 requires the Overview page to show status cards (AI / Trust Engine / Policy
Engine / Blockchain), active agents, the current transaction, the current negotiation
state, recent security events, recent blockchain commitments, a live activity feed and
system health - and to let a judge understand the system within seconds.

Serving that as one endpoint instead of seven means the dashboard renders complete on
first paint instead of filling in progressively, and it removes a fan-out of round trips
on the demo's most important screen.

Referenced by: ``app.main``, the frontend Overview page.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai.gemini_client import gemini_client
from app.blockchain.service import blockchain_service
from app.core.database import run_db
from app.core.events import dispatcher
from app.core.models import (
    Agent,
    AgentStatus,
    AgreementCommitment,
    EventLog,
    SecurityAlert,
    Transaction,
    TransactionState,
    VerificationStatus,
)
from app.core.schemas import (
    AgentOut,
    CommitmentOut,
    ComponentHealth,
    EventOut,
    OverviewOut,
    OverviewStat,
    SecurityAlertOut,
    TransactionDetailOut,
)

logger = logging.getLogger("trustmesh.overview")

router = APIRouter(tags=["overview"])

#: States that mean a transaction is still moving. Used to pick "current transaction".
IN_FLIGHT_STATES: tuple[str, ...] = (
    TransactionState.CREATED.value,
    TransactionState.DISCOVERING.value,
    TransactionState.VERIFYING_IDENTITY.value,
    TransactionState.OFFERS_RECEIVED.value,
    TransactionState.EVALUATING.value,
    TransactionState.NEGOTIATING.value,
    TransactionState.AGREED.value,
    TransactionState.HASHED.value,
)

#: Human labels for the negotiation state shown on the dashboard.
NEGOTIATION_LABELS: dict[str, str] = {
    TransactionState.CREATED.value: "intent received",
    TransactionState.DISCOVERING.value: "discovering suppliers",
    TransactionState.VERIFYING_IDENTITY.value: "verifying identities",
    TransactionState.OFFERS_RECEIVED.value: "offers received",
    TransactionState.EVALUATING.value: "evaluating offers",
    TransactionState.NEGOTIATING.value: "negotiating",
    TransactionState.AGREED.value: "terms locked",
    TransactionState.HASHED.value: "agreement hashed",
    TransactionState.COMMITTED.value: "committed to chain",
    TransactionState.COMPLETED.value: "completed",
    TransactionState.BLOCKED.value: "blocked",
    TransactionState.FAILED.value: "failed",
}


@router.get(
    "/overview",
    response_model=OverviewOut,
    summary="Everything the Overview dashboard needs, in one call (spec §18)",
)
async def overview(
    alerts_limit: int = Query(default=8, ge=1, le=50),
    commitments_limit: int = Query(default=5, ge=1, le=25),
    events_limit: int = Query(default=25, ge=1, le=200),
) -> OverviewOut:
    from app.api.routes.health import _check_database, _component_matrix

    components: list[ComponentHealth] = await _component_matrix()
    ai_status = gemini_client.status()
    chain_status = blockchain_service.status()

    def _query(session: Session) -> dict:
        from app.api.routes.transactions import _detail

        agents = list(
            session.execute(select(Agent).order_by(Agent.agent_type, Agent.id)).scalars().all()
        )
        active = [a for a in agents if a.status == AgentStatus.ACTIVE.value]
        verified = [a for a in agents if a.verification_status == VerificationStatus.VERIFIED.value]
        blocked = [a for a in agents if a.status == AgentStatus.BLOCKED.value]

        # Prefer a transaction that is still moving; otherwise show the most recent.
        current = session.execute(
            select(Transaction).where(Transaction.state.in_(IN_FLIGHT_STATES))
            .order_by(desc(Transaction.updated_at))
            .limit(1)
        ).scalar_one_or_none()
        if current is None:
            current = session.execute(
                select(Transaction).order_by(desc(Transaction.created_at)).limit(1)
            ).scalar_one_or_none()

        # Project the detail INSIDE the session: it walks lazy relationships
        # (offers, decisions, alerts, commitment) that cannot load once detached.
        current_detail = _detail(current) if current is not None else None

        recent_alerts = list(
            session.execute(
                select(SecurityAlert).order_by(desc(SecurityAlert.created_at)).limit(alerts_limit)
            )
            .scalars()
            .all()
        )
        recent_commitments = list(
            session.execute(
                select(AgreementCommitment)
                .order_by(desc(AgreementCommitment.created_at))
                .limit(commitments_limit)
            )
            .scalars()
            .all()
        )
        recent_events = list(
            session.execute(
                select(EventLog).order_by(desc(EventLog.seq)).limit(events_limit)
            )
            .scalars()
            .all()
        )

        totals = {
            "transactions": int(
                session.execute(select(func.count(Transaction.id))).scalar() or 0
            ),
            "alerts": int(session.execute(select(func.count(SecurityAlert.id))).scalar() or 0),
            "blocked": int(
                session.execute(
                    select(func.count(SecurityAlert.id)).where(
                        SecurityAlert.decision == "BLOCK"
                    )
                ).scalar()
                or 0
            ),
            "commitments": int(
                session.execute(select(func.count(AgreementCommitment.id))).scalar() or 0
            ),
            "confirmed": int(
                session.execute(
                    select(func.count(AgreementCommitment.id)).where(
                        AgreementCommitment.status == "CONFIRMED"
                    )
                ).scalar()
                or 0
            ),
            "agents": len(agents),
        }
        return {
            "agents": active,
            "verified": len(verified),
            "blocked_agents": len(blocked),
            "current": current_detail,
            "alerts": recent_alerts,
            "commitments": recent_commitments,
            "events": recent_events,
            "totals": totals,
        }

    data = await run_db(_query)

    current_txn: TransactionDetailOut | None = data["current"]
    negotiation_state = "idle"
    negotiation_round = 0
    if current_txn is not None:
        negotiation_state = NEGOTIATION_LABELS.get(current_txn.state, current_txn.state.lower())
        negotiation_round = current_txn.negotiation_round

    totals = data["totals"]
    confirmed_ratio = (
        totals["confirmed"] / totals["commitments"] if totals["commitments"] else 0.0
    )

    stats = [
        OverviewStat(
            label="Active agents",
            value=str(len(data["agents"])),
            detail=f"{data['verified']} verified · {data['blocked_agents']} blocked",
            tone="good" if data["agents"] else "warn",
        ),
        OverviewStat(
            label="Transactions",
            value=str(totals["transactions"]),
            detail=f"current: {negotiation_state}",
            tone="neutral",
        ),
        OverviewStat(
            label="Actions blocked",
            value=str(totals["blocked"]),
            detail=f"{totals['alerts']} security events recorded",
            tone="bad" if totals["blocked"] else "good",
        ),
        OverviewStat(
            label="Agreements committed",
            value=str(totals["commitments"]),
            detail=(
                f"{totals['confirmed']} on-chain confirmed"
                if totals["commitments"]
                else "none committed yet"
            ),
            tone="good" if confirmed_ratio >= 1 and totals["commitments"] else "neutral",
        ),
        OverviewStat(
            label="Event stream",
            value=str(dispatcher.stats()["seq"]),
            detail=f"{dispatcher.subscriber_count} live subscriber(s)",
            tone="accent",
        ),
    ]

    return OverviewOut(
        components=components,
        stats=stats,
        active_agents=[AgentOut.model_validate(a) for a in data["agents"]],
        agent_count=len(data["agents"]),
        verified_agent_count=data["verified"],
        blocked_agent_count=data["blocked_agents"],
        current_transaction=current_txn,
        negotiation_state=negotiation_state,
        negotiation_round=negotiation_round,
        recent_alerts=[SecurityAlertOut.model_validate(a) for a in data["alerts"]],
        recent_commitments=[CommitmentOut.model_validate(c) for c in data["commitments"]],
        recent_events=[
            EventOut(
                seq=e.seq,
                type=e.type,
                ts=e.created_at,
                transaction_id=e.transaction_id,
                agent_id=e.agent_id,
                payload=e.payload or {},
            )
            for e in reversed(data["events"])
        ],
        blockchain_connection=chain_status["connection"],
        ai_mode=ai_status["last_mode"],
        event_seq=dispatcher.stats()["seq"],
        sse_subscribers=dispatcher.subscriber_count,
        demo_available=True,
        generated_at=datetime.now(timezone.utc),
    )
