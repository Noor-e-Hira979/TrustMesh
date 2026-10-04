/**
 * TrustMeshRegistry test suite.
 *
 * Covers the guarantees the product actually promises:
 *   - a commitment can be recorded and read back
 *   - the stored digest is exactly the value that was submitted (immutability)
 *   - a transaction reference can be committed exactly once (append-only)
 *   - the event is emitted with on-chain timestamp and block
 *   - the contract never accepts value and stores no confidential data
 *   - the backend ABI contract (function names, argument order, return shape) holds
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");
const crypto = require("node:crypto");

const ZERO = "0x" + "00".repeat(32);

describe("TrustMeshRegistry", function () {
  let registry;
  let deployer;
  let other;

  /** Deterministic, realistic fixtures. */
  const fixtures = {
    agreementHash: "0x" + crypto.createHash("sha256").update("trustmesh.agreement.v1").digest("hex"),
    transactionRef: "0x" + crypto.createHash("sha256").update("TM_demo_001").digest("hex"),
    buyerAgentRef: "0x" + crypto.createHash("sha256").update("buyer_agent_1").digest("hex"),
    supplierAgentRef: "0x" + crypto.createHash("sha256").update("supplier_agent_b").digest("hex"),
  };

  beforeEach(async function () {
    [deployer, other] = await ethers.getSigners();
    const factory = await ethers.getContractFactory("TrustMeshRegistry");
    registry = await factory.deploy();
    await registry.waitForDeployment();
  });

  describe("deployment", function () {
    it("starts with zero commitments", async function () {
      expect(await registry.commitmentCount()).to.equal(0n);
    });

    it("declares its hash scheme", async function () {
      expect(await registry.agreementHashScheme()).to.equal(
        "SHA-256(RFC8785-canonical-JSON)",
      );
    });

    it("declares a version", async function () {
      expect(await registry.registryVersion()).to.equal("trustmesh.registry.v1");
    });

    it("declares the fields it never stores", async function () {
      const fields = await registry.neverStoredOnChain();
      expect(fields).to.include.members([
        "credentials",
        "api_keys",
        "prices",
        "quantities",
        "negotiation_transcripts",
        "organization_legal_names",
      ]);
    });

    it("exposes the exact functions the backend ABI depends on", async function () {
      for (const fn of [
        "commitAgreement",
        "commitmentCount",
        "commitments",
        "getCommitment",
        "getCommitmentByIndex",
        "existsByTransactionRef",
      ]) {
        expect(await registry.getFunction(fn)).to.not.equal(undefined, fn);
      }
    });
  });

  describe("commitAgreement", function () {
    it("records a commitment and increments the count", async function () {
      const before = await registry.commitmentCount();
      await registry
        .connect(deployer)
        .commitAgreement(
          fixtures.agreementHash,
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        );
      expect(await registry.commitmentCount()).to.equal(before + 1n);
    });

    it("stores the submitted digest byte-for-byte", async function () {
      await registry.commitAgreement(
        fixtures.agreementHash,
        fixtures.transactionRef,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );
      const [record, exists] = await registry.getCommitment(fixtures.transactionRef);
      expect(exists).to.equal(true);
      expect(record.agreementHash).to.equal(fixtures.agreementHash);
      expect(record.transactionRef).to.equal(fixtures.transactionRef);
      expect(record.buyerAgentRef).to.equal(fixtures.buyerAgentRef);
      expect(record.supplierAgentRef).to.equal(fixtures.supplierAgentRef);
      expect(record.registrant).to.equal(deployer.address);
    });

    it("records the on-chain timestamp and block", async function () {
      const blockBefore = await ethers.provider.getBlock("latest");
      await registry.commitAgreement(
        fixtures.agreementHash,
        fixtures.transactionRef,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );
      const [record] = await registry.getCommitment(fixtures.transactionRef);
      expect(record.commitBlock).to.be.greaterThan(blockBefore.number);
      expect(record.committedAt).to.be.greaterThan(0n);
      expect(record.committedAt).to.be.lessThanOrEqual(
        BigInt(Math.floor(Date.now() / 1000) + 120),
      );
    });

    it("emits AgreementCommitted with the same digest", async function () {
      await expect(
        registry
          .connect(deployer)
          .commitAgreement(
            fixtures.agreementHash,
            fixtures.transactionRef,
            fixtures.buyerAgentRef,
            fixtures.supplierAgentRef,
          ),
      )
        .to.emit(registry, "AgreementCommitted")
        .withArgs(
          fixtures.transactionRef,
          deployer.address,
          fixtures.agreementHash,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
          anyUint64(),
          anyUint(),
        );
    });

    it("attributes the commitment to the submitting address", async function () {
      await registry
        .connect(other)
        .commitAgreement(
          fixtures.agreementHash,
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        );
      const [record] = await registry.getCommitment(fixtures.transactionRef);
      expect(record.registrant).to.equal(other.address);
    });

    it("accepts commitments from any backend service address", async function () {
      await registry
        .connect(other)
        .commitAgreement(
          fixtures.agreementHash,
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        );
      expect(await registry.existsByTransactionRef(fixtures.transactionRef)).to.equal(true);
    });
  });

  describe("input validation", function () {
    it("rejects a zero agreement hash", async function () {
      await expect(
        registry.commitAgreement(
          ZERO,
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        ),
      ).to.be.revertedWithCustomError(registry, "InvalidAgreementHash");
    });

    it("rejects a zero transaction reference", async function () {
      await expect(
        registry.commitAgreement(
          fixtures.agreementHash,
          ZERO,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        ),
      ).to.be.revertedWithCustomError(registry, "InvalidTransactionRef");
    });

    it("rejects a zero buyer reference", async function () {
      await expect(
        registry.commitAgreement(
          fixtures.agreementHash,
          fixtures.transactionRef,
          ZERO,
          fixtures.supplierAgentRef,
        ),
      ).to.be.revertedWithCustomError(registry, "InvalidAgentRef");
    });

    it("rejects a zero supplier reference", async function () {
      await expect(
        registry.commitAgreement(
          fixtures.agreementHash,
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          ZERO,
        ),
      ).to.be.revertedWithCustomError(registry, "InvalidAgentRef");
    });
  });

  describe("immutability (append-only)", function () {
    beforeEach(async function () {
      await registry.commitAgreement(
        fixtures.agreementHash,
        fixtures.transactionRef,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );
    });

    it("refuses to overwrite an existing transaction reference", async function () {
      await expect(
        registry.commitAgreement(
          "0x" + "ab".repeat(32),
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        ),
      ).to.be.revertedWithCustomError(registry, "CommitmentAlreadyExists");
    });

    it("leaves the original digest untouched after a rejected overwrite", async function () {
      await expect(
        registry.commitAgreement(
          "0x" + "ab".repeat(32),
          fixtures.transactionRef,
          fixtures.buyerAgentRef,
          fixtures.supplierAgentRef,
        ),
      ).to.be.reverted;
      const [record] = await registry.getCommitment(fixtures.transactionRef);
      expect(record.agreementHash).to.equal(fixtures.agreementHash);
      expect(await registry.commitmentCount()).to.equal(1n);
    });

    it("has no function that mutates or deletes a commitment", async function () {
      const fragmentNames = registry.interface.fragments
        .filter((f) => f.type === "function")
        .map((f) => f.name);
      for (const forbidden of [
        "updateCommitment",
        "deleteCommitment",
        "removeCommitment",
        "setCommitment",
        "revokeCommitment",
        "transferOwnership",
      ]) {
        expect(fragmentNames).to.not.include(forbidden);
      }
    });
  });

  describe("verification reads", function () {
    it("existsByTransactionRef reflects recorded state", async function () {
      expect(await registry.existsByTransactionRef(fixtures.transactionRef)).to.equal(false);
      await registry.commitAgreement(
        fixtures.agreementHash,
        fixtures.transactionRef,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );
      expect(await registry.existsByTransactionRef(fixtures.transactionRef)).to.equal(true);
    });

    it("returns exists=false for an unknown reference", async function () {
      const [, exists] = await registry.getCommitment(ZERO);
      expect(exists).to.equal(false);
    });

    it("reads back by index in submission order", async function () {
      for (let i = 0; i < 3; i += 1) {
        const ref = "0x" + crypto.createHash("sha256").update(`TM_${i}`).digest("hex");
        const hash = "0x" + crypto.createHash("sha256").update(`agree_${i}`).digest("hex");
        await registry.commitAgreement(hash, ref, fixtures.buyerAgentRef, fixtures.supplierAgentRef);
      }
      expect(await registry.commitmentCount()).to.equal(3n);
      for (let i = 0; i < 3; i += 1) {
        const expected = "0x" + crypto.createHash("sha256").update(`agree_${i}`).digest("hex");
        const byIndex = await registry.getCommitmentByIndex(i);
        const viaAccessor = await registry.commitments(i);
        expect(byIndex.agreementHash).to.equal(expected);
        expect(viaAccessor.agreementHash).to.equal(expected);
      }
    });

    it("reverts for an out-of-range index", async function () {
      await expect(registry.getCommitmentByIndex(0)).to.be.revertedWithCustomError(
        registry,
        "CommitmentNotFound",
      );
    });

    it("reverts from the index accessor for an out-of-range index", async function () {
      await expect(registry.commitments(99)).to.be.revertedWithCustomError(
        registry,
        "CommitmentNotFound",
      );
    });
  });

  describe("data minimisation", function () {
    it("has no function that accepts value", async function () {
      const payableFragments = registry.interface.fragments.filter(
        (f) => f.type === "function" && f.stateMutability === "payable",
      );
      expect(payableFragments).to.have.lengthOf(0);
    });

    it("has no ERC20, ERC721 or ownership surface", async function () {
      const names = registry.interface.fragments
        .filter((f) => f.type === "function")
        .map((f) => f.name);
      for (const forbidden of [
        "transfer",
        "transferFrom",
        "approve",
        "mint",
        "burn",
        "owner",
        "renounceOwnership",
        "propose",
        "vote",
        "execute",
      ]) {
        expect(names).to.not.include(forbidden);
      }
    });

    it("accepts only hashes and addresses as commitment inputs", async function () {
      const fragment = registry.interface.getFunction("commitAgreement");
      const argTypes = fragment.inputs.map((i) => i.type);
      expect(argTypes).to.deep.equal(["bytes32", "bytes32", "bytes32", "bytes32"]);
    });

    it("holds no ETH after any interaction", async function () {
      await registry.commitAgreement(
        fixtures.agreementHash,
        fixtures.transactionRef,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );
      expect(await ethers.provider.getBalance(await registry.getAddress())).to.equal(0n);
    });
  });

  describe("independently reproducible digest", function () {
    it("stores the same digest a verifier computes from the canonical agreement", async function () {
      // Mirrors app/agreements/hashing.py: SHA-256 over RFC 8785 canonical JSON bytes.
      const canonical =
        '{"buyer":{"agent_id":"buyer_agent_1","organization_id":"org_buyer"},' +
        '"product":"Packaging units","quantity":1000}';
      const digest = "0x" + crypto.createHash("sha256").update(canonical, "utf8").digest("hex");
      const ref = "0x" + crypto.createHash("sha256").update("TM_verify").digest("hex");

      await registry.commitAgreement(
        digest,
        ref,
        fixtures.buyerAgentRef,
        fixtures.supplierAgentRef,
      );

      const [record, exists] = await registry.getCommitment(ref);
      expect(exists).to.equal(true);
      expect(record.agreementHash).to.equal(digest);

      // The log index 0 holds the same value.
      expect((await registry.getCommitmentByIndex(0)).agreementHash).to.equal(digest);
    });
  });
});

/** Wildcard matcher for the event's uint64 timestamp argument. */
function anyUint64() {
  const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
  return anyValue;
}

/** Wildcard matcher for the event's uint256 block argument. */
function anyUint() {
  const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
  return anyValue;
}
