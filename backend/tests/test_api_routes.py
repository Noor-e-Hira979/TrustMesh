"""API route contract tests.

Covers the HTTP surface itself: pagination envelopes, the §18 overview aggregate, the
per-transaction sub-resources, the mandated security scenarios, alert triage and the
guarantee that no route ever leaks a secret.

These use the in-process ASGI transport, so they exercise real routing, real validation
and real query construction without a socket.

Referenced by: ``app.api.routes``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agents.agent_registry import (
    BUYER_AGENT_ID,
    IMPOSTOR_AGENT_ID,
    SUPPLIER_A_AGENT_ID,
    SUPPLIER_B_AGENT_ID,
    SUPPLIER_C_AGENT_ID,
    credential_for,
)
from app.core.models import AgreementCommitment, SecurityAlert, Transaction, TransactionState
from app.orchestrator.demo_orchestrator import orchestrator

pytestmark = pytest.mark.usefixtures("db")


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest_asyncio.fixture
async def demo(client: AsyncClient):
    """One completed demo run, shared by the tests that need real lifecycle data."""
    run = await orchestrator.run_demo(step_delay=0.0, use_ai=False, commit_on_chain=True)
    assert run.state == TransactionState.COMPLETED.value, run.errors
    return run


# ------------------------------------------------------------------ pagination
async def test_transactions_use_the_page_envelope(client: AsyncClient, demo):
    body = (await client.get("/api/transactions?limit=5")).json()
    assert set(body) >= {"items", "total", "limit", "offset", "has_more", "next_offset"}
    assert body["limit"] == 5
    assert body["total"] >= 1
    assert len(body["items"]) <= 5
    assert isinstance(body["has_more"], bool)
    if body["has_more"]:
        assert body["next_offset"] == body["offset"] + len(body["items"])


async def test_pagination_windows_do_not_overlap(client: AsyncClient, demo):
    first = (await client.get("/api/transactions?limit=1&offset=0")).json()
    second = (await client.get("/api/transactions?limit=1&offset=1")).json()
    if first["items"] and second["items"]:
        assert first["items"][0]["id"] != second["items"][0]["id"]


async def test_pagination_rejects_out_of_range_values(client: AsyncClient):
    assert (await client.get("/api/transactions?limit=0")).status_code == 422
    assert (await client.get("/api/transactions?limit=9999")).status_code == 422
    assert (await client.get("/api/transactions?offset=-1")).status_code == 422


async def test_transactions_filter_by_state(client: AsyncClient, demo):
    body = (await client.get("/api/transactions?state=completed")).json()
    assert body["total"] >= 1
    assert all(item["state"] == "completed" for item in body["items"])


async def test_alerts_use_the_page_envelope(client: AsyncClient, demo):
    body = (await client.get("/api/security/alerts?limit=3")).json()
    assert body["limit"] == 3
    assert body["total"] >= 1


async def test_alerts_filter_by_severity_and_simulation(client: AsyncClient, demo):
    body = (await client.get("/api/security/alerts?severity=CRITICAL")).json()
    assert all(item["severity"] == "CRITICAL" for item in body["items"])
    assert (await client.get("/api/security/alerts?severity=NOPE")).status_code == 422


async def test_commitments_use_the_page_envelope(client: AsyncClient, demo):
    body = (await client.get("/api/blockchain/commitments?limit=10")).json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1


# ----------------------------------------------------------- sub-resources
async def test_offers_subresource(client: AsyncClient, demo):
    body = (await client.get(f"/api/transactions/{demo.transaction_id}/offers")).json()
    assert body["total"] >= 3
    rounds = [item["round_number"] for item in body["items"]]
    assert rounds == sorted(rounds)


async def test_offers_filter_by_agent(client: AsyncClient, demo):
    body = (
        await client.get(f"/api/transactions/{demo.transaction_id}/offers?agent_id={SUPPLIER_B_AGENT_ID}")
    ).json()
    assert body["total"] >= 1
    assert all(item["agent_id"] == SUPPLIER_B_AGENT_ID for item in body["items"])


async def test_decisions_subresource_answers_the_audit_questions(client: AsyncClient, demo):
    """Spec §31: who, which capability, which rule, allowed or blocked, why."""
    body = (await client.get(f"/api/transactions/{demo.transaction_id}/decisions")).json()
    assert body["total"] >= 8
    for item in body["items"]:
        assert item["agent_id"]
        assert item["decision"] in {"ALLOW", "BLOCK"}
        assert item["rule_id"]
        assert item["reason"]
        assert item["stage"]


async def test_decisions_filter_by_decision_and_stage(client: AsyncClient, demo):
    blocked = (
        await client.get(f"/api/transactions/{demo.transaction_id}/decisions?decision=BLOCK")
    ).json()
    assert blocked["total"] >= 1
    assert all(item["decision"] == "BLOCK" for item in blocked["items"])
    policy = (
        await client.get(f"/api/transactions/{demo.transaction_id}/decisions?stage=policy")
    ).json()
    assert all(item["stage"] == "policy" for item in policy["items"])
    assert (await client.get(f"/api/transactions/{demo.transaction_id}/decisions?decision=MAYBE")).status_code == 422


async def test_transaction_alerts_subresource(client: AsyncClient, demo):
    body = (await client.get(f"/api/transactions/{demo.transaction_id}/alerts")).json()
    assert body["total"] >= 4
    for item in body["items"]:
        assert item["decision"] == "BLOCK"
        assert item["reason"]


async def test_subresources_404_for_unknown_transaction(client: AsyncClient):
    for suffix in ("offers", "decisions", "alerts", "timeline"):
        response = await client.get(f"/api/transactions/TM_missing/{suffix}")
        assert response.status_code == 404, suffix


async def test_timeline_has_all_three_layers(client: AsyncClient, demo):
    steps = (await client.get(f"/api/transactions/{demo.transaction_id}/timeline")).json()
    assert {s["layer"] for s in steps} == {"AGENT", "TRUSTMESH", "BLOCKCHAIN"}
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))
    # Ordering must be chronological.
    timestamps = [s["ts"] for s in steps]
    assert timestamps == sorted(timestamps)
    stages = [s["stage"] for s in steps]
    assert stages.index("intent") == 0
    assert "chain" in stages


# ------------------------------------------------------------------- overview
async def test_overview_returns_everything_the_dashboard_needs(client: AsyncClient, demo):
    body = (await client.get("/api/overview")).json()
    assert body["principle"] == "REASON -> ENFORCE -> VERIFY"
    names = {c["name"] for c in body["components"]}
    assert {"database", "event_dispatcher", "ai_reasoning", "policy_engine", "blockchain"} <= names
    assert body["stats"], "dashboard headline stats are empty"
    assert body["active_agents"]
    assert body["current_transaction"] is not None
    assert body["current_transaction"]["id"] == demo.transaction_id
    assert body["negotiation_state"]
    assert body["recent_alerts"]
    assert body["recent_commitments"]
    assert body["recent_events"]
    assert body["blockchain_connection"] in {
        "connected",
        "degraded",
        "simulated",
        "unavailable",
    }
    assert "prototype" in body["disclaimer"].lower()


async def test_overview_prefers_an_in_flight_transaction(client: AsyncClient, demo):
    body = (await client.get("/api/overview")).json()
    state = body["current_transaction"]["state"]
    assert state in {
        "created",
        "discovering",
        "verifying_identity",
        "offers_received",
        "evaluating",
        "negotiating",
        "agreed",
        "hashed",
        "committed",
        "completed",
    }


async def test_overview_handles_an_empty_database(client: AsyncClient):
    """No transactions at all must still render, not 500."""
    body = (await client.get("/api/overview")).json()
    assert body["current_transaction"] is None
    assert body["negotiation_state"] == "idle"
    assert body["recent_commitments"] == []


async def test_overview_respects_limit_parameters(client: AsyncClient, demo):
    body = (await client.get("/api/overview?alerts_limit=2&events_limit=3")).json()
    assert len(body["recent_alerts"]) <= 2
    assert len(body["recent_events"]) <= 3


# ------------------------------------------------------------------ scenarios
@pytest.mark.parametrize(
    ("scenario", "expected_stage"),
    [
        ("unauthorized_capability", "capability"),
        ("policy_violation", "policy"),
        ("identity_mismatch", "identity"),
    ],
)
async def test_each_mandated_scenario_blocks_at_its_stage(
    client: AsyncClient, demo, scenario: str, expected_stage: str
):
    body = (await client.post(f"/api/security/scenarios/{scenario}")).json()
    assert body["scenario"] == scenario
    assert body["decision"] == "BLOCK", body
    assert body["rule_id"]
    assert body["reason"]
    assert body["explanation"]
    assert body["evidence"]["stage"] == expected_stage
    assert body["alert_id"]
    assert body["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    assert "POLICY_BLOCKED" in body["events_emitted"]
    assert "SECURITY_ALERT" in body["events_emitted"]


async def test_unknown_scenario_is_404(client: AsyncClient):
    assert (await client.post("/api/security/scenarios/not_a_scenario")).status_code == 404


async def test_scenarios_keep_the_legitimate_transaction_intact(client: AsyncClient, demo):
    before = (await client.get(f"/api/transactions/{demo.transaction_id}")).json()["state"]
    for scenario in ("unauthorized_capability", "policy_violation", "identity_mismatch"):
        await client.post(f"/api/security/scenarios/{scenario}")
    after = (await client.get(f"/api/transactions/{demo.transaction_id}")).json()["state"]
    assert before == after == TransactionState.COMPLETED.value


# ------------------------------------------------------------- alert triage
async def test_alert_status_can_be_acknowledged_and_resolved(client: AsyncClient, demo):
    feed = (await client.get("/api/security/alerts?limit=1")).json()
    alert_id = feed["items"][0]["id"]

    acked = (
        await client.post(f"/api/security/alerts/{alert_id}/status?status=ACKNOWLEDGED")
    ).json()
    assert acked["status"] == "ACKNOWLEDGED"
    assert acked["reason"], "triage must not erase the evidence"

    resolved = (await client.post(f"/api/security/alerts/{alert_id}/status?status=RESOLVED")).json()
    assert resolved["status"] == "RESOLVED"

    assert (await client.post(f"/api/security/alerts/{alert_id}/status?status=BOGUS")).status_code == 422
    assert (await client.post("/api/security/alerts/SA_missing/status?status=RESOLVED")).status_code == 404


async def test_open_only_filter(client: AsyncClient, demo):
    feed = (await client.get("/api/security/alerts?open_only=true")).json()
    assert all(item["status"] == "OPEN" for item in feed["items"])


async def test_security_stats_reports_simulations(client: AsyncClient, demo):
    body = (await client.get("/api/security/stats")).json()
    assert body["total"] >= 4
    assert body["blocked_actions"] >= 4
    assert body["simulated_alerts"] >= 1
    assert body["simulation_only"] is True


# --------------------------------------------------------------------- agents
async def test_trust_evaluation_scoped_to_a_capability(client: AsyncClient):
    allowed = (await client.get(f"/api/agents/{BUYER_AGENT_ID}/trust?capability=search")).json()
    assert allowed["tier"] in {"TRUSTED", "CONDITIONAL", "UNTRUSTED", "UNASSESSED"}
    allowed_factors = {f["factor"]: f["status"] for f in allowed["factors"]}
    assert allowed_factors["capability"] == "AUTHORIZED"

    denied = (
        await client.get(f"/api/agents/{BUYER_AGENT_ID}/trust?capability=buyer_api_credentials")
    ).json()
    denied_factors = {f["factor"]: f["status"] for f in denied["factors"]}
    assert denied_factors["capability"] == "NOT_AUTHORIZED"
    assert denied["tier"] == "UNTRUSTED"
    assert "capability_not_authorized" in denied["blocking_factors"]


async def test_trust_response_has_no_numeric_score(client: AsyncClient):
    """Spec §8: explainability over fake numerical sophistication."""
    body = (await client.get(f"/api/agents/{SUPPLIER_B_AGENT_ID}/trust")).json()
    assert "score" not in body
    assert "trust_score" not in body
    assert body["tier"] in {"TRUSTED", "CONDITIONAL", "UNTRUSTED", "UNASSESSED"}
    # Every factor is a labelled, explained status - never a number.
    for factor in body["factors"]:
        assert factor["status"] in {
            "VERIFIED",
            "REJECTED",
            "UNVERIFIED",
            "VALID",
            "MISSING",
            "INVALID",
            "EXPIRED",
            "REVOKED",
            "AUTHORIZED",
            "DECLARED",
            "NOT_AUTHORIZED",
            "DENIED",
            "GRANTED",
            "NOT_EVALUATED",
            "CLEAN",
            "DETECTED",
            "COMPLIANT",
            "VIOLATION",
            "OBSERVED",
            "NONE",
        }
        assert factor["detail"]
    assert body["credential_fingerprint"] != "none"
    assert credential_for(SUPPLIER_B_AGENT_ID) not in (await client.get(
        f"/api/agents/{SUPPLIER_B_AGENT_ID}/trust"
    )).text


async def test_impostor_is_not_in_the_agent_registry(client: AsyncClient):
    ids = {a["id"] for a in (await client.get("/api/agents")).json()}
    assert IMPOSTOR_AGENT_ID not in ids
    assert (await client.get(f"/api/agents/{IMPOSTOR_AGENT_ID}")).status_code == 404


async def test_catalogue_includes_the_unregistered_impostor(client: AsyncClient):
    agents = (await client.get("/api/agents/registry-catalogue")).json()["agents"]
    by_id = {a["id"]: a for a in agents}
    assert by_id[IMPOSTOR_AGENT_ID]["registered"] is False
    assert by_id[IMPOSTOR_AGENT_ID]["is_malicious_simulation"] is True
    assert by_id[BUYER_AGENT_ID]["registered"] is True


async def test_agents_filter_by_type(client: AsyncClient):
    body = (await client.get("/api/agents?agent_type=buyer")).json()
    assert body
    assert all(a["agent_type"] == "buyer" for a in body)
    assert (await client.get("/api/agents?agent_type=alien")).status_code == 422


async def test_organization_detail_includes_its_agents(client: AsyncClient):
    body = (await client.get("/api/organizations/org_buyer")).json()
    assert body["organization"]["id"] == "org_buyer"
    assert body["agent_count"] >= 1
    assert all(a["org_id"] == "org_buyer" for a in body["agents"])
    assert (await client.get("/api/organizations/org_nope")).status_code == 404


# ----------------------------------------------------------------- blockchain
async def test_commitment_confirm_reports_state_instead_of_failing(client: AsyncClient, demo):
    """Spec §34: an unreachable node is a state, not a request failure."""
    feed = (await client.get("/api/blockchain/commitments?limit=1")).json()
    commitment_id = feed["items"][0]["id"]

    response = await client.post(f"/api/blockchain/commitments/{commitment_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"CONFIRMED", "SIMULATED", "PENDING", "FAILED"}
    assert body["mode"] in {"live", "simulated"}
    assert "note" in body


async def test_confirm_unknown_commitment_is_404(client: AsyncClient):
    assert (await client.post("/api/blockchain/commitments/AC_missing/confirm")).status_code == 404


async def test_commitment_explorer_url_is_present_when_confirmed(client: AsyncClient, demo):
    feed = (await client.get("/api/blockchain/commitments?limit=1")).json()
    commitment = feed["items"][0]
    assert commitment["status"] in {"SIMULATED", "CONFIRMED", "PENDING"}
    assert commitment["agreement_hash"].startswith("0x")
    assert commitment["algorithm"] == "SHA-256"
    assert commitment["canonicalization"] == "RFC8785"


async def test_blockchain_abi_is_served(client: AsyncClient):
    body = (await client.get("/api/blockchain/abi")).json()
    names = {entry.get("name") for entry in body["abi"]}
    assert {"commitAgreement", "getCommitment", "commitmentCount"} <= names


# -------------------------------------------------------------- secret safety
@pytest.mark.parametrize(
    "path",
    [
        "/api/health",
        "/api/health/architecture",
        "/api/overview",
        "/api/agents",
        "/api/agents/registry-catalogue",
        "/api/organizations",
        "/api/organizations/org_buyer",
        "/api/security/alerts",
        "/api/security/stats",
        "/api/security/rules",
        "/api/blockchain/status",
        "/api/blockchain/abi",
        "/api/blockchain/hashing",
        "/api/events/types",
        "/api/events/bus",
    ],
)
async def test_no_route_ever_returns_secret_material(client: AsyncClient, path: str):
    """Every fixture secret must be absent from every public payload."""
    text = (await client.get(path)).text
    for secret in (
        credential_for(BUYER_AGENT_ID),
        credential_for(SUPPLIER_A_AGENT_ID),
        credential_for(SUPPLIER_B_AGENT_ID),
        credential_for(SUPPLIER_C_AGENT_ID),
    ):
        assert secret not in text, f"{path} leaked a credential secret"
    assert "credential_hash" not in text
    assert "tm_buyer_" not in text
    assert "tm_sup" not in text


async def test_intent_validation_rejects_impossible_envelopes(client: AsyncClient):
    bad_payloads = [
        {"product": "", "quantity": 1000, "max_budget_cents": 200000, "max_delivery_days": 7},
        {"product": "x", "quantity": 0, "max_budget_cents": 200000, "max_delivery_days": 7},
        {"product": "x", "quantity": 10, "max_budget_cents": -1, "max_delivery_days": 7},
        {"product": "x", "quantity": 10, "max_budget_cents": 100, "max_delivery_days": 0},
        {"product": "x", "quantity": 10, "max_budget_cents": 100, "max_delivery_days": 400},
    ]
    for payload in bad_payloads:
        response = await client.post("/api/transactions", json=payload)
        assert response.status_code == 422, payload


async def test_hashing_endpoints_agree_with_each_other(client: AsyncClient):
    a = (await client.get("/api/blockchain/hashing")).json()
    b = (await client.get("/api/transactions/hashing/describe")).json()
    assert a["canonicalization"] == b["canonicalization"] == "RFC8785"
    assert a["algorithm"] == b["algorithm"] == "SHA-256"
    assert a["selftest_passed"] is True
    assert a["on_chain_payload"] == b["on_chain_payload"]
    assert all(vector["passed"] for vector in a["vectors"])


async def test_architecture_endpoint_describes_the_authority_split(client: AsyncClient):
    body = (await client.get("/api/health/architecture")).json()
    assert body["principle"] == "REASON -> ENFORCE -> VERIFY"
    assert body["pipeline"] == [
        "schema",
        "identity",
        "credential",
        "capability",
        "authorization",
        "threat",
        "policy",
    ]
    assert set(body["authority"]) == {"ai", "trustmesh", "blockchain"}
    assert "buyer_api_credentials" in body["sentinel_capabilities"]


async def test_event_types_reports_all_sixteen_mandated_events(client: AsyncClient):
    body = (await client.get("/api/events/types")).json()
    assert len(body["mandated_by_specification"]) == 16
    assert set(body["mandated_by_specification"]) <= set(body["all"])
    assert set(body["layers"]) == set(body["all"])
    assert "POLICY_BLOCKED" in body["blocking"]
    assert body["layers"]["POLICY_BLOCKED"] == "TRUSTMESH"
    assert body["layers"]["HASH_GENERATED"] == "BLOCKCHAIN"
    assert body["layers"]["OFFER_RECEIVED"] == "AGENT"
