"""Structured-output contracts for the AI layer.

These are the *only* shapes the model may return. Anything else is rejected by
Pydantic and the caller falls back to deterministic mode (spec §10, §34).

Dependency direction note (spec §6): this package must never import
``app.security.policy_engine``, ``app.trust.credentials`` or a database session.
The AI returns data; it cannot return a permission.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RationaleOut(BaseModel):
    """Natural-language explanation of a proposal. No authority, no numbers of record."""

    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=1200)
    tone: Literal["neutral", "persuasive", "cautious", "urgent"] = "neutral"
    highlights: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("rationale")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("rationale must not be blank after normalisation")
        return cleaned


class IntentParseOut(BaseModel):
    """Structured read of a buyer's free-text procurement intent."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=160)
    quantity: int = Field(gt=0, le=10_000_000)
    max_budget_cents: int = Field(gt=0)
    max_delivery_days: int = Field(gt=0, le=365)
    certification_required: str = Field(default="VALID", max_length=64)
    summary: str = Field(default="", max_length=600)

    @field_validator("certification_required")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "VALID").strip().upper() or "VALID"


class NegotiationLineOut(BaseModel):
    """A framing of a negotiation message. Numbers are supplied by the solver."""

    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=1200)
    highlights: list[str] = Field(default_factory=list, max_length=5)
    emphasis: Literal["price", "delivery", "certification", "relationship"] = "price"


AIOutput = RationaleOut | IntentParseOut | NegotiationLineOut

__all__ = ["AIOutput", "IntentParseOut", "NegotiationLineOut", "RationaleOut"]
