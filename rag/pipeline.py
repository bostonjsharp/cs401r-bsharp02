"""End-to-end offer generation: profile + question -> retrieved policy -> offer.

`OfferPipeline.invoke` has the signature evaluation_harness.RAGEvaluator wants
(customer_context, question) -> {"answer": str, "contexts": list[str]} plus
extra keys the examples file and cost log use.

The `profile_in_contexts` switch is the before/after experiment for the
rubric's 4-point item: when on, the customer profile is appended to the
contexts list the judge sees. The offer is grounded in the profile (tier,
categories, lapse) as much as in policy, and a judge that cannot see the
profile marks every profile fact as unsupported.

Try one case from the command line (one Titan query embed + one Haiku call,
about a cent):

    python -m rag.pipeline --case 0 [--strategy AB] [--no-profile]
"""
from __future__ import annotations

import argparse
import json

from rag.generate import generate_offer, profile_block
from rag.index import PolicyIndex
from rag.retrieve import STRATEGIES, retrieve


class OfferPipeline:
    def __init__(self, strategy: str = "AB", chunking: str = "section", k: int = 5,
                 profile_in_contexts: bool = True, temperature: float = 0.2):
        self.index = PolicyIndex.build(chunking)
        self.strategy = strategy
        self.chunking = chunking
        self.k = k
        self.profile_in_contexts = profile_in_contexts
        self.temperature = temperature
        self.calls: list[dict] = []   # one record per invoke, for the cost log

    @property
    def config(self) -> dict:
        return {"retrieval": self.strategy, "chunking": self.chunking, "k": self.k,
                "profile_in_contexts": self.profile_in_contexts,
                "temperature": self.temperature}

    def invoke(self, customer_context: dict, question: str) -> dict:
        hits = retrieve(self.index, customer_context, self.strategy, self.k)
        gen = generate_offer(customer_context, question, hits, self.temperature)
        contexts = [c.text for c, _ in hits]
        if self.profile_in_contexts:
            contexts.append("Customer profile:\n" + profile_block(customer_context))
        record = {
            "answer": gen["text"].strip(),
            "contexts": contexts,
            "chunk_ids": [c.chunk_id for c, _ in hits],
            "scores": [round(s, 4) for _, s in hits],
            "input_tokens": gen["input_tokens"],
            "output_tokens": gen["output_tokens"],
            "stop_reason": gen["stop_reason"],
            "latency_ms": round(gen["latency_ms"], 1),
            "customer_id": customer_context.get("customer_id"),
        }
        self.calls.append({k: v for k, v in record.items() if k not in ("contexts",)})
        return record


def make_rag_invoke_fn(**kw):
    """What evaluation_harness.RAGEvaluator(rag_invoke_fn=...) takes."""
    pipe = OfferPipeline(**kw)
    return pipe.invoke, pipe


if __name__ == "__main__":
    from evaluation_harness import OFFER_TEST_CASES

    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, default=0, help="index into OFFER_TEST_CASES (0-3)")
    ap.add_argument("--strategy", choices=STRATEGIES, default="AB")
    ap.add_argument("--chunking", choices=("section", "fixed"), default="section")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-profile", action="store_true", help="leave the profile out of contexts")
    a = ap.parse_args()

    tc = OFFER_TEST_CASES[a.case]
    ctx = dict(tc.customer_context, customer_id=tc.customer_id)
    pipe = OfferPipeline(a.strategy, a.chunking, a.k, not a.no_profile)
    r = pipe.invoke(ctx, tc.question)
    print(f"== {tc.customer_id} ({ctx['loyalty_tier']}) | config {json.dumps(pipe.config)}")
    print(f"question: {tc.question}")
    print("retrieved:", ", ".join(f"{cid} ({s})" for cid, s in zip(r["chunk_ids"], r["scores"])))
    print(f"\n{r['answer']}\n")
    print(f"tokens in/out {r['input_tokens']}/{r['output_tokens']}, "
          f"{r['latency_ms']} ms, stop={r['stop_reason']}")
