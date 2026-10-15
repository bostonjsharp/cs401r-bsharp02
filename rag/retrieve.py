"""The three retrieval query strategies from Template 4, plus a two-pass
variant. Each returns a de-duplicated list of (Chunk, score) in rank order.

Why strategies matter: context recall is scored on whether the retrieved
chunks cover the facts the ground truth expects (tier benefits AND the
constraints an offer must not violate). A query that only asks about
benefits never fetches the final-sale clause; a query that only asks about
constraints never fetches the tier table.
"""
from __future__ import annotations

from rag.corpus import Chunk
from rag.index import PolicyIndex

STRATEGIES = ("A", "B", "C", "AB")


def query_a(customer: dict) -> str:
    """Option A, tier-benefit query: the entitlements that constrain the offer."""
    return (f"loyalty tier {customer['loyalty_tier']} benefits points earn rate "
            f"free shipping threshold return window birthday reward")


def query_b(customer: dict) -> str:
    """Option B, constraint query: the limits the offer must not violate."""
    return ("final sale non-returnable exclusions expired offers cannot be reinstated "
            "retention offer validity single use combinability")


def query_c(customer: dict) -> str:
    """Option C, hybrid: situation-aware, used with a doc_id filter."""
    channel = "online" if customer.get("online_to_store_ratio", 0.5) > 0.5 else "in store"
    return (f"tier {customer['loyalty_tier']} entitlements and shipping thresholds "
            f"for a customer who shops {channel}")


def retrieve(index: PolicyIndex, customer: dict, strategy: str = "AB",
             k: int = 5) -> list[tuple[Chunk, float]]:
    if strategy == "A":
        return index.search(query_a(customer), k=k)
    if strategy == "B":
        return index.search(query_b(customer), k=k)
    if strategy == "C":
        return index.search(query_c(customer), k=k, doc_ids={"POL-LOY-011", "POL-SHP-007"})
    if strategy == "AB":
        # Two passes, one per need, merged in rank order with duplicates dropped.
        seen, merged = set(), []
        for hits in (index.search(query_a(customer), k=k),
                     index.search(query_b(customer), k=k)):
            for c, s in hits:
                if c.chunk_id not in seen:
                    seen.add(c.chunk_id)
                    merged.append((c, s))
        return merged
    raise ValueError(f"unknown retrieval strategy {strategy!r}; choose from {STRATEGIES}")
