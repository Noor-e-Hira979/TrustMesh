# TrustMesh Contracts

`TrustMeshRegistry.sol` — the tamper-evident commitment layer for TrustMesh.

Blockchain is **not** the security engine. TrustMesh performs real-time enforcement
in the backend; this contract is the public, verifiable witness that an agreement
existed in a particular form.

```
Agreement Engine  ->  BlockchainService  ->  Web3 / EVM Adapter  ->  TrustMeshRegistry.sol  ->  Public EVM testnet
```

---

## What is stored on-chain

| Field | Type | Purpose |
|---|---|---|
| `agreementHash` | `bytes32` | SHA-256 of the RFC 8785 canonical agreement |
| `transactionRef` | `bytes32` | SHA-256 of the TrustMesh transaction id |
| `buyerAgentRef` | `bytes32` | SHA-256 of the buyer agent id (pseudonymous) |
| `supplierAgentRef` | `bytes32` | SHA-256 of the supplier agent id (pseudonymous) |
| `committedAt` | `uint64` | `block.timestamp` — the chain is the time authority |
| `commitBlock` | `uint256` | `block.number` |
| `registrant` | `address` | Backend service address that submitted it |

## What is never stored

Credentials, API keys, prices, quantities, product names, negotiation transcripts and
organization legal names. The contract returns this list from `neverStoredOnChain()` so
the guarantee is machine-checkable rather than a documentation promise.

There are no tokens, NFTs, payments, DAO mechanics, transfers or ownership functions.
The contract is non-payable, holds no ETH, and its test suite asserts all of that.

## Immutability

* One commitment per `transactionRef`; a second attempt reverts with
  `CommitmentAlreadyExists`.
* No update, delete, revoke or transfer function exists — asserted by a test.
* Everything else is `view`.

---

## Quick start

```bash
npm install
npm run compile
npm test          # 28 tests
npm run deploy    # ephemeral hardhat chain + ABI export to the backend
```

### Local node (recommended for demos)

```bash
# terminal 1
npm run node

# terminal 2
npm run deploy:localhost
```

The deploy script prints the address and the exact `.env` lines to copy.

### Public testnet

```bash
cp .env.example .env      # then fill in BLOCKCHAIN_RPC_URL and BLOCKCHAIN_PRIVATE_KEY
npm run deploy:sepolia
```

Free resources:

* Sepolia RPC — `https://ethereum-sepolia-rpc.publicnode.com`
* Sepolia faucet — `https://sepoliafaucet.com`
* Amoy (Polygon) RPC — `https://rpc-amoy.polygon.technology`
* Amoy faucet — `https://faucet.polygon.technology`

A deployed testnet account needs test ETH. Without it the deployment will report a zero
balance rather than failing silently.

---

## Wiring it into the backend

`npm run deploy` (and `npm run deploy:*`) automatically writes the compiled ABI to
`backend/app/blockchain/abi/TrustMeshRegistry.abi.json`, so the backend always loads the
ABI of the contract that was actually deployed.

Then in `backend/.env`:

```
BLOCKCHAIN_NETWORK=sepolia
BLOCKCHAIN_CHAIN_ID=11155111
BLOCKCHAIN_RPC_URL=https://ethereum-sepolia-rpc.publicnode.com
BLOCKCHAIN_PRIVATE_KEY=0x...        # backend only, never exposed to the frontend
BLOCKCHAIN_CONTRACT_ADDRESS=0x...
BLOCKCHAIN_MODE=live
```

If no RPC is configured, `BLOCKCHAIN_MODE=auto` degrades to `SIMULATED`: commitments are
still hashed, persisted and locally verifiable, and the UI labels them `SIMULATED` so
they are never mistaken for on-chain confirmations.

---

## Verifying an agreement independently

```bash
# 1. Read the canonical JSON from the backend
curl http://localhost:8000/api/transactions/<txn>/verify

# 2. Recompute
#    canonical JSON -> RFC 8785 (sorted keys, minimal escaping, ECMAScript numbers)
#                  -> SHA-256
#                  -> compare with the on-chain agreementHash

# 3. Or read it straight from the chain
cast call <address> "getCommitment(bytes32)(tuple,bool)" <transactionRef> --rpc-url <rpc>
```

`/api/transactions/{id}/verify` recomputes the digest from the stored canonical bytes and,
when a node is reachable, reads the commitment back from `TrustMeshRegistry` and compares
the two.

---

## API

| Function | Mutability | Notes |
|---|---|---|
| `commitAgreement(bytes32,bytes32,bytes32,bytes32)` | non-payable | Appends one commitment |
| `getCommitment(bytes32)` | view | `(Commitment, bool exists)` |
| `getCommitmentByIndex(uint256)` | view | Reverts `CommitmentNotFound` out of range |
| `commitments(uint256)` | view | Index accessor, matches the backend ABI |
| `existsByTransactionRef(bytes32)` | view | |
| `commitmentCount()` | view | |
| `agreementHashScheme()` | pure | `SHA-256(RFC8785-canonical-JSON)` |
| `registryVersion()` | pure | `trustmesh.registry.v1` |
| `neverStoredOnChain()` | pure | Data-minimisation guarantee |

Event: `AgreementCommitted(bytes32 indexed transactionRef, address indexed registrant, bytes32 agreementHash, bytes32 buyerAgentRef, bytes32 supplierAgentRef, uint64 committedAt, uint256 commitBlock)`

Errors: `InvalidAgreementHash`, `InvalidTransactionRef`, `InvalidAgentRef`,
`CommitmentAlreadyExists`, `CommitmentNotFound`.

---

TrustMesh is a hackathon prototype. Threat scenarios are simulated. No production
readiness or certification is claimed.
