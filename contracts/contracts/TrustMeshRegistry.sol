// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title  TrustMeshRegistry
 * @notice Tamper-evident commitment layer for TrustMesh.
 *
 *         TrustMesh performs real-time enforcement; this contract is the public,
 *         verifiable witness that a specific agreement existed in a specific form.
 *
 * @dev    DESIGN CONSTRAINTS (deliberate, per specification sections 12 and 33)
 *
 *         1. MINIMAL PAYLOAD ONLY. The contract stores the SHA-256 digest of the
 *            RFC 8785 canonical agreement plus pseudonymous references. It never
 *            stores prices, quantities, product names, credentials, API keys,
 *            negotiation transcripts or any confidential business data.
 *         2. NO VALUE. The contract accepts no ETH and holds no balance. It has no
 *            token, no NFT, no payment and no DAO mechanism.
 *         3. APPEND-ONLY. A transaction reference can be committed exactly once.
 *            The mapping is never mutated and no commitment is ever deleted.
 *         4. NOT A GATEWAY. Nothing here grants, revokes or evaluates authority.
 *            Trust enforcement happens off-chain and is unaffected by this contract.
 *
 *         Verification flow:
 *             off-chain agreement -> RFC 8785 canonical JSON -> SHA-256 -> commit
 *             -> later: recompute the digest and compare against the stored value.
 */
contract TrustMeshRegistry {
    // ------------------------------------------------------------------ types

    /// @notice One immutable agreement commitment.
    /// @param agreementHash  SHA-256 (32 bytes) of the RFC 8785 canonical agreement.
    /// @param transactionRef  32-byte reference for the TrustMesh transaction id.
    /// @param buyerAgentRef   32-byte pseudonymous reference for the buyer agent.
    /// @param supplierAgentRef 32-byte pseudonymous reference for the supplier agent.
    /// @param committedAt     Unix timestamp taken from the block that recorded it.
    /// @param commitBlock     Block number that recorded the commitment.
    /// @param registrant      Address that submitted the commitment.
    struct Commitment {
        bytes32 agreementHash;
        bytes32 transactionRef;
        bytes32 buyerAgentRef;
        bytes32 supplierAgentRef;
        uint64 committedAt;
        uint256 commitBlock;
        address registrant;
    }

    // ----------------------------------------------------------------- errors

    /// @notice The supplied agreement hash is the zero hash.
    error InvalidAgreementHash();
    /// @notice The supplied transaction reference is the zero hash.
    error InvalidTransactionRef();
    /// @notice The buyer or supplier agent reference is the zero hash.
    error InvalidAgentRef();
    /// @notice This transaction reference already has a commitment. Commitments are final.
    error CommitmentAlreadyExists();
    /// @notice The requested index or reference does not exist.
    error CommitmentNotFound();

    // ---------------------------------------------------------------- storage

    /// @notice Commitment count, also the next index in `commitments`.
    uint256 private _commitmentCount;

    /// @dev transactionRef => Commitment. Written once, never updated, never deleted.
    mapping(bytes32 => Commitment) private _byTransactionRef;

    /// @notice Append-only log of every commitment, in submission order.
    Commitment[] private _commitments;

    // ----------------------------------------------------------------- events

    /**
     * @notice Emitted for every recorded commitment.
     * @dev The event carries only hashes and pseudonymous references. `committedAt`
     *      and `commitBlock` come from `block.timestamp` and `block.number`, so the
     *      chain itself is the timestamp authority.
     */
    event AgreementCommitted(
        bytes32 indexed transactionRef,
        address indexed registrant,
        bytes32 agreementHash,
        bytes32 buyerAgentRef,
        bytes32 supplierAgentRef,
        uint64 committedAt,
        uint256 commitBlock
    );

    // --------------------------------------------------------------- functions

    /**
     * @notice Record an agreement commitment.
     * @param agreementHash    SHA-256 of the RFC 8785 canonical agreement.
     * @param transactionRef   32-byte reference for the TrustMesh transaction id.
     * @param buyerAgentRef    32-byte pseudonymous buyer agent reference.
     * @param supplierAgentRef 32-byte pseudonymous supplier agent reference.
     *
     * @dev Non-payable on purpose: this contract never handles value.
     *      Reverts if the transaction reference was already committed, which makes the
     *      registry append-only and the record final.
     */
    function commitAgreement(
        bytes32 agreementHash,
        bytes32 transactionRef,
        bytes32 buyerAgentRef,
        bytes32 supplierAgentRef
    ) external {
        if (agreementHash == bytes32(0)) revert InvalidAgreementHash();
        if (transactionRef == bytes32(0)) revert InvalidTransactionRef();
        if (buyerAgentRef == bytes32(0) || supplierAgentRef == bytes32(0)) {
            revert InvalidAgentRef();
        }
        if (_byTransactionRef[transactionRef].agreementHash != bytes32(0)) {
            revert CommitmentAlreadyExists();
        }

        Commitment memory record = Commitment({
            agreementHash: agreementHash,
            transactionRef: transactionRef,
            buyerAgentRef: buyerAgentRef,
            supplierAgentRef: supplierAgentRef,
            committedAt: uint64(block.timestamp),
            commitBlock: block.number,
            registrant: msg.sender
        });

        _byTransactionRef[transactionRef] = record;
        _commitments.push(record);
        _commitmentCount += 1;

        emit AgreementCommitted(
            transactionRef,
            msg.sender,
            agreementHash,
            buyerAgentRef,
            supplierAgentRef,
            record.committedAt,
            record.commitBlock
        );
    }

    /**
     * @notice Read a commitment back by its transaction reference.
     * @param transactionRef 32-byte reference for the TrustMesh transaction id.
     * @return record        The stored commitment.
     * @return exists        True when a commitment exists for that reference.
     */
    function getCommitment(bytes32 transactionRef)
        external
        view
        returns (Commitment memory record, bool exists)
    {
        record = _byTransactionRef[transactionRef];
        exists = record.agreementHash != bytes32(0);
    }

    /**
     * @notice Read a commitment by its index in the append-only log.
     * @param index Position in the log, starting at zero.
     */
    function getCommitmentByIndex(uint256 index) external view returns (Commitment memory) {
        if (index >= _commitmentCount) revert CommitmentNotFound();
        return _commitments[index];
    }

    /**
     * @notice True when a transaction reference has already been committed.
     * @param transactionRef 32-byte reference for the TrustMesh transaction id.
     */
    function existsByTransactionRef(bytes32 transactionRef) external view returns (bool) {
        return _byTransactionRef[transactionRef].agreementHash != bytes32(0);
    }

    /**
     * @notice Total number of commitments recorded.
     *
     * @dev Named `commitmentCount()` to match the backend ABI in
     *      `backend/app/blockchain/contract.py`.
     */
    function commitmentCount() external view returns (uint256) {
        return _commitmentCount;
    }

    /**
     * @notice Index-based accessor matching the backend ABI's `commitments(uint256)`.
     * @param index Position in the append-only log, starting at zero.
     */
    function commitments(uint256 index) external view returns (Commitment memory) {
        if (index >= _commitmentCount) revert CommitmentNotFound();
        return _commitments[index];
    }

    /**
     * @notice Hashing scheme used for the stored digests.
     * @dev Returned verbatim in the API so a verifier always knows how to reproduce
     *      the digest: SHA-256 over the RFC 8785 canonical JSON bytes.
     */
    function agreementHashScheme() external pure returns (string memory) {
        return "SHA-256(RFC8785-canonical-JSON)";
    }

    /**
     * @notice Contract version, useful for pinning a verifier to a known schema.
     */
    function registryVersion() external pure returns (string memory) {
        return "trustmesh.registry.v1";
    }

    /**
     * @notice Fields this registry deliberately never stores.
     * @dev Returned verbatim in the API so the data-minimisation guarantee is
     *      machine-checkable rather than a documentation promise.
     */
    function neverStoredOnChain() external pure returns (string[] memory) {
        string[] memory fields = new string[](6);
        fields[0] = "credentials";
        fields[1] = "api_keys";
        fields[2] = "prices";
        fields[3] = "quantities";
        fields[4] = "negotiation_transcripts";
        fields[5] = "organization_legal_names";
        return fields;
    }
}
