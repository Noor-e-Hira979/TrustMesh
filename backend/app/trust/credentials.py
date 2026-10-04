"""Credential issue / verify / revoke.

Secret material is generated inside the backend, stored only as an HMAC-SHA256
digest and compared with ``hmac.compare_digest``. It is never returned by an API
route, never logged and never handed to the frontend or to an agent runtime.

Referenced by: ``app.trust.identity``, ``app.trust.trust_engine``,
``app.services.bootstrap``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import Credential, CredentialStatus, new_id, utcnow

logger = logging.getLogger("trustmesh.credentials")

CREDENTIAL_TTL_HOURS = 72


class CredentialError(Exception):
    """Raised when credential material cannot be issued."""


@dataclass(slots=True)
class CredentialVerification:
    valid: bool
    reason: str
    rule_id: str
    credential_id: str | None = None
    scope: list[str] | None = None
    expires_at: datetime | None = None


def _digest(agent_id: str, secret: str) -> str:
    return hmac.new(
        settings.credential_secret, f"{agent_id}:{secret}".encode(), hashlib.sha256
    ).hexdigest()


def issue_credential(
    session: Session,
    *,
    agent_id: str,
    org_id: str,
    scope: list[str],
    ttl_hours: int = CREDENTIAL_TTL_HOURS,
) -> tuple[Credential, str]:
    """Create a credential. Returns the row and the one-time plaintext secret.

    The plaintext is handed straight to the bootstrap/demo runtime inside this
    process. It is deliberately never stored and never returned over HTTP.
    """
    secret = secrets.token_urlsafe(32)
    row = _insert_credential(
        session,
        agent_id=agent_id,
        org_id=org_id,
        secret=secret,
        scope=scope,
        ttl_hours=ttl_hours,
    )
    return row, secret


def issue_credential_with_secret(
    session: Session,
    *,
    agent_id: str,
    org_id: str,
    secret: str,
    scope: list[str],
    ttl_hours: int = CREDENTIAL_TTL_HOURS,
) -> Credential:
    """Issue a credential from caller-supplied material.

    Used by the demo bootstrap so the seeded agents verify against the deterministic
    fixture secrets in ``app.agents.agent_registry``. The secret still is not stored:
    only its HMAC digest is persisted.
    """
    return _insert_credential(
        session,
        agent_id=agent_id,
        org_id=org_id,
        secret=secret,
        scope=scope,
        ttl_hours=ttl_hours,
    )


def _insert_credential(
    session: Session,
    *,
    agent_id: str,
    org_id: str,
    secret: str,
    scope: list[str],
    ttl_hours: int,
) -> Credential:
    if not secret:
        raise CredentialError("refusing to issue a credential with empty material")
    row = Credential(
        id=new_id("CRED"),
        agent_id=agent_id,
        org_id=org_id,
        credential_hash=_digest(agent_id, secret),
        scope=list(scope),
        status=CredentialStatus.ACTIVE.value,
        issued_at=utcnow(),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    session.add(row)
    session.flush()
    return row


def verify_credential(
    session: Session, *, agent_id: str, presented_secret: str, org_id: str | None = None
) -> CredentialVerification:
    """Constant-time verification of presented credential material.

    An agent may hold more than one credential row (rotation, re-issuance), so every
    row is considered rather than assuming a single result. The most recently issued
    credential is evaluated first.
    """
    rows = list(
        session.execute(
            select(Credential)
            .where(Credential.agent_id == agent_id)
            .order_by(Credential.issued_at.desc())
        )
        .scalars()
        .all()
    )

    if not rows:
        return CredentialVerification(
            valid=False,
            reason="No credential is registered for this agent.",
            rule_id="CREDENTIAL_UNKNOWN",
        )

    if org_id is not None and not any(hmac.compare_digest(r.org_id, org_id) for r in rows):
        return CredentialVerification(
            valid=False,
            reason="Credential organization binding does not match the presented identity.",
            rule_id="CREDENTIAL_ORG_BINDING_MISMATCH",
            credential_id=rows[0].id,
        )

    if not presented_secret:
        return CredentialVerification(
            valid=False,
            reason="No credential material was presented with the request.",
            rule_id="CREDENTIAL_NOT_PRESENTED",
            credential_id=rows[0].id,
        )

    now = datetime.now(timezone.utc)
    saw_revoked = False
    saw_expired = False
    signature_matched: Credential | None = None

    for row in rows:
        presented_digest = _digest(agent_id, presented_secret)
        if not hmac.compare_digest(row.credential_hash, presented_digest):
            continue
        signature_matched = row

        if row.status == CredentialStatus.REVOKED.value:
            saw_revoked = True
            continue
        if row.status == CredentialStatus.EXPIRED.value:
            saw_expired = True
            continue
        expiry = row.expires_at
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= now:
                saw_expired = True
                continue

        return CredentialVerification(
            valid=True,
            reason="Credential is active, unexpired and bound to this agent and organization.",
            rule_id="CREDENTIAL_VALID",
            credential_id=row.id,
            scope=list(row.scope or []),
            expires_at=row.expires_at,
        )

    if saw_revoked:
        return CredentialVerification(
            valid=False,
            reason="Credential has been revoked.",
            rule_id="CREDENTIAL_REVOKED",
            credential_id=signature_matched.id if signature_matched else rows[0].id,
        )
    if saw_expired:
        return CredentialVerification(
            valid=False,
            reason="Credential has expired.",
            rule_id="CREDENTIAL_EXPIRED",
            credential_id=signature_matched.id if signature_matched else rows[0].id,
        )
    return CredentialVerification(
        valid=False,
        reason="Credential signature does not match the registered credential.",
        rule_id="CREDENTIAL_SIGNATURE_MISMATCH",
        credential_id=rows[0].id,
    )


def revoke_credential(session: Session, credential_id: str) -> bool:
    row = session.get(Credential, credential_id)
    if row is None:
        return False
    row.status = CredentialStatus.REVOKED.value
    row.revoked_at = utcnow()
    session.flush()
    logger.warning("credential %s revoked", credential_id)
    return True


def credential_fingerprint(credential_id: str | None) -> str:
    """Short, non-reversible fingerprint safe to show in the UI."""
    if not credential_id:
        return "none"
    return hashlib.sha256(credential_id.encode()).hexdigest()[:12]
