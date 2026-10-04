#!/usr/bin/env node
/**
 * One-command Sepolia deployment for TrustMesh.
 *
 *   node scripts/deploy-sepolia.mjs
 *
 * Why this exists: the deploy step has several easy-to-get-wrong follow-ups (copy the
 * address into two .env files, switch four settings from localhost to Sepolia, keep the
 * 0x prefix on the key). Doing them by hand is where deployments go wrong, so this does
 * them atomically and takes a timestamped backup of backend/.env first.
 *
 * Safe to re-run: it refuses to overwrite an existing TRUSTMESH_REGISTRY_ADDRESS unless
 * you pass --force.
 */

import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import dotenv from 'dotenv'
import { ethers } from 'ethers'

const HERE = dirname(fileURLToPath(import.meta.url))
const CONTRACTS = resolve(HERE, '..')
const REPO = resolve(CONTRACTS, '..')
const BACKEND_ENV = join(REPO, 'backend', '.env')
const CONTRACTS_ENV = join(CONTRACTS, '.env')

const GREEN = '\x1b[92m'
const RED = '\x1b[91m'
const DIM = '\x1b[2m'
const CYAN = '\x1b[96m'
const RESET = '\x1b[0m'
const FORCE = process.argv.includes('--force')

const env = dotenv.parse(readFileSync(CONTRACTS_ENV, 'utf8'))
const rpc = env.BLOCKCHAIN_RPC_URL
const key = env.BLOCKCHAIN_PRIVATE_KEY

function die(msg) {
  console.error(`${RED}${msg}${RESET}`)
  process.exit(1)
}

if (!rpc) die('BLOCKCHAIN_RPC_URL is not set in contracts/.env')
if (!key) die('BLOCKCHAIN_PRIVATE_KEY is not set in contracts/.env')

const provider = new ethers.JsonRpcProvider(rpc, {
  chainId: Number(env.BLOCKCHAIN_CHAIN_ID || 11155111),
  name: 'sepolia',
})
const wallet = new ethers.Wallet(key.startsWith('0x') ? key : `0x${key}`, provider)

console.log(`\n${CYAN}TrustMesh - Sepolia deployment${RESET}`)
console.log(`${DIM}deployer : ${wallet.address}${RESET}`)
console.log(`${DIM}network  : chainId ${(await provider.getNetwork()).chainId}${RESET}\n`)

const balance = await provider.getBalance(wallet.address)
console.log(`  balance  ${ethers.formatEther(balance)} ETH`)
if (balance === 0n) {
  die(
    `  The deployer has no Sepolia ETH.\n` +
      `  Fund ${wallet.address} from a faucet, then re-run:\n` +
      `    https://sepoliafaucet.com\n` +
      `    https://www.alchemy.com/faucets/sepolia\n` +
      `    https://console.cloud.google.com/blockchain/faucet`,
  )
}

if (existsSync(CONTRACTS_ENV)) {
  const existing = dotenv.parse(readFileSync(CONTRACTS_ENV, 'utf8')).TRUSTMESH_REGISTRY_ADDRESS
  if (existing && !FORCE) {
    die(
      `  TRUSTMESH_REGISTRY_ADDRESS is already set to ${existing}.\n` +
        `  Re-run with --force to deploy a new copy.`,
    )
  }
}

console.log(`\n${DIM}deploying...${RESET}`)
let out
try {
  out = execFileSync('npx', ['hardhat', 'run', 'scripts/deploy.js', '--network', 'sepolia'], {
    cwd: CONTRACTS,
    encoding: 'utf8',
  })
} catch (e) {
  process.stdout.write(out ?? '')
  die(`  Deployment failed:\n${(e.stderr || e.stdout || '').toString().slice(-600)}`)
}
process.stdout.write(out)

const m = out.match(/TRUSTMESH_CONTRACT_ADDRESS=(\S+)/) || out.match(/(0x[a-fA-F0-9]{40})/g)?.at(-1)
if (!m) die('  Could not parse the contract address from the deploy output.')
const address = m[1] ?? m[0]
console.log(`\n${GREEN}deployed${RESET} TrustMeshRegistry at ${address}`)

// ------------------------------------------------------------------ backend env
const backup = `${BACKEND_ENV}.bak-${new Date().toISOString().replace(/[:.]/g, '-')}`
copyFileSync(BACKEND_ENV, backup)
console.log(`${DIM}backend/.env backed up to ${backup.split(/[\\/]/).pop()}${RESET}`)

let be = readFileSync(BACKEND_ENV, 'utf8')
const setVar = (text, name, value) => {
  const re = new RegExp(`^${name}=.*$`, 'm')
  return re.test(text) ? text.replace(re, `${name}=${value}`) : `${text}\n${name}=${value}`
}

be = setVar(be, 'BLOCKCHAIN_RPC_URL', rpc)
be = setVar(be, 'BLOCKCHAIN_CONTRACT_ADDRESS', address)
be = setVar(be, 'BLOCKCHAIN_NETWORK', 'sepolia')
be = setVar(be, 'BLOCKCHAIN_CHAIN_ID', '11155111')
be = setVar(be, 'BLOCKCHAIN_MODE', 'auto')
be = setVar(be, 'BLOCKCHAIN_PRIVATE_KEY', wallet.privateKey)
writeFileSync(BACKEND_ENV, be)

let ce = readFileSync(CONTRACTS_ENV, 'utf8')
ce = setVar(ce, 'TRUSTMESH_REGISTRY_ADDRESS', address)
writeFileSync(CONTRACTS_ENV, ce)

const code = await provider.getCode(address)
console.log(`\n${GREEN}done${RESET}`)
console.log(`  contract code      : ${code === '0x' ? 'MISSING' : `${(code.length - 2) / 2} bytes`}`)
console.log(`  backend/.env       : now points at sepolia`)
console.log(`  contracts/.env     : TRUSTMESH_REGISTRY_ADDRESS set`)
console.log(`\n${DIM}restart the backend, then:${RESET}`)
console.log(`  curl http://127.0.0.1:8000/api/blockchain/status`)
console.log(`\n${DIM}local-chain tip: the previous settings are in the .bak file if you want them back.${RESET}\n`)