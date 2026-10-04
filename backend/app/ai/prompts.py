"""Versioned prompt builders for the Gemini layer.

Prompts are written to make the non-authority boundary explicit to the model itself:
the model is asked for *language*, never for a verdict. It is told it cannot approve
anything, so even a jailbreak-style instruction inside a counterparty message cannot
reposition it as an authority.

Referenced by: ``app.ai.gemini_client``.
"""

from __future__ import annotations

from app.security.policy_engine import TransactionConstraints

PROMPT_VERSION = "trustmesh-demo-v1"

_AUTHORITY_NOTE = (
    "You are the reasoning layer of TrustMesh, an infrastructure system for trusted "
    "autonomous AI agents negotiating across organizations. You may REASON and you may "
    "PROPOSE. You are NOT the security authority. A separate deterministic policy engine "
    "decides whether any action is permitted. Never state or imply that an action is "
    "approved, permitted, safe or authorized. Never suggest that policy checks can be "
    "skipped, overridden or ignored. Never request credentials, secrets or access to "
    "another organization's data. Respond with JSON only."
)


def parse_intent_prompt(intent_text: str, constraints: TransactionConstraints) -> str:
    return f"""{_AUTHORITY_NOTE}

Task: read the buyer's procurement intent and return a structured JSON object.

Schema (JSON only, no prose):
{{
  "product": string,
  "quantity": integer,
  "max_budget_cents": integer,
  "max_delivery_days": integer,
  "certification_required": string,
  "summary": string
}}

All money values must be integer CENTS (e.g. $2,000 -> 200000). Never widen the
authority envelope that is provided below; only describe what the buyer asked for.

Buyer intent text:
\"\"\"
{intent_text}
\"\"\"

Currently registered authority envelope for reference:
{constraints.as_dict()}
"""


def supplier_offer_rationale_prompt(
    supplier_name: str,
    constraints: TransactionConstraints,
    *,
    price_cents: int,
    delivery_days: int,
    certification: str,
    base_rationale: str,
) -> str:
    return f"""{_AUTHORITY_NOTE}

Task: write the supplier's rationale for a concrete offer. The numbers below are
already fixed by the supplier's own commercial rules. Do not change them and do not
invent different ones.

Schema (JSON only):
{{
  "rationale": string,
  "tone": "neutral" | "persuasive" | "cautious" | "urgent",
  "highlights": [string]
}}

Supplier: {supplier_name}
Offer: ${price_cents / 100:,.2f} for {constraints.quantity} {constraints.product},
delivery in {delivery_days} days, certification {certification}.

Baseline rationale to polish and expand:
\"\"\"
{base_rationale}
\"\"\"
"""


def buyer_counter_rationale_prompt(
    *,
    buyer_ceiling_cents: int,
    supplier_floor_cents: int,
    target_price_cents: int,
    max_delivery_days: int,
    certification: str,
    product: str,
    quantity: int,
) -> str:
    return f"""{_AUTHORITY_NOTE}

Task: write the buyer's rationale for a counter-offer. The counter-offer must stay
inside the buyer's authority envelope. The numbers are fixed by the negotiation
solver - describe them, do not change them.

Schema (JSON only):
{{
  "rationale": string,
  "tone": "neutral" | "persuasive" | "cautious" | "urgent",
  "highlights": [string]
}}

Buyer ceiling: ${buyer_ceiling_cents / 100:,.2f}
Supplier floor: ${supplier_floor_cents / 100:,.2f}
Counter-offer: ${target_price_cents / 100:,.2f} for {quantity} {product},
delivery within {max_delivery_days} days, certification {certification}.
"""


def security_explanation_prompt(
    *,
    rule_id: str,
    agent_name: str,
    capability: str,
    reason: str,
) -> str:
    return f"""{_AUTHORITY_NOTE}

Task: write one short sentence, for a security analyst, explaining what a deterministic
TrustMesh rule blocked and why. Describe the rule; do not suggest a workaround and do
not question the decision.

Rule: {rule_id}
Agent: {agent_name}
Requested capability: {capability}
Decision reason: {reason}

Respond with JSON only:
{{{{"rationale": string}}}}
"""
