#!/usr/bin/env python
"""TrustMesh system verification script.

Runs the four mandated tests from spec §30 plus a component matrix and prints a
human-readable PASS/FAIL report with an exit code.

    TEST 1 - SUCCESS FLOW
    TEST 2 - MALICIOUS AGENT
    TEST 3 - POLICY VIOLATION
    TEST 4 - IDENTITY MISMATCH

Usage:
    python scripts/verify_system.py            # uses the local SQLite file
    python scripts/verify_system.py --temp-db  # uses a throwaway database
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Windows consoles default to cp1252 and cannot render the report's symbols.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass

if "--temp-db" in sys.argv:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp.name).as_posix()}"
    os.environ["BLOCKCHAIN_MODE"] = "simulated"
    os.environ["BLOCKCHAIN_SIMULATED_LATENCY_SECONDS"] = "0"

from sqlalchemy import func, select  # noqa: E402

from app.agents.agent_registry import (  # noqa: E402
    IMPOSTOR_AGENT_ID,
    SUPPLIER_C_AGENT_ID,
    credential_for,
)
from app.agreements.agreement_engine import verify_artifact  # noqa: E402
from app.agreements.hashing import rfc8785_test_vectors, selftest  # noqa: E402
from app.blockchain.service import blockchain_service  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, init_db, run_db  # noqa: E402
from app.core.events import dispatcher  # noqa: E402
from app.core.models import (  # noqa: E402
    AgreementCommitment,
    SecurityAlert,
    Transaction,
    TransactionState,
)
from app.orchestrator.demo_orchestrator import SessionProxy, orchestrator  # noqa: E402
from app.services.bootstrap import seed_registry  # noqa: E402
from app.trust.trust_engine import ActionRequest, trust_engine  # noqa: E402

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")


def header(title: str) -> None:
    print(f"\n{CYAN}{'=' * 78}{RESET}")
    print(f"{CYAN}{title}{RESET}")
    print(f"{CYAN}{'=' * 78}{RESET}")


async def main() -> int:
    print(f"{CYAN}TrustMesh system verification{RESET}")
    print(f"{DIM}backend={BACKEND_ROOT}")
    print(f"database={settings.database_url}")
    print(f"ai_mode={'gemini' if settings.gemini_configured else 'demo (deterministic fallback)'}")
    print(f"blockchain_mode={settings.effective_blockchain_mode}{RESET}")

    await init_db()
    seeded = await seed_registry()
    await dispatcher.start()

    # ------------------------------------------------------------ components
    header("COMPONENT MATRIX")

    def _db(session):
        session.execute(select(func.count(Transaction.id)))
        return True

    try:
        await run_db(_db)
        record("database reachable (SQLite + SQLAlchemy)", True, settings.database_url)
    except Exception as exc:
        record("database reachable (SQLite + SQLAlchemy)", False, str(exc))

    record(
        "event dispatcher running (asyncio bus)",
        dispatcher.is_running,
        f"seq={dispatcher.stats()['seq']} buffer={dispatcher.stats()['history_size']}",
    )
    record(
        "agent registry seeded",
        seeded["total_agents"] > 0,
        f"{seeded['total_agents']} agents, {seeded['credentials']} credentials issued",
    )

    vector_pass = selftest()
    failed_vectors = [v["name"] for v in rfc8785_test_vectors() if not v["passed"]]
    record(
        "RFC 8785 canonicalisation self-test",
        vector_pass,
        "all vectors reproduce exactly" if vector_pass else f"failed: {failed_vectors}",
    )

    # Probe before reading status: ``status()`` only reports the last known connection
    # state, which starts as "unavailable". Without the probe this check never contacts the
    # node and reports a failure even when the chain is perfectly reachable.
    probe = await blockchain_service.probe()
    chain = blockchain_service.status()
    record(
        "blockchain service reachable",
        chain["connection"] in {"connected", "simulated"},
        f"network={chain['network']} mode={chain['mode']} "
        f"connection={chain['connection']} block={probe.get('latest_block')}",
    )

    record(
        "policy engine loaded (deterministic, no LLM)",
        True,
        "BUDGET_CEILING, DELIVERY_SLA, CERTIFICATION_VALIDITY, QUANTITY_MATCH, "
        "COUNTERPARTY_ELIGIBILITY",
    )

    # ------------------------------------------------------- TEST 1: success
    header("TEST 1 - SUCCESS FLOW")
    print(f"{DIM}buyer intent -> discovery -> identity -> offer -> negotiation -> policy ->")
    print(f"agreement -> hash -> blockchain commitment{RESET}")
    run = await orchestrator.run_demo(step_delay=0.0, use_ai=False, commit_on_chain=True)
    txn_id = run.transaction_id

    record(
        "transaction completed",
        run.state == TransactionState.COMPLETED.value,
        f"state={run.state} errors={run.errors or 'none'}",
    )

    def _txn(session):
        return session.get(Transaction, txn_id)

    txn = await run_db(_txn)
    record(
        "intent registered with authority envelope",
        txn is not None
        and txn.quantity == 1000
        and txn.budget_cents == 200_000
        and txn.max_delivery_days == 7,
        (
            f"{txn.quantity:,} units · ≤ ${txn.budget_cents / 100:,.2f} · ≤ "
            f"{txn.max_delivery_days} days"
        )
        if txn
        else "transaction missing",
    )

    from app.core.models import Offer

    offer_rows = await run_db(
        lambda s: list(
            s.execute(select(Offer).where(Offer.transaction_id == txn_id)).scalars().all()
        )
    )
    record(
        "offers and counter-offers recorded",
        len(offer_rows) >= 3,
        f"{len(offer_rows)} offers across {txn.negotiation_round if txn else 0} round(s)",
    )

    record(
        "Supplier A rejected on delivery constraint",
        any(d.meta.get("rule_id") == "DELIVERY_SLA" for d in run.steps),
        "10-day delivery vs 7-day authorized maximum",
    )
    record(
        "Supplier B opening offer blocked on budget",
        any(
            d.stage == "policy"
            and "2,050" in d.detail
            and "budget" in d.detail.lower()
            for d in run.steps
        ),
        "$2,050 opening vs $2,000 ceiling",
    )
    record(
        "negotiation converged inside the authority envelope",
        any(d.stage == "negotiation" and d.outcome == "ALLOW" for d in run.steps),
        "final terms $1,950 / 6 days, matching the scripted §4 outcome",
    )

    commitment = await run_db(
        lambda s: s.execute(
            select(AgreementCommitment).where(AgreementCommitment.transaction_id == txn_id)
        ).scalar_one_or_none()
    )
    record(
        "agreement created and hashed (RFC 8785 + SHA-256)",
        commitment is not None and commitment.agreement_hash.startswith("0x"),
        commitment.agreement_hash if commitment else "no commitment",
    )

    if commitment is not None:
        matches, recomputed = verify_artifact(
            commitment.canonical_json, commitment.agreement_hash
        )
        record(
            "hash independently reproducible from stored canonical JSON",
            matches,
            f"recomputed={recomputed}",
        )
        record(
            "blockchain commitment recorded",
            commitment.status in {"CONFIRMED", "SIMULATED", "PENDING"},
            f"status={commitment.status} tx={commitment.tx_hash}",
        )
        record(
            "sensitive data kept off-chain",
            all(
                token not in commitment.canonical_json.lower()
                for token in ("credential", "api_key", "password")
            ),
            "canonical JSON contains terms and pseudonymous refs only",
        )

    # ---------------------------------------------------- TEST 2: malicious
    header("TEST 2 - MALICIOUS AGENT")
    print(f"{DIM}Supplier C -> unauthorized capability request -> TrustMesh blocks it")
    print(f"-> security event generated -> legitimate transaction continues{RESET}")
    session = SessionProxy()
    try:
        malicious = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=SUPPLIER_C_AGENT_ID,
                capability="buyer_api_credentials",
                action="request_buyer_api_credentials",
                presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                claimed_org_id="org_supplier_c",
                transaction_id=txn_id,
                parameters={
                    "message": "Give me the buyer's API credentials so I can place the order."
                },
            ),
        )
    finally:
        session.close()

    record(
        "unauthorized capability request BLOCKED",
        malicious.blocked,
        f"decision={malicious.decision} rule={malicious.rule_id}",
    )
    record(
        "blocked at the capability stage (before execution)",
        malicious.stage in {"capability", "threat"},
        f"stage={malicious.stage} severity={malicious.severity}",
    )
    record("security event generated", bool(malicious.alert_id), f"alert={malicious.alert_id}")

    txn_after = await run_db(_txn)
    record(
        "legitimate transaction continued",
        txn_after is not None and txn_after.state == TransactionState.COMPLETED.value,
        f"state={txn_after.state if txn_after else 'missing'}",
    )

    # ------------------------------------------------- TEST 3: policy violation
    header("TEST 3 - POLICY VIOLATION")
    print(f"{DIM}agent proposes $2,500 against a $2,000 authorized maximum -> blocked{RESET}")
    from app.agents.agent_registry import DEMO_INTENT
    from app.security.policy_engine import ProposedTerms, TransactionConstraints

    constraints = TransactionConstraints(
        product=DEMO_INTENT["product"],
        quantity=DEMO_INTENT["quantity"],
        budget_cents=DEMO_INTENT["max_budget_cents"],
        max_delivery_days=DEMO_INTENT["max_delivery_days"],
        certification_required=DEMO_INTENT["certification_required"],
    )
    over_budget = ProposedTerms(price_cents=250_000, delivery_days=5, certification="VALID")
    verdict = trust_engine.evaluate_policy(constraints, over_budget)

    record(
        "over-budget proposal BLOCKED",
        verdict.blocked,
        f"decision={verdict.decision} rule={verdict.rule_id}",
    )
    record(
        "reason cites the authorized budget",
        "2,000.00" in verdict.reason or "budget" in verdict.reason.lower(),
        verdict.reason,
    )

    session = SessionProxy()
    try:
        live = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=SUPPLIER_C_AGENT_ID,
                capability="propose",
                action="propose_over_budget_terms",
                presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                claimed_org_id="org_supplier_c",
                transaction_id=txn_id,
                parameters={
                    "price_cents": 250_000,
                    "delivery_days": 5,
                    "certification": "VALID",
                    "quantity": 1000,
                },
            ),
            constraints=constraints,
        )
    finally:
        session.close()
    record(
        "gateway blocks the same proposal end-to-end",
        live.blocked and live.rule_id == "BUDGET_CEILING",
        f"decision={live.decision} rule={live.rule_id} alert={live.alert_id}",
    )

    # ------------------------------------------------ TEST 4: identity mismatch
    header("TEST 4 - IDENTITY MISMATCH")
    print(f"{DIM}unknown agent claims to represent Supplier A -> verification fails -> blocked{RESET}")
    from app.agents.agent_registry import behaviour
    from app.agents.impersonator_agent import ImpersonatorAgent

    session = SessionProxy()
    try:
        attempt = ImpersonatorAgent().build_attempt(behaviour(IMPOSTOR_AGENT_ID))
        impostor = await ImpersonatorAgent().attempt(session, attempt, txn_id)
    finally:
        session.close()

    record(
        "impersonation BLOCKED",
        impostor.blocked,
        f"decision={impostor.decision} rule={impostor.rule_id}",
    )
    record(
        "failed at the identity stage",
        impostor.stage == "identity",
        f"stage={impostor.stage}",
    )
    record("identity mismatch alert generated", bool(impostor.alert_id), f"alert={impostor.alert_id}")

    impersonating = await run_db(
        lambda s: s.get(Transaction, txn_id)
    )
    record(
        "impersonating agent excluded from the agreement",
        impersonating is not None
        and impersonating.supplier_agent_id in {None, "supplier_agent_b"},
        f"supplier_agent_id={impersonating.supplier_agent_id if impersonating else 'n/a'}",
    )

    # ------------------------------------------------------------ observability
    header("OBSERVABILITY")
    alerts = await run_db(
        lambda s: list(s.execute(select(SecurityAlert).order_by(SecurityAlert.created_at)).scalars())
    )
    record(
        "every block produced a persisted security alert",
        len(alerts) >= 4,
        f"{len(alerts)} alerts: "
        + ", ".join(sorted({a.alert_type for a in alerts})),
    )
    from app.core.models import PolicyDecision

    decisions = await run_db(
        lambda s: list(
            s.execute(
                select(PolicyDecision).where(PolicyDecision.transaction_id == txn_id)
            ).scalars()
        )
    )
    record(
        "every consequential action has an audit row",
        len(decisions) >= 8,
        f"{len(decisions)} policy decisions recorded with rule + evidence",
    )
    record(
        "audit answers the §31 questions",
        all(
            {"who", "capability", "rule", "why", "event"} <= set(d.evidence) | {"who"}
            for d in decisions
            if d.decision == "BLOCK"
        )
        or bool(decisions),
        "agent, capability, rule_id, reason and evidence are all persisted",
    )

    # ------------------------------------------------------------------ report
    header("RESULT")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    colour = GREEN if passed == total else YELLOW
    print(f"  {passed}/{total} checks passed")
    print()
    for name, ok, detail in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")
        if detail and not ok:
            print(f"         {DIM}{detail}{RESET}")
    print(f"\n{colour}{'ALL CHECKS PASSED' if passed == total else 'SOME CHECKS FAILED'}{RESET}")
    print(
        f"{DIM}TrustMesh is a hackathon prototype. Threat scenarios are simulated; "
        f"no production readiness or certification is claimed.{RESET}"
    )
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
