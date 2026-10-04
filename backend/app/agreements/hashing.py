"""RFC 8785 (JSON Canonicalization Scheme) + SHA-256.

Why this is hand-written rather than a dependency
------------------------------------------------
The canonical hash is the product's verifiability guarantee. Keeping the serializer
in-repo means it is auditable, dependency-free and testable against the RFC's own
vectors, and it removes a supply-chain dependency from the security-critical path.

What RFC 8785 requires
----------------------
1. Serialise with no insignificant whitespace.
2. Object members sorted by their UTF-16 code-unit sequence (NOT Python's default
   code-point ordering - these differ for characters outside the BMP).
3. Numbers serialised per ECMAScript ``Number::toString`` (shortest round-trip form).
4. Strings escaped with the minimal JSON escape set: ``"`` ``\\`` and C0 controls
   (``\\b \\f \\n \\r \\t`` or ``\\u00xx``). Forward slash is NOT escaped.
5. No ``NaN``/``Infinity``; lone surrogates are a serialisation error.

Referenced by: ``app.agreements.agreement_engine``, ``tests/test_hashing.py``,
``scripts/verify_system.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

JCS_VERSION = "RFC8785"
HASH_ALGORITHM = "SHA-256"

_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonicalised per RFC 8785."""


# --------------------------------------------------------------- key ordering
def _utf16_key(name: str) -> tuple[int, ...]:
    """Sort key = the name's UTF-16 code units, as required by RFC 8785 §3.2.3."""
    return tuple(name.encode("utf-16-be", errors="surrogatepass"))


# ------------------------------------------------------------------- strings
def _escape_string(value: str) -> str:
    out = ['"']
    for char in value:
        code = ord(char)
        short = _SHORT_ESCAPES.get(code)
        if short is not None:
            out.append(short)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


# -------------------------------------------------------------------- numbers
_EXP_RE = re.compile(r"^(-?)(\d)(?:\.(\d+))?e([+-])(\d+)$", re.I)


def _es_number(value: int | float) -> str:
    """ECMAScript ``Number::toString`` for the JSON number grammar of RFC 8785 §3.2.2.3."""
    if isinstance(value, bool):  # bool is an int subclass - never a number here
        raise CanonicalizationError("bool is not a JSON number")
    if isinstance(value, int):
        return str(value)

    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError("NaN and Infinity are not valid JSON numbers")
    if value == 0:
        return "0"  # normalises -0.0 to 0 per ECMAScript
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))

    text = repr(float(value))
    if "e" in text or "E" in text:
        mantissa, _, exponent_text = text.replace("E", "e").partition("e")
        exponent = int(exponent_text)
    else:
        mantissa, exponent = text, 0

    sign = ""
    if mantissa.startswith("-"):
        sign, mantissa = "-", mantissa[1:]

    int_part, _, frac_part = mantissa.partition(".")
    digits = (int_part + frac_part).lstrip("0")
    if not digits:
        return "0"
    leading_zeros = len(int_part + frac_part) - len((int_part + frac_part).lstrip("0"))
    # value == 0.<digits> * 10 ** point
    point = len(int_part) - leading_zeros + exponent

    if digits.endswith("0"):
        digits = digits.rstrip("0")

    k = len(digits)
    n = point
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    exp = n - 1
    exp_sign = "+" if exp >= 0 else "-"
    if k == 1:
        return f"{sign}{digits}e{exp_sign}{abs(exp)}"
    return f"{sign}{digits[0]}.{digits[1:]}e{exp_sign}{abs(exp)}"


# ----------------------------------------------------------------- serialiser
def _serialise(value: Any, depth: int = 0) -> str:
    if depth > 64:
        raise CanonicalizationError("value nested too deeply for canonicalisation")

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _es_number(value)
    if isinstance(value, str):
        if _has_lone_surrogate(value):
            raise CanonicalizationError("string value contains a lone surrogate")
        return _escape_string(value)
    if isinstance(value, (bytes, bytearray)):
        raise CanonicalizationError("bytes cannot be canonicalised; hex-encode first")
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialise(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        members: list[tuple[tuple[int, ...], str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"object keys must be strings, got {type(key).__name__}")
            if _has_lone_surrogate(key):
                raise CanonicalizationError("object key contains a lone surrogate")
            members.append((_utf16_key(key), key, item))
        members.sort(key=lambda entry: entry[0])
        return (
            "{"
            + ",".join(f"{_escape_string(key)}:{_serialise(item, depth + 1)}" for _, key, item in members)
            + "}"
        )

    # Last resort: dataclasses / pydantic models / named tuples.
    if hasattr(value, "model_dump"):
        return _serialise(value.model_dump(exclude_none=True), depth + 1)
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses

        return _serialise(dataclasses.asdict(value), depth + 1)
    if hasattr(value, "_asdict"):
        return _serialise(value._asdict(), depth + 1)

    raise CanonicalizationError(f"unsupported type for canonicalisation: {type(value).__name__}")


def _has_lone_surrogate(text: str) -> bool:
    """RFC 8785 forbids lone surrogates: they cannot be encoded as UTF-8."""
    for index, char in enumerate(text):
        if 0xD800 <= ord(char) <= 0xDBFF:
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if not nxt or not (0xDC00 <= ord(nxt) <= 0xDFFF):
                return True
        elif 0xDC00 <= ord(char) <= 0xDFFF:
            return True
    return False


def canonicalize(payload: Any) -> str:
    """Return the RFC 8785 canonical JSON string for ``payload``."""
    return _serialise(payload)


def canonical_bytes(payload: Any) -> bytes:
    """Canonical JSON encoded as UTF-8, which is what gets hashed."""
    return canonicalize(payload).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_prefixed(data: bytes) -> str:
    """``0x``-prefixed digest, the form the UI and the contract both display."""
    return "0x" + hashlib.sha256(data).hexdigest()


def hash_agreement(payload: Any) -> tuple[str, str, bytes]:
    """Canonicalise then hash. Returns ``(canonical_json, hex_digest, raw_bytes)``."""
    text = canonicalize(payload)
    raw = text.encode("utf-8")
    return text, sha256_hex(raw), raw


def agreement_digest(payload: Any) -> str:
    """``0x``-prefixed SHA-256 of the canonical agreement."""
    return sha256_prefixed(canonical_bytes(payload))


def verify_hash(payload: Any, expected_hash: str) -> bool:
    """Constant-shape comparison used by the verification endpoint."""
    computed = agreement_digest(payload)
    return computed.lower() == str(expected_hash).lower()


def rfc8785_test_vectors() -> list[dict[str, Any]]:
    """Self-test used by ``scripts/verify_system.py`` and the test suite."""
    cases: list[dict[str, Any]] = [
        {
            "name": "member ordering",
            "input": {"b": 1, "a": 2, "A": 3, "": 4},
            "expected": '{"":4,"A":3,"a":2,"b":1}',
        },
        {
            "name": "no insignificant whitespace",
            "input": {"x": [1, 2, {"y": None}], "z": True},
            "expected": '{"x":[1,2,{"y":null}],"z":true}',
        },
        {
            "name": "minimal escaping, slash not escaped",
            "input": {"k": 'a"b\\c/d\ne\tf\u0001g'},
            "expected": '{"k":"a\\"b\\\\c/d\\ne\\tf\\u0001g"}',
        },
        {
            "name": "ECMAScript integer form",
            "input": [1, 1.0, 100.0, 0.0, -0.0, 20.5],
            "expected": "[1,1,100,0,0,20.5]",
        },
        {
            "name": "ECMAScript exponent form",
            "input": [1e21, 1e-7, 1.5e-8, 333333333.33333329],
            "expected": "[1e+21,1e-7,1.5e-8,333333333.3333333]",
        },
    ]
    results = []
    for case in cases:
        produced = canonicalize(case["input"])
        results.append(
            {
                "name": case["name"],
                "input": case["input"],
                "expected": case["expected"],
                "actual": produced,
                "passed": produced == case["expected"],
            }
        )
    return results


def selftest() -> bool:
    """True when every RFC 8785 vector reproduces exactly."""
    return all(case["passed"] for case in rfc8785_test_vectors())


def describe() -> dict[str, Any]:
    return {
        "canonicalization": JCS_VERSION,
        "algorithm": HASH_ALGORITHM,
        "implementation": "app.agreements.hashing (in-repo, dependency-free)",
        "key_order": "UTF-16 code units",
        "number_format": "ECMAScript Number::toString",
        "string_escaping": "minimal JSON escapes, '/' unescaped",
        "sample": json.dumps(
            {"algorithm": HASH_ALGORITHM, "canonicalization": JCS_VERSION}, separators=(",", ":")
        ),
    }
