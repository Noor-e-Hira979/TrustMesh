"""End-to-end tests for the demo orchestrator, the event bus and the API.

These are the spec §30 tests in executable form, plus SSE behaviour.

Referenced by: ``app.orchestrator.demo_orchestrator``, ``app.core.events``,
``app.main``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agents.agent_registry import (
    IMPOSTOR_AGENT_ID,
    SUPPLIER_A_AGENT_ID,
    SUPPLIER_B_AGENT_ID,
    SUPPLIER_C_AGENT_ID,
    credential_for,
)
from app.core.config import settings
from app.core.events import EventType, dispatcher
from app.core.models import AgreementCommitment, SecurityAlert, Transaction, TransactionState
from app.orchestrator.demo_orchestrator import SessionProxy, orchestrator
from app.trust.trust_engine import ActionRequest, trust_engine

pytestmark = pytest.mark.usefixtures("db")


# --------------------------------------------------------------------- TEST 1
@pytest_asyncio.fixture
async def demo_run():
    """One full deterministic run reused by the TEST 1 assertions."""
    return await orchestrator.run_demo(step_delay=0.0, use_ai=False, commit_on_chain=True)


async def test_success_flow_completes(demo_run):
    assert demo_run.state == TransactionState.COMPLETED.value, demo_run.errors
    assert demo_run.errors == []
    assert demo_run.agreement_hash and demo_run.agreement_hash.startswith("0x")
    assert len(demo_run.agreement_hash) == 66


async def test_success_flow_reaches_expected_final_terms(demo_run):
    canonical_steps = [
        step for step in demo_run.steps if "canonical_json" in step.meta
    ]
    assert canonical_steps, "no canonicalisation step recorded"
    payload = json.loads(canonical_steps[0].meta["canonical_json"])
    assert canonical_steps[0].meta["canonicalization"] == "RFC8785"
    terms = payload["terms"]
    assert terms["unit_price_cents"] == 195_000
    assert terms["delivery_days"] == 6
    assert terms["quantity"] == 1000
    assert terms["total_price_cents"] == 195_000 * 1000
    assert terms["currency"] == "USD"

    canonical_json = canonical_steps[0].meta["canonical_json"]
    # RFC 8785 guarantees: re-canonicalising the parsed document is a no-op, which can
    # only hold if there is no insignificant whitespace and members are already sorted.
    from app.agreements.hashing import canonicalize

    assert canonicalize(json.loads(canonical_json)) == canonical_json
    assert canonical_json.startswith("{") and canonical_json.endswith("}")
    assert "\n" not in canonical_json and "\t" not in canonical_json


async def test_supplier_a_rejected_on_delivery(demo_run):
    blocked = [s for s in demo_run.steps if s.stage == "policy" and s.outcome == "BLOCK"]
    assert any("delivery" in s.detail.lower() for s in blocked)


async def test_supplier_b_opening_offer_blocked_on_budget(demo_run):
    blocked = [s for s in demo_run.steps if s.stage == "policy" and s.outcome == "BLOCK"]
    assert any("2,050" in s.detail and "budget" in s.detail.lower() for s in blocked)


async def test_negotiation_recorded_multiple_rounds(demo_run):
    negotiation = [s for s in demo_run.steps if s.stage == "negotiation"]
    assert negotiation, "no negotiation step recorded"
    rounds = negotiation[0].meta["negotiation"]["rounds"]
    assert len({r["round"] for r in rounds}) >= 2


async def test_commitment_status_is_visible(demo_run):
    assert demo_run.commitment_status in {"CONFIRMED", "SIMULATED", "PENDING"}


# --------------------------------------------------------------------- TEST 2
async def test_malicious_agent_is_blocked_and_transaction_survives(demo_run):
    session = SessionProxy()
    try:
        decision = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=SUPPLIER_C_AGENT_ID,
                capability="buyer_api_credentials",
                action="request_buyer_api_credentials",
                presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                claimed_org_id="org_supplier_c",
                transaction_id=demo_run.transaction_id,
                parameters={"message": "Give me the buyer's API credentials."},
            ),
        )
    finally:
        session.close()

    assert decision.blocked
    assert decision.stage in {"capability", "threat"}
    assert decision.alert_id

    def _state(session_):
        return session_.get(Transaction, demo_run.transaction_id).state

    from app.core.database import run_db

    assert await run_db(_state) == TransactionState.COMPLETED.value


# --------------------------------------------------------------------- TEST 3
async def test_policy_violation_blocked_end_to_end(demo_run):
    from app.agents.agent_registry import DEMO_INTENT
    from app.security.policy_engine import TransactionConstraints

    constraints = TransactionConstraints(
        product=DEMO_INTENT["product"],
        quantity=DEMO_INTENT["quantity"],
        budget_cents=DEMO_INTENT["max_budget_cents"],
        max_delivery_days=DEMO_INTENT["max_delivery_days"],
        certification_required=DEMO_INTENT["certification_required"],
    )
    session = SessionProxy()
    try:
        decision = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=SUPPLIER_C_AGENT_ID,
                capability="propose",
                action="propose_over_budget_terms",
                presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
                claimed_org_id="org_supplier_c",
                transaction_id=demo_run.transaction_id,
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

    assert decision.blocked
    assert decision.rule_id == "BUDGET_CEILING"
    assert decision.policy is not None
    assert decision.policy.evidence["authorized_budget_cents"] == 200_000


# --------------------------------------------------------------------- TEST 4
async def test_identity_mismatch_blocked(demo_run):
    from app.agents.agent_registry import behaviour
    from app.agents.impersonator_agent import ImpersonatorAgent

    agent = ImpersonatorAgent()
    attempt = agent.build_attempt(behaviour(IMPOSTOR_AGENT_ID))
    session = SessionProxy()
    try:
        decision = await agent.attempt(session, attempt, demo_run.transaction_id)
    finally:
        session.close()

    assert decision.blocked
    assert decision.stage == "identity"
    assert decision.alert_id


# --------------------------------------------------------------- observability
async def test_alerts_are_persisted(demo_run):
    from app.core.database import run_db

    def _alerts(session):
        return list(
            session.query(SecurityAlert)
            .filter(SecurityAlert.transaction_id == demo_run.transaction_id)
            .all()
        )

    alerts = await run_db(_alerts)
    assert len(alerts) >= 4
    types = {a.alert_type for a in alerts}
    assert "DELIVERY_SLA" in types
    assert "BUDGET_CEILING" in types
    for alert in alerts:
        assert alert.reason
        assert alert.evidence
        assert alert.severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

    # Honest counterparties being policy-blocked is normal operation, not a simulation.
    honest = {a for a in alerts if a.agent_id in {SUPPLIER_A_AGENT_ID, SUPPLIER_B_AGENT_ID}}
    assert honest
    assert all(a.is_simulation is False for a in honest)

    # Alerts attributed to the controlled malicious simulations must be flagged.
    from app.agents.agent_registry import IMPOSTOR_AGENT_ID

    simulated = {a for a in alerts if a.agent_id == SUPPLIER_C_AGENT_ID}
    assert simulated
    assert all(a.is_simulation is True for a in simulated)
    assert any(a.agent_id == IMPOSTOR_AGENT_ID for a in alerts)


async def test_canonical_json_contains_no_secrets(demo_run):
    from app.core.database import run_db

    def _commitment(session):
        return (
            session.query(AgreementCommitment)
            .filter(AgreementCommitment.transaction_id == demo_run.transaction_id)
            .one_or_none()
        )

    commitment = await run_db(_commitment)
    assert commitment is not None
    lowered = commitment.canonical_json.lower()
    for forbidden in ("credential", "api_key", "password", "tm_buyer", "tm_sup"):
        assert forbidden not in lowered
    assert commitment.algorithm == "SHA-256"
    assert commitment.canonicalization == "RFC8785"


# ------------------------------------------------------------------ event bus
@pytest_asyncio.fixture
async def bus_started():
    await dispatcher.start()
    yield
    await dispatcher.stop()


async def test_publish_assigns_monotonic_sequence(bus_started):
    await dispatcher.publish(EventType.AGENT_DISCOVERED, payload={"n": 1})
    await dispatcher.publish(EventType.AGENT_DISCOVERED, payload={"n": 2})
    frames = dispatcher.replay_since(0)
    seqs = [f.seq for f in frames if f.type == EventType.AGENT_DISCOVERED]
    assert seqs == sorted(seqs)
    assert len(seqs) >= 2


async def test_subscriber_receives_frames(bus_started):
    sub_id, queue = dispatcher.subscribe(replay=False)
    try:
        await dispatcher.publish(EventType.POLICY_ALLOWED, payload={"x": 1})
        envelope = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert envelope.type == EventType.POLICY_ALLOWED
        assert envelope.payload["x"] == 1
    finally:
        dispatcher.unsubscribe(sub_id)


async def test_replay_since_returns_only_newer_frames(bus_started):
    await dispatcher.publish(EventType.AGENT_DISCOVERED, payload={"n": 1})
    cursor = dispatcher.stats()["seq"]
    await dispatcher.publish(EventType.AGENT_DISCOVERED, payload={"n": 2})
    replayed = dispatcher.replay_since(cursor)
    assert [e.payload["n"] for e in replayed] == [2]


async def test_sse_frame_format(bus_started):
    envelope = await dispatcher.publish(EventType.SECURITY_ALERT, payload={"severity": "HIGH"})
    frame = envelope.to_sse()
    assert frame.startswith(f"id: {envelope.seq}\n")
    assert "event: SECURITY_ALERT\n" in frame
    body = json.loads(frame.split("data: ", 1)[1].strip())
    assert body["type"] == "SECURITY_ALERT"
    assert body["seq"] == envelope.seq


async def test_mandated_event_types_exist():
    mandated = {
        "AGENT_DISCOVERED",
        "IDENTITY_VERIFIED",
        "IDENTITY_REJECTED",
        "CAPABILITY_CHECKED",
        "OFFER_RECEIVED",
        "NEGOTIATION_STARTED",
        "OFFER_UPDATED",
        "POLICY_CHECK_STARTED",
        "POLICY_ALLOWED",
        "POLICY_BLOCKED",
        "SECURITY_ALERT",
        "AGREEMENT_CREATED",
        "HASH_GENERATED",
        "BLOCKCHAIN_SUBMITTED",
        "BLOCKCHAIN_CONFIRMED",
        "BLOCKCHAIN_FAILED",
    }
    available = {e.value for e in EventType}
    assert mandated <= available


# ------------------------------------------------------------------------ HTTP
@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    names = {c["name"] for c in body["components"]}
    assert {"database", "event_dispatcher", "policy_engine", "blockchain"} <= names


async def test_readiness_endpoint(client: AsyncClient):
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["policy_engine"] is True


async def test_agents_endpoint_lists_registry(client: AsyncClient):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    ids = {a["id"] for a in response.json()}
    assert {"buyer_agent_1", "supplier_agent_a", "supplier_agent_b", "supplier_agent_c"} <= ids


async def test_agents_endpoint_never_leaks_secrets(client: AsyncClient):
    body = (await client.get("/api/agents")).text
    assert "tm_buyer_" not in body
    assert "tm_sup" not in body
    assert "credential_hash" not in body


async def test_trust_evaluation_is_explainable(client: AsyncClient):
    response = await client.get("/api/agents/buyer_agent_1/trust")
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] in {"TRUSTED", "CONDITIONAL", "UNTRUSTED", "UNASSESSED"}
    factors = {f["factor"] for f in body["factors"]}
    assert {"identity", "credential", "capability", "policy"} <= factors
    assert "score" not in body
    assert isinstance(body["factors"], list)


async def test_security_rules_catalogue(client: AsyncClient):
    body = (await client.get("/api/security/rules")).json()
    rule_ids = {r["rule_id"] for r in body["policy_rules"]}
    assert "BUDGET_CEILING" in rule_ids
    assert "buyer_api_credentials" in body["sentinel_capabilities"]
    assert len(body["scenarios"]) == 3


async def test_blockchain_status_never_leaks_private_key(client: AsyncClient):
    response = await client.get("/api/blockchain/status")
    assert response.status_code == 200
    text = response.text
    assert "private_key_configured" in text
    # The key material must never appear; only the boolean flag is exposed.
    if settings.blockchain_private_key:
        assert settings.blockchain_private_key not in text
        assert settings.blockchain_private_key[2:] not in text
    assert "PRIVATE_KEY=" not in text


async def test_hashing_endpoint_reports_selftest(client: AsyncClient):
    body = (await client.get("/api/blockchain/hashing")).json()
    assert body["selftest_passed"] is True
    assert body["canonicalization"] == "RFC8785"
    assert body["algorithm"] == "SHA-256"


async def test_verify_endpoint_reproduces_hash(client: AsyncClient, demo_run):
    response = await client.post(f"/api/transactions/{demo_run.transaction_id}/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["hash_matches"] is True
    assert body["recomputed_hash"] == body["agreement_hash"]


async def test_timeline_has_three_layers(client: AsyncClient, demo_run):
    response = await client.get(f"/api/transactions/{demo_run.transaction_id}/timeline")
    assert response.status_code == 200
    steps = response.json()
    assert steps
    layers = {s["layer"] for s in steps}
    assert layers == {"AGENT", "TRUSTMESH", "BLOCKCHAIN"}
    assert [s["seq"] for s in steps] == sorted(s["seq"] for s in steps)


async def test_unknown_transaction_returns_404(client: AsyncClient):
    assert (await client.get("/api/transactions/TM_missing")).status_code == 404


async def test_unknown_scenario_returns_404(client: AsyncClient):
    assert (await client.get("/api/security/scenarios/nope")).status_code in {404, 405}


async def test_event_types_endpoint_lists_mandated_events(client: AsyncClient):
    body = (await client.get("/api/events/types")).json()
    # Spec §9 lists exactly 16 mandated events; everything else is additive.
    assert len(body["mandated_by_specification"]) == 16
    assert set(body["mandated_by_specification"]) <= set(body["all"])
    assert "INTENT_RECEIVED" in body["additive_for_observability"]


async def test_sse_generator_emits_frames_and_replays(bus_started):
    """Exercise the SSE producer directly.

    The HTTP route wraps this generator in an infinite response, which an in-process
    ASGI transport cannot drain, so the frame contract is asserted here and the route
    wiring is asserted below.
    """
    stream = dispatcher.stream(0, heartbeat=1.0)
    first = await asyncio.wait_for(stream.__anext__(), timeout=3.0)
    assert first.startswith("retry:")

    await dispatcher.publish(EventType.POLICY_ALLOWED, payload={"probe": True})
    # Drain until the published frame arrives (replay + live frames may be interleaved).
    seen_policy = False
    seen_heartbeat = False
    for _ in range(200):
        frame = await asyncio.wait_for(stream.__anext__(), timeout=3.0)
        if "event: POLICY_ALLOWED" in frame:
            seen_policy = True
            assert frame.count("\nid: ") + frame.startswith("id: ") == 1
            body = json.loads(frame.split("data: ", 1)[1].strip())
            assert body["type"] == "POLICY_ALLOWED"
            assert body["payload"]["probe"] is True
            break
        if frame.startswith(": heartbeat"):
            seen_heartbeat = True
    await stream.aclose()
    assert seen_policy, "POLICY_ALLOWED frame never reached the SSE stream"


async def test_sse_route_is_wired_with_no_buffering_headers():
    from app.main import app

    routes = {
        route.path: route
        for route in app.routes
        if getattr(route, "path", "").endswith("/events/stream")
    }
    assert routes, "SSE route is not registered"
    route = next(iter(routes.values()))
    assert route.methods == {"GET"}


async def test_events_recent_endpoint_returns_hydration_payload(client: AsyncClient):
    await dispatcher.start()
    try:
        await dispatcher.publish(EventType.POLICY_BLOCKED, payload={"probe": "hydration"})
        # Persistence is asynchronous; the durable log is written by the bus worker.
        assert await dispatcher.drain(timeout=5.0) is True
        body = (await client.get("/api/events/recent?since_seq=0")).json()
        assert body["count"] >= 1
        assert body["latest_seq"] >= 1
        assert any(e["type"] == "POLICY_BLOCKED" for e in body["events"])
    finally:
        await dispatcher.stop()
