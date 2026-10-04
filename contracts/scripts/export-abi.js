/**
 * Copy the compiled TrustMeshRegistry ABI into the backend.
 *
 * Run after `npm run compile` (or after any deployment, which does it automatically)
 * so `backend/app/blockchain/contract.py` always loads the real ABI instead of its
 * hand-maintained fallback.
 *
 *   npm run export:abi
 */

const fs = require("node:fs");
const path = require("node:path");
const hre = require("hardhat");

const CONTRACT_NAME = "TrustMeshRegistry";

function main() {
  const artifactPath = path.join(
    hre.config.paths.artifacts,
    "contracts",
    `${CONTRACT_NAME}.sol`,
    `${CONTRACT_NAME}.json`,
  );
  if (!fs.existsSync(artifactPath)) {
    throw new Error(
      `No compiled artifact at ${artifactPath}. Run "npm run compile" first.`,
    );
  }

  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
  const outDir = path.resolve(__dirname, "..", "..", "backend", "app", "blockchain", "abi");
  const outPath = path.join(outDir, "TrustMeshRegistry.abi.json");

  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(outPath, `${JSON.stringify(artifact.abi, null, 2)}\n`, "utf8");

  const required = [
    "commitAgreement",
    "commitmentCount",
    "commitments",
    "getCommitment",
    "existsByTransactionRef",
    "AgreementCommitted",
  ];
  const names = artifact.abi.map((entry) => entry.name || entry.type);
  const missing = required.filter((name) => !names.includes(name));
  if (missing.length > 0) {
    throw new Error(`ABI is missing entries the backend depends on: ${missing.join(", ")}`);
  }

  console.log(`ABI exported -> ${outPath}`);
  console.log(`entries     -> ${artifact.abi.length}`);
  console.log(`verified    -> ${required.join(", ")}`);
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
