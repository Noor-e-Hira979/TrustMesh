"""Gemini client with a hard deterministic fallback.

Contract (spec §10, §34):
* If no API key, no network, a timeout, an error, or invalid structured output, this
  module returns a deterministic value instead of raising.
* The returned object always satisfies the same Pydantic contract, so callers have
  exactly one code path.
* The *numbers that policy judges* never come from this module. It supplies rationale
  language only. A change in model temperature can therefore never flip an ALLOW into
  a BLOCK.

Referenced by: ``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai import prompts
from app.ai.schemas import IntentParseOut, NegotiationLineOut, RationaleOut
from app.core.config import settings
from app.security.policy_engine import TransactionConstraints

logger = logging.getLogger("trustmesh.ai")

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass(slots=True)
class AIResult:
    """A typed AI answer plus provenance, so the UI can show which engine spoke."""

    value: BaseModel
    mode: str  # "gemini" | "demo"
    prompt_version: str = prompts.PROMPT_VERSION
    fallback_reason: str | None = None
    latency_ms: int = 0

    @property
    def used_ai(self) -> bool:
        return self.mode == "gemini"


class GeminiClient:
    """Thin, defensive wrapper over ``google-genai``."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._unavailable_reason: str | None = None
        self.last_mode = "demo"

    # --------------------------------------------------------------- plumbing
    def _ensure_client(self) -> Any | None:
        if not settings.gemini_configured:
            self._unavailable_reason = "GEMINI_API_KEY is not configured"
            return None
        if self._client is not None:
            return self._client
        try:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)
            self._unavailable_reason = None
            return self._client
        except Exception as exc:  # pragma: no cover - import/env dependent
            self._unavailable_reason = f"Gemini SDK unavailable: {type(exc).__name__}"
            logger.warning(self._unavailable_reason)
            return None

    def status(self) -> dict[str, Any]:
        return {
            "configured": settings.gemini_configured,
            "model": settings.gemini_model,
            "prompt_version": prompts.PROMPT_VERSION,
            "last_mode": self.last_mode,
            "unavailable_reason": self._unavailable_reason,
            "authority": "non-authoritative: reasoning and language only",
        }

    async def _generate(self, prompt: str) -> tuple[dict[str, Any] | None, str | None]:
        """Returns ``(parsed_json, failure_reason)``."""
        client = self._ensure_client()
        if client is None:
            return None, self._unavailable_reason or "gemini unavailable"

        last_error: str | None = None
        for attempt in range(1, max(1, settings.gemini_max_attempts) + 1):
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=settings.gemini_model,
                        contents=prompt,
                        config={
                            "temperature": 0.2,
                            "max_output_tokens": 900,
                            "response_mime_type": "application/json",
                        },
                    ),
                    timeout=settings.gemini_timeout_seconds,
                )
                text = getattr(response, "text", None)
                if not text:
                    last_error = "empty response text"
                    continue
                parsed = self._extract_json(text)
                if parsed is None:
                    last_error = "response was not valid JSON"
                    continue
                return parsed, None
            except TimeoutError:
                last_error = f"timeout after {settings.gemini_timeout_seconds}s"
            except ValidationError as exc:
                last_error = f"pydantic validation error: {exc.error_count()} issue(s)"
            except Exception as exc:  # network, auth, quota, SDK
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("gemini attempt %s failed: %s", attempt, last_error)
        return None, last_error or "unknown gemini failure"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        candidates = [text.strip()]
        block = _JSON_BLOCK.search(text)
        if block:
            candidates.append(block.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    async def _validated(
        self, prompt: str, schema: type[T], fallback: T
    ) -> AIResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        payload, failure = await self._generate(prompt)
        latency = int((loop.time() - started) * 1000)

        if payload is not None:
            try:
                value = schema.model_validate(payload)
                self.last_mode = "gemini"
                return AIResult(value=value, mode="gemini", latency_ms=latency)
            except ValidationError as exc:
                failure = f"model output failed schema validation ({exc.error_count()} issue(s))"

        logger.info("falling back to deterministic mode: %s", failure)
        self.last_mode = "demo"
        return AIResult(
            value=fallback,
            mode="demo",
            fallback_reason=failure,
            latency_ms=latency,
        )

    # ------------------------------------------------------------- use cases
    async def parse_intent(
        self, intent_text: str, constraints: TransactionConstraints
    ) -> AIResult:
        fallback = IntentParseOut(
            product=constraints.product,
            quantity=constraints.quantity,
            max_budget_cents=constraints.budget_cents,
            max_delivery_days=constraints.max_delivery_days,
            certification_required=constraints.certification_required,
            summary=(
                f"Deterministic read of the buyer's intent: {constraints.quantity} "
                f"{constraints.product} within ${constraints.budget_cents / 100:,.2f} and "
                f"{constraints.max_delivery_days} days."
            ),
        )
        return await self._validated(
            prompts.parse_intent_prompt(intent_text, constraints), IntentParseOut, fallback
        )

    async def supplier_rationale(
        self,
        *,
        supplier_name: str,
        constraints: TransactionConstraints,
        price_cents: int,
        delivery_days: int,
        certification: str,
        base_rationale: str,
    ) -> AIResult:
        fallback = RationaleOut(
            rationale=base_rationale,
            tone="neutral",
            highlights=[
                f"${price_cents / 100:,.2f} for {constraints.quantity} units",
                f"{delivery_days}-day delivery",
                f"{certification} certification",
            ],
        )
        prompt = prompts.supplier_offer_rationale_prompt(
            supplier_name,
            constraints,
            price_cents=price_cents,
            delivery_days=delivery_days,
            certification=certification,
            base_rationale=base_rationale,
        )
        return await self._validated(prompt, RationaleOut, fallback)

    async def buyer_counter_rationale(
        self,
        *,
        buyer_ceiling_cents: int,
        supplier_floor_cents: int,
        target_price_cents: int,
        max_delivery_days: int,
        certification: str,
        product: str,
        quantity: int,
        base_rationale: str,
    ) -> AIResult:
        fallback = RationaleOut(
            rationale=base_rationale,
            tone="neutral",
            highlights=[
                f"Buyer ceiling ${buyer_ceiling_cents / 100:,.2f}",
                f"Counter-offer ${target_price_cents / 100:,.2f}",
                f"Within {max_delivery_days}-day delivery",
            ],
        )
        prompt = prompts.buyer_counter_rationale_prompt(
            buyer_ceiling_cents=buyer_ceiling_cents,
            supplier_floor_cents=supplier_floor_cents,
            target_price_cents=target_price_cents,
            max_delivery_days=max_delivery_days,
            certification=certification,
            product=product,
            quantity=quantity,
        )
        return await self._validated(prompt, RationaleOut, fallback)

    async def security_explanation(
        self,
        *,
        rule_id: str,
        agent_name: str,
        capability: str,
        reason: str,
    ) -> AIResult:
        fallback = RationaleOut(rationale=reason, tone="cautious", highlights=[rule_id])
        prompt = prompts.security_explanation_prompt(
            rule_id=rule_id,
            agent_name=agent_name,
            capability=capability,
            reason=reason,
        )
        return await self._validated(prompt, RationaleOut, fallback)


gemini_client = GeminiClient()

__all__ = ["AIResult", "GeminiClient", "gemini_client"]
