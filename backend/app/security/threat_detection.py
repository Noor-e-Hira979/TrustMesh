"""Threat Interception.

Stage 5 of the enforcement pipeline. Deterministic, auditable rules - no ML, no
opaque scoring (spec §33). Each rule is a named, documented detector so a judge can
read exactly why something was blocked.

The rules operate on the *structure* of a request (capability, action name,
parameters) and on a small set of textual signals. They are simulations of threat
detection; nothing here executes, exploits or reaches outside the process.

Referenced by: ``app.trust.trust_engine``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# Signals that appear in a *simulated* malicious request. These strings are data
# fixtures for detection, not attack tooling.
_CREDENTIAL_SOLICITATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(api[_ -]?key|secret[_ -]?key|access[_ -]?token|bearer token)\b", re.I),
    re.compile(r"\b(credentials?|password|passphrase|private key)\b", re.I),
    re.compile(r"\b(buyer|client|customer)[_ -]?(api|account|login)\b", re.I),
    re.compile(r"\b(env|environment)\s*(vars|variables|file)\b", re.I),
)

_OVERRIDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(ignore|bypass|override|disable)\b.{0,24}\b(polic|rule|guard|check)", re.I),
    re.compile(r"\b(you are|act as) (now )?(an? )?(admin|root|operator|developer)\b", re.I),
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\bwithout (asking|checking|approval|authorization)\b", re.I),
)

_EXFIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(all|entire|full|complete)\b.{0,20}\b(customer|client|user|employee)\b", re.I),
    re.compile(r"\b(dump|export|exfiltrate|leak)\b", re.I),
    re.compile(r"\bother (company|organisation|organization)'?s? (data|records)\b", re.I),
)

_TAMPER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(raise|increase|adjust) the (budget|limit|ceiling)\b", re.I),
    re.compile(r"\b(extend|waive|ignore) the (delivery|sla|deadline)\b", re.I),
    re.compile(r"\bmark .{0,20}certification as (valid|approved)\b", re.I),
)

_IMPERSONATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(i am|this is|representing|speaking for)\b.{0,30}\b(another|their|supplier \w+)", re.I),
    re.compile(r"\bact on behalf of\b", re.I),
    re.compile(r"\buse (their|his|her) (identity|credential|key)\b", re.I),
)


@dataclass(slots=True)
class ThreatSignal:
    code: str
    severity: str
    description: str
    matched: str


@dataclass(slots=True)
class ThreatResult:
    malicious: bool
    rule_id: str
    reason: str
    signals: list[ThreatSignal] = field(default_factory=list)

    def as_evidence(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "signals": [
                {
                    "code": s.code,
                    "severity": s.severity,
                    "description": s.description,
                    "matched": s.matched,
                }
                for s in self.signals
            ],
        }


@dataclass(frozen=True, slots=True)
class ThreatRule:
    code: str
    severity: str
    description: str
    matcher: Callable[[str], re.Match[str] | None]


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> re.Match[str] | None:
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found
    return None


RULES: tuple[ThreatRule, ...] = (
    ThreatRule(
        code="CREDENTIAL_SOLICITATION",
        severity="CRITICAL",
        description="Request attempts to obtain secret material belonging to another party.",
        matcher=lambda text: _first_match(text, _CREDENTIAL_SOLICITATION_PATTERNS),
    ),
    ThreatRule(
        code="INSTRUCTION_OVERRIDE_ATTEMPT",
        severity="HIGH",
        description="Request attempts to suppress or bypass the TrustMesh enforcement layer.",
        matcher=lambda text: _first_match(text, _OVERRIDE_PATTERNS),
    ),
    ThreatRule(
        code="CROSS_TENANT_DATA_REQUEST",
        severity="HIGH",
        description="Request attempts to read data belonging to a different organization.",
        matcher=lambda text: _first_match(text, _EXFIL_PATTERNS),
    ),
    ThreatRule(
        code="CONSTRAINT_TAMPERING",
        severity="HIGH",
        description="Request attempts to alter the authority constraints themselves.",
        matcher=lambda text: _first_match(text, _TAMPER_PATTERNS),
    ),
    ThreatRule(
        code="IDENTITY_IMPERSONATION",
        severity="CRITICAL",
        description="Request claims an identity other than the verified one.",
        matcher=lambda text: _first_match(text, _IMPERSONATION_PATTERNS),
    ),
)

_RULES_BY_CODE: dict[str, ThreatRule] = {rule.code: rule for rule in RULES}

# Capabilities whose mere presence implies threat, independent of wording.
THREAT_CAPABILITIES: dict[str, tuple[str, str]] = {
    "buyer_api_credentials": (
        "CREDENTIAL_SOLICITATION",
        "CRITICAL",
    ),
    "access_unrelated_company_data": (
        "CROSS_TENANT_DATA_REQUEST",
        "HIGH",
    ),
    "impersonate_organization": (
        "IDENTITY_IMPERSONATION",
        "CRITICAL",
    ),
    "exceed_authorized_budget": (
        "CONSTRAINT_TAMPERING",
        "HIGH",
    ),
    "unauthorized_payment": (
        "CONSTRAINT_TAMPERING",
        "HIGH",
    ),
}


def _flatten_text(action: str, parameters: dict[str, object] | None) -> str:
    parts: list[str] = [action or ""]
    for key, value in (parameters or {}).items():
        parts.append(str(key))
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(f"{k} {v}" for k, v in value.items())
    return " ".join(parts)


def evaluate(
    *, capability: str, action: str, parameters: dict[str, object] | None = None
) -> ThreatResult:
    """Screen one action request. Pure function - no I/O, no side effects."""
    signals: list[ThreatSignal] = []

    cap_threat = THREAT_CAPABILITIES.get(capability)
    if cap_threat:
        code, severity = cap_threat
        rule = _RULES_BY_CODE[code]
        signals.append(
            ThreatSignal(
                code=code,
                severity=severity,
                description=rule.description,
                matched=f"capability={capability}",
            )
        )

    text = _flatten_text(action, parameters)
    for rule in RULES:
        if any(s.code == rule.code for s in signals):
            continue
        found = rule.matcher(text)
        if found:
            signals.append(
                ThreatSignal(
                    code=rule.code,
                    severity=rule.severity,
                    description=rule.description,
                    matched=found.group(0)[:120],
                )
            )

    if not signals:
        return ThreatResult(
            malicious=False,
            rule_id="THREAT_CLEAR",
            reason="No threat rules matched this action request.",
            signals=[],
        )

    top = max(signals, key=lambda s: {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}.get(s.severity, 0))
    return ThreatResult(
        malicious=True,
        rule_id=top.code,
        reason=(
            f"Threat interception matched {top.code}: {top.description} "
            f"(severity {top.severity})."
        ),
        signals=signals,
    )


def catalogue() -> list[dict[str, str]]:
    """Machine-readable rule catalogue for the UI / About page."""
    return [
        {
            "code": rule.code,
            "severity": rule.severity,
            "description": rule.description,
        }
        for rule in RULES
    ]
