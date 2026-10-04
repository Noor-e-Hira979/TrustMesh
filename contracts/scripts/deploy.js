/**
 * Deploy TrustMeshRegistry and export its ABI to the backend.
 *
 * Usage
 * -----
 *   npx hardhat run scripts/deploy.js                       # ephemeral hardhat chain
 *   npx hardhat run scripts/deploy.js --network localhost   # local node (npm run node)
 *   npx hardhat run scripts/deploy.js --network sepolia     # public testnet
 *
 * After a real-network deployment, copy the printed address into backend/.env as
 * BLOCKCHAIN_CONTRACT_ADDRESS. The private key is only ever read from the environment
 * and is never printed, logged or written to a file.
 */

const fs = require("node:fs");
const path = require("node:path");
const hre = require("hardhat");

const CONTRACT_NAME = "TrustMeshRegistry";

/**
 * Resolve the compiled artifact for a contract.
 *
 * Built from `config.paths.artifacts` rather than an API call, so it works the same on
 * every Hardhat 2.x minor version.
 *
 * @param {string} name Contract name.
 * @returns {string} Absolute path to the artifact JSON.
 */
function artifactPathFor(name) {
  return path.join(
    hre.config.paths.artifacts,
    "contracts",
    `${name}.sol`,
    `${name}.json`,
  );
}

/**
 * Write the compiled ABI to the backend so `app.blockchain.contract` always talks to
 * the contract that was actually deployed, not a hand-maintained copy.
 *
 * @param {string} artifactPath Path to the Hardhat artifact JSON.
 * @returns {string} The backend ABI path that was written.
 */
function exportAbi(artifactPath) {
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
  const backendAbiDir = path.resolve(__dirname, "..", "..", "backend", "app", "blockchain", "abi");
  const backendAbiPath = path.join(backendAbiDir, "TrustMeshRegistry.abi.json");

  fs.mkdirSync(backendAbiDir, { recursive: true });
  fs.writeFileSync(backendAbiPath, `${JSON.stringify(artifact.abi, null, 2)}\n`, "utf8");

  console.log(`ABI exported  -> ${backendAbiPath}`);
  console.log(`               ${artifact.abi.length} entries`);
  return backendAbiPath;
}

/**
 * Print a short deployment report. Never prints any secret.
 *
 * @param {object} params
 */
function report({ network, address, deployer, chainId, blockNumber, abiPath }) {
  const line = "-".repeat(64);
  console.log(`\n${line}`);
  console.log("TrustMeshRegistry deployed");
  console.log(line);
  console.log(`network        : ${network}`);
  console.log(`chainId        : ${chainId}`);
  console.log(`address        : ${address}`);
  console.log(`deployer       : ${deployer}`);
  console.log(`block          : ${blockNumber}`);
  console.log(`abi            : ${abiPath}`);
  console.log(`hash scheme    : SHA-256 over RFC 8785 canonical JSON`);
  console.log("on-chain data  : agreement hash, tx ref, pseudonymous agent refs, timestamp");
  console.log("never stored   : credentials, api keys, prices, quantities, transcripts");
  console.log(`${line}\n`);

  console.log("Next step for the backend (.env):");
  console.log(`  BLOCKCHAIN_CONTRACT_ADDRESS=${address}`);
  console.log(`  BLOCKCHAIN_NETWORK=${network}`);
  console.log(`  BLOCKCHAIN_CHAIN_ID=${chainId}\n`);
}

async function main() {
  const { ethers, network } = hre;
  const isLiveNetwork = !["hardhat", "localhost"].includes(network.name);

  if (isLiveNetwork && !process.env.BLOCKCHAIN_RPC_URL) {
    throw new Error(
      `BLOCKCHAIN_RPC_URL is not set. A public EVM RPC is required for --network ${network.name}.`,
    );
  }
  if (isLiveNetwork && !process.env.BLOCKCHAIN_PRIVATE_KEY) {
    throw new Error(
      "BLOCKCHAIN_PRIVATE_KEY is not set. The deployer key must come from the environment.",
    );
  }

  const [deployer] = await ethers.getSigners();
  const balance = await ethers.provider.getBalance(deployer.address);

  console.log(`\nDeploying ${CONTRACT_NAME}`);
  console.log(`  network  : ${network.name}`);
  console.log(`  deployer : ${deployer.address}`);
  console.log(`  balance  : ${ethers.formatEther(balance)} ETH`);
  if (balance === 0n && isLiveNetwork) {
    console.warn("  WARNING   : deployer balance is zero. Fund it from a testnet faucet first.");
  }

  const factory = await ethers.getContractFactory(CONTRACT_NAME);
  const registry = await factory.deploy();
  const deployTx = registry.deploymentTransaction();
  await registry.waitForDeployment();
  const address = await registry.getAddress();
  const blockNumber = await ethers.provider.getBlockNumber();
  const { chainId } = await ethers.provider.getNetwork();

  const abiPath = exportAbi(artifactPathFor(CONTRACT_NAME));

  // Smoke-test the deployed contract before reporting success.
  const count = await registry.commitmentCount();
  const scheme = await registry.agreementHashScheme();
  const neverStored = await registry.neverStoredOnChain();
  if (count !== 0n) throw new Error("fresh registry must start with zero commitments");
  if (!scheme.includes("SHA-256")) throw new Error(`unexpected hash scheme: ${scheme}`);

  report({
    network: network.name,
    address,
    deployer: deployer.address,
    chainId: chainId.toString(),
    blockNumber,
    abiPath,
  });

  console.log("Post-deploy checks:");
  console.log(`  commitmentCount()   : ${count}`);
  console.log(`  agreementHashScheme : ${scheme}`);
  console.log(`  neverStoredOnChain  : ${neverStored.join(", ")}`);
  console.log(`  deploy tx hash      : ${deployTx ? deployTx.hash : "n/a"}\n`);
}

main().catch((error) => {
  console.error(`\nDeployment failed: ${error.message}`);
  process.exitCode = 1;
});
