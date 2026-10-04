# Deploying TrustMesh

**Short answer on Vercel: good for the frontend, wrong for the backend.**

| Component | Vercel? | Why |
|---|---|---|
| `frontend/` | **Yes — ideal** | Static SPA. Zero-config, free, fast CDN, custom domain. |
| `backend/` | **No** | See below. |
| `contracts/` | **No** | Solidity compiles at build time and deploys to a *blockchain*, not to Vercel. |

### Why Vercel will break the backend

1. **Serverless functions time out.** A demo run takes 30–60s. Vercel's Hobby limit is
   **10 seconds**. `POST /api/transactions/demo` would be killed mid-run.
2. **SSE does not work.** The entire live feed is one long-lived HTTP response. Vercel's
   functions buffer and cap responses; the Agent Network graph, activity feed and
   Security Center would all silently stop updating.
3. **No persistent filesystem.** SQLite is ephemeral — every cold start wipes the
   registry and audit history.
4. **No long-running process.** The asyncio event bus and the Web3 confirmation poller
   assume a persistent process.

So: **frontend → Vercel, backend → a container host that supports long-lived requests.**

---

## Recommended free stack

```
contracts/  ──►  public EVM testnet (Sepolia or Amoy, free RPC + faucet)
backend/    ──►  Render  (free, Docker, SSE works)
frontend/   ──►  Vercel (free, static)
```

| Host | Free tier | Cold start | SSE | Card needed |
|---|---|---|---|---|
| **Render** | 512 MB, spins down after ~15 min idle | **30–60 s** | Yes | No |
| **Cloud Run** | 2M req/mo, 1 CPU, 512 MB | **~2 s** | Yes | Yes |
| Fly.io | Small allowance | ~1 s | Yes | Yes |
| Koyeb | 512 MB | ~5 s | Yes | No |

**Start with Render** (no card, least friction). If the cold start is awkward during your
demo, switch the backend to Cloud Run using the same `backend/Dockerfile` — no code change.

> **Cold-start tip for Render:** hit `https://<your-api>/api/health` ~60 seconds before
> you present. The banner turns green and it stays awake while you talk.

---

## Step 0 — Rotate the exposed key (do this first)

A real Google Gemini API key was found in `backend/.env.example` and has been removed.
`.env.example` is a file meant to be public, so **treat that key as compromised**:

1. Go to <https://aistudio.google.com/apikey>
2. Delete the key beginning `AQ.Ab8RN6…`
3. Create a new one and put it **only** in `backend/.env`

Verified: `backend/.env.example` now contains no key material.

---

## Step 1 — Push to GitHub

The repo is already initialised and staged. `.gitignore` already excludes `.env`,
`node_modules`, `*.db`, and Hardhat build output.

```bash
cd K:\hackatthon\trustmesh

# Safety gate - refuses to continue if a secret would be committed
python backend/scripts/preflight.py --staged

git commit -m "TrustMesh: REASON -> ENFORCE -> VERIFY

Cross-organizational agent trust infrastructure.
- FastAPI control plane: deterministic policy engine, asyncio event bus, SSE
- TrustMeshRegistry.sol: RFC 8785 + SHA-256 agreement commitments
- Vite/React/Tailwind console: live agent network, security center, chain explorer"

gh repo create trustmesh --private --source=. --remote=origin --push
# or: create the repo on github.com, then
# git remote add origin https://github.com/<you>/trustmesh.git
# git push -u origin main
```

> Use a **private** repo for a hackathon project. If you must go public, re-run
> `python backend/scripts/preflight.py` immediately before every push.

---

## Step 2 — Deploy the contract to a public testnet

A Hardhat node on your laptop **cannot** be reached from the cloud, so the hosted backend
must use a public RPC.

```bash
cd contracts
cp .env.example .env      # then fill in BLOCKCHAIN_RPC_URL and BLOCKCHAIN_PRIVATE_KEY
npx hardhat compile
npx hardhat run scripts/deploy.js --network sepolia
```

Free resources:

| Network | RPC | Faucet | Chain ID |
|---|---|---|---|
| Sepolia | `https://ethereum-sepolia-rpc.publicnode.com` | <https://sepoliafaucet.com> | 11155111 |
| Amoy | `https://rpc-amoy.polygon.technology` | <https://faucet.polygon.technology> | 80002 |

The deploy script prints the address and the exact `.env` lines to copy. Keep it open —
you need the address for Step 3.

**Fund the deployer** from the faucet first, or the deployment fails with a zero balance.

---

## Step 3 — Deploy the backend to Render

1. <https://render.com> → **New → Blueprint**
2. Connect the `trustmesh` repo. Render finds `render.yaml` automatically.
3. Fill in the `sync: false` values:

   | Key | Value |
   |---|---|
   | `BLOCKCHAIN_RPC_URL` | `https://ethereum-sepolia-rpc.publicnode.com` |
   | `BLOCKCHAIN_CONTRACT_ADDRESS` | address printed in Step 2 |
   | `BLOCKCHAIN_PRIVATE_KEY` | deployer key (**testnet only — never a funded key**) |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://trustmesh.vercel.app` |

4. **Deploy.** First build takes ~5 minutes.
5. Wait for green, then verify:

```bash
curl https://<your-api>.onrender.com/api/health
```

You want `"status": "ok"` with all five components `up`.

> **If blockchain shows `degraded`:** the RPC is unreachable or the contract address is
> wrong. Check `GET /api/blockchain/status` — the `notes` array names the exact problem.
> If you have no RPC at all, set `BLOCKCHAIN_MODE=simulated`; commitments are still
> hashed and locally verifiable, just labelled `SIMULATED` instead of `CONFIRMED`.

---

## Step 4 — Deploy the frontend to Vercel

```bash
cd frontend
npx vercel --prod          # first time: npx vercel  (link the project)
```

Or import the repo at <https://vercel.com/new>:

| Setting | Value |
|---|---|
| Framework preset | **Vite** |
| Build command | `npm run build` |
| Output directory | `dist` |
| **Environment variable** | `VITE_API_URL` = `https://<your-api>.onrender.com` |

> **`VITE_API_URL` is mandatory.** Vite inlines `VITE_*` variables at *build* time, so add
> the variable **before** the first deploy. Without it the SPA calls
> `trustmesh.vercel.app/api/...` and every request 404s — a blank, dead app.

Then add the Vercel origin to the backend's `CORS_ORIGINS` and let Render redeploy.

**No rewrite rules are needed** — the app uses `HashRouter`, so client-side routes such as
`/#/security` resolve on a static host without server configuration.

---

## Step 5 — Verify the deployed stack

```bash
# 1. backend healthy
curl https://<your-api>.onrender.com/api/health

# 2. frontend can reach it (run from the frontend folder)
cd frontend
node scripts/check-live-api.mjs https://<your-api>.onrender.com
```

All 32 checks should pass, including `demo run completed` and `verifyTransaction`.

Then open the Vercel URL and confirm:

- [ ] Navbar shows a **green `live` badge** with an increasing `#seq` (SSE connected)
- [ ] **Run demonstration** produces offers, blocked alerts, and a commitment
- [ ] **Blockchain** page shows `Confirmed` or `Simulated`, never a crash
- [ ] Refreshing mid-demo does not break the feed

---

## Local development after this

Nothing changes — `.env` files are per-machine and gitignored.

```bash
# terminal 1 - local chain
cd contracts && npx hardhat node

# terminal 2 - backend
cd backend && python -m uvicorn app.main:app --reload --port 8000

# terminal 3 - contracts (only needed once per chain restart)
cd contracts && npx hardhat run scripts/deploy.js --network localhost

# terminal 4 - frontend
cd frontend && npm run dev
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Frontend loads, all panels empty, console shows 404 on `/api` | `VITE_API_URL` not set at build time | Add it in Vercel and redeploy |
| Navbar badge says `disconnected` | Backend CORS rejecting the Vercel origin | Add the exact Vercel URL to `CORS_ORIGINS` |
| `POST /transactions/demo` returns 504 | Backend spun down (Render free tier) | Wake it via `/api/health` first; move to Cloud Run |
| Blockchain `degraded` | No RPC, or wrong contract address | `GET /api/blockchain/status` → read `notes` |
| Everything `FAILED` on chain | Deployer out of test ETH | Faucet again |
| `sqlite3.OperationalError: no such table` mid-deploy | Ephemeral `/data` wiped | Harmless — the seeder recreates and re-seeds on boot |
| Deployment hangs on first build | Docker building wheels | First build ~5 min; subsequent ~1 min |

---

## Notes on the free tiers

- **Render free** instances sleep after ~15 minutes idle and have a monthly hour cap. Fine
  for a demo; not for a service.
- **SQLite is ephemeral** on all free tiers. The registry re-seeds automatically on boot
  (the seeder is idempotent), so the app always comes up working — you just lose demo
  history. Attach a disk and point `DATABASE_URL` at it if you want persistence.
- **Never deploy a funded key.** Use a testnet-only deployer, and rotate it after the
  demo.

---

TrustMesh is a hackathon prototype. Threat scenarios are simulated. No production
readiness or certification is claimed.
