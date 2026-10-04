"""RFC 8785 canonicalisation + SHA-256 tests.

The agreement hash is the product's verifiability guarantee, so the canonicaliser is
tested against the RFC's own requirements rather than only against itself.

Referenced by: ``app.agreements.hashing``, ``app.agreements.agreement_engine``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.agreements.agreement_engine import (
    agent_reference,
    transaction_reference,
    verify_artifact,
)
from app.agreements.hashing import (
    CanonicalizationError,
    agreement_digest,
    canonical_bytes,
    canonicalize,
    hash_agreement,
    rfc8785_test_vectors,
    selftest,
    sha256_prefixed,
)


def test_all_rfc_vectors_pass():
    failures = [v for v in rfc8785_test_vectors() if not v["passed"]]
    assert failures == [], failures
    assert selftest() is True


def test_no_insignificant_whitespace():
    assert canonicalize({"b": 1, "a": [1, 2, {"c": None}]}) == '{"a":[1,2,{"c":null}],"b":1}'


def test_keys_sorted_by_utf16_code_units():
    # U+10000 encodes as the surrogate pair D800 DC00, so its first UTF-16 code unit
    # (0xD800) sorts BELOW U+FF01 (0xFF01). Code-point ordering would put U+FF01 first,
    # which is why RFC 8785 specifies UTF-16 code-unit ordering.
    payload = {"\uff01": 1, "\U00010000": 2, "z": 3}
    out = canonicalize(payload)
    # z(007A) < U+10000(D800 DC00) < U+FF01(FF01) in UTF-16 code-unit order.
    # Code-point order would place U+FF01 before U+10000.
    assert out.index('"z"') < out.index("\U00010000") < out.index("\uff01")
    assert json.loads(out) == payload


def test_keys_sorted_within_the_bmp():
    payload = {"é": 1, "︳": 2, "z": 3}
    out = canonicalize(payload)
    assert out.index('"z"') < out.index("é") < out.index("︳")


def test_astral_plane_string_value_round_trips():
    payload = {"note": "shipment \U0001F4E6 ready"}
    assert json.loads(canonicalize(payload)) == payload


def test_forward_slash_is_not_escaped():
    assert canonicalize({"u": "https://example.test/a"}) == '{"u":"https://example.test/a"}'


def test_minimal_control_character_escapes():
    assert canonicalize("a\u0000b\u001fc\nd\te") == '"a\\u0000b\\u001fc\\nd\\te"'
    assert canonicalize("\b\f\r\"\\") == '"\\b\\f\\r\\"\\\\"'


def test_ecmascript_integer_forms():
    assert canonicalize([1, 1.0, 100.0, 0.0, -0.0, 20.5]) == "[1,1,100,0,0,20.5]"


def test_ecmascript_exponent_forms():
    assert canonicalize([1e21, 1e-7, 1.5e-8]) == "[1e+21,1e-7,1.5e-8]"


def test_large_integer_precision():
    assert canonicalize(9007199254740993) == "9007199254740993"


def test_booleans_are_not_numbers():
    assert canonicalize({"t": True, "f": False}) == '{"f":false,"t":true}'


def test_none_becomes_null():
    assert canonicalize(None) == "null"


def test_nan_and_infinity_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize(float("nan"))
    with pytest.raises(CanonicalizationError):
        canonicalize(float("inf"))


def test_bytes_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize(b"raw")


def test_non_string_keys_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize({1: "a"})


def test_lone_surrogate_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize("\ud800")


def test_canonical_output_is_valid_json():
    payload = {"z": [1, {"a": None}], "a": "x", "m": True}
    assert json.loads(canonicalize(payload)) == payload


def test_canonicalisation_is_idempotent():
    payload = {"b": 1, "a": {"d": 2, "c": [3, 4]}}
    once = canonicalize(payload)
    assert canonicalize(json.loads(once)) == once


def test_canonicalization_is_order_independent():
    a = canonicalize({"a": 1, "b": 2, "c": 3})
    b = canonicalize({"c": 3, "b": 2, "a": 1})
    assert a == b


def test_digest_is_sha256_of_canonical_bytes():
    payload = {"hello": "world"}
    canonical, digest_hex, raw = hash_agreement(payload)
    assert digest_hex == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert raw == canonical.encode("utf-8")
    assert agreement_digest(payload) == "0x" + digest_hex


def test_digest_is_stable_across_key_insertion_order():
    first = agreement_digest({"a": 1, "b": {"c": 2, "d": 3}})
    second = agreement_digest({"b": {"d": 3, "c": 2}, "a": 1})
    assert first == second


def test_verify_artifact_recomputes_from_stored_bytes():
    payload = {"transaction_id": "TM_1", "terms": {"price_cents": 195000}}
    canonical, digest_hex, _ = hash_agreement(payload)
    expected = "0x" + digest_hex
    ok, recomputed = verify_artifact(canonical, expected)
    assert ok is True
    assert recomputed == expected
    assert sha256_prefixed(canonical.encode("utf-8")) == expected


def test_verify_artifact_detects_tampering():
    canonical, digest_hex, _ = hash_agreement({"price_cents": 195000})
    tampered = canonical.replace("195000", "999999")
    ok, _ = verify_artifact(tampered, "0x" + digest_hex)
    assert ok is False


def test_pseudonymous_references_are_stable_and_opaque():
    ref = agent_reference("supplier_agent_b")
    assert ref == agent_reference("supplier_agent_b")
    assert ref.startswith("0x") and len(ref) == 66
    assert "supplier_agent_b" not in ref
    txn_ref = transaction_reference("TM_abc123")
    assert txn_ref.startswith("0x") and len(txn_ref) == 66
    assert txn_ref != ref


def test_canonical_bytes_are_utf8():
    payload = {"product": "Packaging units – café"}
    assert canonical_bytes(payload).decode("utf-8")
    assert "café" in canonicalize(payload)
