require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

/**
 * Hardhat configuration for the TrustMeshRegistry.
 *
 * Networks
 * --------
 * hardhat / localhost : in-process chain, used by `npm test` and local demos.
 * sepolia              : public Ethereum testnet, free RPC and faucet.
 * amoy                 : public Polygon testnet, free RPC and faucet.
 *
 * Secrets come from the environment only. Nothing here has a hard-coded default key.
 *
 * @type {import('hardhat/config').HardhatUserConfig}
 */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // Pinned so compilation is reproducible across machines.
      evmVersion: "paris",
      metadata: { bytecodeHash: "none" },
    },
  },
  networks: {
    hardhat: {
      chainId: 31337,
    },
    localhost: {
      url: process.env.RPC_URL || "http://127.0.0.1:8545",
      chainId: 31337,
    },
    sepolia: {
      url: process.env.BLOCKCHAIN_RPC_URL || "",
      chainId: 11155111,
      accounts: process.env.BLOCKCHAIN_PRIVATE_KEY ? [process.env.BLOCKCHAIN_PRIVATE_KEY] : [],
    },
    amoy: {
      url: process.env.BLOCKCHAIN_RPC_URL || "",
      chainId: 80002,
      accounts: process.env.BLOCKCHAIN_PRIVATE_KEY ? [process.env.BLOCKCHAIN_PRIVATE_KEY] : [],
    },
  },
  paths: {
    sources: "./contracts",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts",
  },
  mocha: {
    timeout: 120000,
  },
};
