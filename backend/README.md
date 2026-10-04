# TrustMesh Backend

**Secure Trust Infrastructure for Autonomous AI Agents.**

`REASON → ENFORCE → VERIFY`

The control plane that enforces authority on cross-organizational agent transactions,
streams every decision live, and commits agreement hashes to a public chain.

| Layer | Authority | Never does |
|---|---|---|
| **AI / Gemini** | reason, negotiate, propose | approve its own action, execute, hold a credential |
| **TrustMesh** | identity, credentials, capabilities, authorization, policy, threat | negotiate, or let a probability replace a rule |
| **Blockchain** | tamper-evident commitment | store sensitive data, decide anything |

---

## Quick start

```bash
python -m pip install -r requirements.txt
cp .env.example .env            # optional: works with no configuration at all
python -m uvicorn app.main:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/docs`.

**No API key and no RPC are required.** Without `GEMINI_API_KEY` the AI layer falls
back to the deterministic constraint solver; without `BLOCKCHAIN_RPC_URL` commitments are
recorded locally and labelled `SIMULATED`. The demo behaves identically either way
(spec §10, §34).

Run the full demonstration:

```bash
curl -X POST http://localhost:8000/api/transactions/demo \
     -H 'Content-Type: application/json' \
     -d '{"step_delay_seconds":0.5,"use_ai":true,"commit_on_chain":true}'

curl -N http://localhost:8000/api/events/stream      # watch it happen
```

---

## Verification

```bash
python scripts/verify_system.py --temp-db     # 30 checks: components + spec §30 tests
python -m pytest                              # 173 tests
python scripts/verify_chain.py --help         # real on-chain verification
```

`verify_system.py` runs TEST 1 (success flow), TEST 2 (malicious agent), TEST 3 (policy
violation) and TEST 4 (identity mismatch), then prints a PASS/FAIL matrix and exits
non-zero on any failure.

`verify_chain.py` proves the VERIFY path against a live node — see
[`../contracts/README.md`](../contracts/README.md) for how to start one.

---

## Module map

```
app/
├── main.py                     app factory, lifespan, CORS, log redaction
├── core/
│   ├── config.py               env-driven settings; secrets never serialised
│   ├── database.py             SQLAlchemy 2.0 engine, WAL pragmas, run_db bridge
│   ├── models.py               9 ORM tables + enums + integrity constraints
│   ├── schemas.py              40+ Pydantic v2 contracts (no secret is representable)
│   └── events.py               asyncio EventBus: 23 types, replay, SSE framing
├── api/routes/                 36 endpoints (see ROUTERS in routes/__init__.py)
│   ├── health.py               /health · /health/ready · /health/architecture
│   ├── overview.py             /overview — the whole dashboard in one call
│   ├── agents.py               /agents · /agents/{id}/trust · /organizations
│   ├── transactions.py         create · demo · detail · offers · decisions
│   │                           · alerts · timeline · verify
│   ├── security.py             /security/alerts · /rules · /scenarios/{name}
│   ├── blockchain.py           /blockchain/status · /commitments · /hashing
│   └── events.py               /events/stream (SSE) · /recent · /types
├── trust/
│   ├── trust_engine.py         THE GATEWAY — sole source of ALLOW
│   ├── identity.py             agent + organization identity verification
│   ├── credentials.py          HMAC-digest credentials, constant-time verify
│   └── trust_evaluator.py      explainable factors + tier (no numeric score)
├── security/
│   ├── policy_engine.py        5 deterministic rules → ALLOW/BLOCK
│   ├── capability_guard.py     declared ∩ requested ∩ sentinel deny-list
│   ├── threat_detection.py     5 named interception rules
│   └── security_events.py      single funnel for alerts
├── agents/
│   ├── agent_registry.py       the seeded demo cast
│   ├── buyer_agent.py          intent + authority envelope
│   ├── supplier_agent.py       honest + compromised (simulation) runtimes
│   ├── impersonator_agent.py   controlled impersonation scenario
│   └── negotiation.py          constraint-aware negotiation solver
├── orchestrator/
│   └── demo_orchestrator.py    the deterministic spine of the whole story
├── agreements/
│   ├── hashing.py              RFC 8785 (JCS) + SHA-256, in-repo, dependency-free
│   └── agreement_engine.py     builds and freezes the canonical agreement
├── ai/
│   ├── gemini_client.py        typed client with a hard deterministic fallback
│   ├── prompts.py              versioned prompt builders
│   └── schemas.py              AI output contracts
├── blockchain/
│   ├── service.py              Web3 adapter: live / degraded / simulated
│   ├── contract.py             ABI, explorer URLs, on-chain payload guarantee
│   └── abi/                    compiled ABI, exported by contracts/npm run deploy
└── services/bootstrap.py       idempotent registry seeder
```

---

## The enforcement pipeline

Every consequential action runs these seven stages in order. The pipeline **fails
closed** — any unexpected exception becomes a BLOCK, never an ALLOW.

```
schema → identity → credential → capability → authorization → threat → policy
                                                                          ↓
                                                        ALLOW → execute → audit
```

Structural guarantees:

- **No import path from `app/ai/` to execution.** The AI layer returns data; it cannot
  return a permission. The dependency direction is one-way and grep-checkable.
- **Agent runtimes hold no policy logic.** They build an `ActionRequest` and call
  `trust_engine.request_action()`. They never evaluate a rule or read a credential.
- **Secrets never leave the process.** `GEMINI_API_KEY` and `BLOCKCHAIN_PRIVATE_KEY` are
  `repr=False` settings, a redacting log filter scrubs 32-byte hex blobs, and no response
  schema has a field that could carry one.

### Deterministic rules

| Rule | Expression |
|---|---|
| `BUDGET_CEILING` | `price_cents <= constraints.budget_cents` |
| `DELIVERY_SLA` | `delivery_days <= constraints.max_delivery_days` |
| `CERTIFICATION_VALIDITY` | `rank(certification) >= rank(required)` |
| `QUANTITY_MATCH` | `quantity == constraints.quantity` |
| `COUNTERPARTY_ELIGIBILITY` | `identity_verified AND NOT agent_blocked` |

Threat rules: `CREDENTIAL_SOLICITATION`, `INSTRUCTION_OVERRIDE_ATTEMPT`,
`CROSS_TENANT_DATA_REQUEST`, `CONSTRAINT_TAMPERING`, `IDENTITY_IMPERSONATION`.

Exposed live at `GET /api/security/rules`.

---

## Agreement hashing

```
Final Agreement → RFC 8785 canonical JSON → UTF-8 bytes → SHA-256 → on-chain
```

RFC 8785 is implemented in-repo (`app/agreements/hashing.py`) rather than pulled from a
dependency, because the digest is the product's verifiability guarantee and should be
auditable in-tree. It is tested against the RFC's own requirements: UTF-16 code-unit key
ordering, ECMAScript `Number::toString`, minimal escaping, lone-surrogate rejection,
idempotence.

`GET /api/blockchain/hashing` returns the live self-test.
`POST /api/transactions/{id}/verify` recomputes the digest from the stored canonical bytes
— it never trusts the stored hash — and reads the commitment back from the chain when a
node is reachable.

---

## Events

23 event types on one in-process asyncio bus: the 16 mandated by §9 plus 7 additive for
observability. Each frame carries `layer: AGENT | TRUSTMESH | BLOCKCHAIN | SYSTEM`, which
is what makes the three-layer model machine-readable in the UI.

```
id: 128
event: POLICY_BLOCKED
data: {"seq":128,"type":"POLICY_BLOCKED","layer":"TRUSTMESH","ts":"...","payload":{...}}
```

`id:` is a monotonic cursor, so a browser reconnect sends `Last-Event-ID` and the server
replays exactly the missed frames. A `retry:` field plus `: heartbeat` comments keep
proxies from closing the connection.

---

## Security boundary

- Credentials are stored as HMAC-SHA256 digests and compared with `hmac.compare_digest`.
  Only an HMAC credential id is ever exposed, and only as a truncated fingerprint.
- `SupplierAgentC` and the impostor are flagged `is_malicious_simulation`. They can only
  submit scripted requests to the Gateway. There is no exploitation, credential-theft,
  malware or destructive code anywhere in this repository.
- The blockchain adapter is only ever called after an ALLOW. It cannot grant, revoke or
  evaluate anything, and its failure never aborts a transaction.

---

TrustMesh is a hackathon prototype. Threat scenarios are simulated. No production
readiness or certification is claimed.
