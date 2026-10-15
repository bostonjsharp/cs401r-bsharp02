"""Assemble the prompts and call the generator.

Fixes the two documented weaknesses of the kit templates:
  Template 1 never received the loyalty tier  -> the profile block (with tier)
      is in the user turn AND the corrected tier guidelines are in the system
      prompt, so the model knows which tier's benefits apply.
  Template 2 passed raw numbers with no meaning -> interpret_profile() turns
      each number into a plain statement the model can act on.
"""
from __future__ import annotations

import os

from rag import bedrock
from rag.corpus import Chunk

PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
        text = f.read()
    # Strip the HTML comment header (audit notes) from the guidelines file.
    if text.lstrip().startswith("<!--"):
        text = text.split("-->", 1)[1]
    return text.strip()


def format_context(hits: list[tuple[Chunk, float]]) -> str:
    """Each chunk labelled with its citable ID so the model can refer to it
    and the examples file can list what was retrieved."""
    return "\n\n".join(f"[{c.chunk_id}] {c.text}" for c, _ in hits)


def profile_block(customer: dict) -> str:
    lines = []
    for key, val in customer.items():
        if isinstance(val, float) and key == "churn_probability":
            val = f"{val:.0%}"
        elif isinstance(val, float) and key in ("total_lifetime_value", "avg_order_value"):
            val = f"${val:,.2f}"
        elif isinstance(val, list):
            val = ", ".join(map(str, val))
        lines.append(f"- {key}: {val}")
    return "\n".join(lines)


def interpret_profile(customer: dict) -> str:
    """Plain-language reading of the numeric signals (Template 2 fix)."""
    out = []
    tier = customer.get("loyalty_tier", "unknown")
    out.append(f"The customer is currently a {tier} member; apply {tier} benefits only.")

    p = customer.get("churn_probability")
    if p is not None:
        level = "high" if p >= 0.75 else "elevated" if p >= 0.6 else "moderate"
        out.append(f"Churn risk is {level} ({p:.0%}).")

    d = customer.get("days_since_last_purchase")
    if d is not None:
        out.append(f"Last purchase was {int(d)} days ago"
                   + ("; the lapse is a fact you may state, not a feeling to address." if d >= 45 else "."))

    ltv, aov = customer.get("total_lifetime_value"), customer.get("avg_order_value")
    if ltv is not None and aov is not None:
        if aov >= 300:
            out.append(f"Average order is ${aov:,.0f}: a percentage discount is costly; "
                       "prefer recognition, credits, or access over discounting.")
        elif ltv < 500:
            out.append(f"Lifetime value is ${ltv:,.0f}: keep the incentive modest and simple.")
        else:
            out.append(f"Lifetime value ${ltv:,.0f}, average order ${aov:,.0f}: "
                       "a moderate incentive is proportionate.")

    div = customer.get("category_diversity_score")
    if div is not None:
        n_cat = max(1, round(div * 8))
        if div <= 0.2:
            out.append(f"Shops about {n_cat} of 8 categories: a single-category shopper; "
                       "cross-category discovery is a reasonable angle.")
        elif div >= 0.6:
            out.append(f"Shops about {n_cat} of 8 categories: broad interest; "
                       "lean on breadth rather than one category.")
        else:
            out.append(f"Shops about {n_cat} of 8 categories.")

    r = customer.get("online_to_store_ratio")
    if r is not None:
        if r >= 0.6:
            out.append(f"Shops mostly online ({r:.0%}): shipping and return-shipping "
                       "benefits are relevant.")
        elif r <= 0.4:
            out.append(f"Shops mostly in store ({1 - r:.0%} in store): shipping incentives "
                       "are largely irrelevant; store pickup, in-store returns and points "
                       "matter more.")
        else:
            out.append("Shops online and in store about equally.")

    cats = customer.get("top_categories")
    if cats:
        out.append(f"Recommend from what they already buy: {', '.join(cats)}.")
    return "\n".join(f"- {s}" for s in out)


def build_prompts(customer: dict, question: str, hits: list[tuple[Chunk, float]]) -> tuple[str, str]:
    system = _load("system_prompt.md").format(
        retrieved_context=format_context(hits),
        tier_guidelines=_load("tier_guidelines.md"),
    )
    user = _load("user_prompt.md").format(
        question=question,
        profile_block=profile_block(customer),
        profile_interpretation=interpret_profile(customer),
    )
    return system, user


def generate_offer(customer: dict, question: str, hits: list[tuple[Chunk, float]],
                   temperature: float = 0.2) -> dict:
    system, user = build_prompts(customer, question, hits)
    result = bedrock.converse(system, user, temperature=temperature)
    result["system_prompt_chars"] = len(system)
    return result
