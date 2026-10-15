"""Run the Track B RAGAS harness repeatedly and summarise honestly.

Rubric (changed 2026-10-12, targets no longer graded):
  5 pts  harness on all 4 cases at least twice; per-case scores, mean, range;
         judge model named
  6 pts  per-metric explanation of what limits it (written from this output)
  4 pts  one pipeline change with before/after scores

So this runner:
  * runs evaluation_harness.RAGEvaluator N times (default 2) for one pipeline
    configuration (a "label", e.g. before / after)
  * treats NaN as a failed judge call (throttling, or a truncated judge reply), re-judges that
    SAME answer up to --max-retries times, and never averages a NaN in
  * writes one CSV per run plus a combined CSV with a `run` column (the
    rubric's docs/lab3-evidence/rag_eval.csv is the combined file) and a
    summary JSON + Markdown table with per-case scores, per-metric mean and
    min-max range across runs, the judge model, and generator token usage

    python -m rag.run_eval --label after --runs 2
    python -m rag.run_eval --label before --runs 2 --no-profile
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import evaluation_harness as eh
from rag import bedrock
from rag.pipeline import OfferPipeline

METRICS = ("faithfulness", "answer_relevancy", "context_recall")

# Haiku 4.5 on Bedrock, us-east-1 (per the handout's cost table): $1 / M input,
# $5 / M output tokens. Titan V2: $0.02 / M tokens.
PRICE = {"gen_in": 1.0 / 1e6, "gen_out": 5.0 / 1e6, "embed": 0.02 / 1e6}


def _judge_rows(df: pd.DataFrame, judge_model: str, region: str) -> pd.DataFrame:
    """Re-score already-generated answers (no new generation). Mirrors
    RAGEvaluator.run's scoring half so a NaN retry judges the SAME offer."""
    from datasets import Dataset
    from ragas import evaluate
    ds = Dataset.from_dict({
        "question": df["question"].tolist(),
        "answer": df["answer"].tolist(),
        "contexts": df["contexts"].tolist(),
        "ground_truth": df["ground_truth"].tolist(),
    })
    llm, emb = eh.build_bedrock_judge(judge_model, region)
    scores = evaluate(ds, metrics=[eh.faithfulness, eh.answer_relevancy, eh.context_recall],
                      llm=llm, embeddings=emb)
    return scores.to_pandas()


def _metric_cols(df: pd.DataFrame) -> list[str]:
    return [m for m in METRICS if m in df.columns]


def run_once(pipe: OfferPipeline, judge_model: str, region: str,
             max_retries: int) -> pd.DataFrame:
    cases = copy.deepcopy(eh.OFFER_TEST_CASES)
    evaluator = eh.RAGEvaluator(rag_invoke_fn=pipe.invoke, bedrock_model_id=judge_model,
                                region=region)
    df = evaluator.run(cases)
    # Recover the harness's own column names if ragas renamed them.
    rename = {"user_input": "question", "response": "answer",
              "retrieved_contexts": "contexts", "reference": "ground_truth"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df["customer_id"] = [tc.customer_id for tc in cases]
    df["loyalty_tier"] = [tc.customer_context["loyalty_tier"] for tc in cases]
    # The pipeline's per-call record (chunk IDs, tokens, latency) lines up
    # with the cases in call order.
    recs = pipe.calls[-len(cases):]
    df["chunk_ids"] = [";".join(r["chunk_ids"]) for r in recs]
    df["input_tokens"] = [r["input_tokens"] for r in recs]
    df["output_tokens"] = [r["output_tokens"] for r in recs]
    df["latency_ms"] = [r["latency_ms"] for r in recs]

    cols = _metric_cols(df)
    for attempt in range(1, max_retries + 1):
        bad = df.index[df[cols].isna().any(axis=1)].tolist()
        if not bad:
            break
        print(f"  NaN scores on rows {bad} (judge call failed: throttling, or the judge's "
              f"reply hit max_tokens); "
              f"re-judging the same answers, attempt {attempt}/{max_retries}")
        rescored = _judge_rows(df.loc[bad], judge_model, region)
        rescored = rescored.rename(columns={k: v for k, v in rename.items() if k in rescored.columns})
        for i, (_, row) in zip(bad, rescored.iterrows()):
            for m in cols:
                if pd.isna(df.at[i, m]) and not pd.isna(row[m]):
                    df.at[i, m] = row[m]
    still = df[cols].isna().any(axis=1).sum()
    if still:
        print(f"  WARNING: {still} row(s) still NaN after {max_retries} retries; "
              f"they are excluded from means and reported as NaN, not 0")
    return df


def summarise(runs: list[pd.DataFrame], label: str, config: dict, judge_model: str) -> dict:
    cols = _metric_cols(runs[0])
    per_case = {}
    for df in runs:
        for _, r in df.iterrows():
            per_case.setdefault(r["customer_id"], {"tier": r["loyalty_tier"], "scores": {m: [] for m in cols}})
            for m in cols:
                per_case[r["customer_id"]]["scores"][m].append(None if pd.isna(r[m]) else round(float(r[m]), 4))
    run_means = {m: [round(float(df[m].mean()), 4) for df in runs] for m in cols}
    overall = {m: {
        "mean_of_runs": round(float(np.nanmean(run_means[m])), 4),
        "min_run": round(float(np.nanmin(run_means[m])), 4),
        "max_run": round(float(np.nanmax(run_means[m])), 4),
        "range": round(float(np.nanmax(run_means[m]) - np.nanmin(run_means[m])), 4),
        "per_run": run_means[m],
    } for m in cols}
    u = bedrock.USAGE
    cost = (u["gen_input_tokens"] * PRICE["gen_in"] + u["gen_output_tokens"] * PRICE["gen_out"]
            + u["embed_tokens"] * PRICE["embed"])
    return {
        "label": label,
        "config": config,
        "judge_model": judge_model,
        "generator_model": bedrock.GEN_MODEL_ID,
        "embedding_model": bedrock.EMBED_MODEL_ID,
        "n_runs": len(runs),
        "n_cases": len(runs[0]),
        "overall": overall,
        "per_case": per_case,
        "generator_usage": dict(u, estimated_cost_usd=round(cost, 4),
                                note="judge (RAGAS) calls go through langchain and are billed separately"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def summary_markdown(s: dict) -> str:
    cols = list(s["overall"].keys())
    out = [f"### {s['label']}  (config: `{json.dumps(s['config'])}`)",
           f"Judge: `{s['judge_model']}` · generator: `{s['generator_model']}` · "
           f"{s['n_runs']} runs x {s['n_cases']} cases", "",
           "| metric | " + " | ".join(f"run {i + 1}" for i in range(s["n_runs"])) + " | mean | range |",
           "|---|" + "---|" * (s["n_runs"] + 2)]
    for m in cols:
        o = s["overall"][m]
        out.append(f"| {m} | " + " | ".join(f"{v:.3f}" for v in o["per_run"])
                   + f" | **{o['mean_of_runs']:.3f}** | {o['min_run']:.3f}-{o['max_run']:.3f} |")
    out += ["", "| case | tier | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
    for cid, pc in s["per_case"].items():
        cells = []
        for m in cols:
            vals = pc["scores"][m]
            cells.append(", ".join("NaN" if v is None else f"{v:.3f}" for v in vals))
        out.append(f"| {cid} | {pc['tier']} | " + " | ".join(cells) + " |")
    g = s["generator_usage"]
    out += ["", f"Generator usage: {g['gen_calls']} calls, {g['gen_input_tokens']:,} in / "
                f"{g['gen_output_tokens']:,} out tokens, {g['embed_calls']} embed calls; "
                f"est. ${g['estimated_cost_usd']:.4f} (judge billed separately)."]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", required=True, help="name for this configuration, e.g. before / after")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--strategy", default="AB", choices=("A", "B", "C", "AB"))
    ap.add_argument("--chunking", default="section", choices=("section", "fixed"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-profile", action="store_true", help="leave the customer profile out of contexts")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--judge-model", default=eh.DEFAULT_BEDROCK_MODEL_ID)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--out", default="evaluation_results")
    a = ap.parse_args()

    if not eh.RAGAS_AVAILABLE:
        raise SystemExit('ragas not installed: pip install ragas datasets "langchain-community<0.4" langchain-aws')
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    pipe = OfferPipeline(a.strategy, a.chunking, a.k, not a.no_profile, a.temperature)
    print(f"== Track B evaluation '{a.label}': {a.runs} runs x {len(eh.OFFER_TEST_CASES)} cases, "
          f"config {pipe.config}, judge {a.judge_model}")

    runs = []
    for i in range(1, a.runs + 1):
        print(f"\n---- run {i}/{a.runs} ----")
        df = run_once(pipe, a.judge_model, a.region, a.max_retries)
        df.insert(0, "run", i)
        df.insert(1, "label", a.label)
        p = os.path.join(a.out, f"rag_eval_{a.label}_run{i}_{ts}.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        print(f"  saved {p}")
        runs.append(df)

    combined = pd.concat(runs, ignore_index=True)
    combined_path = os.path.join(a.out, f"rag_eval_{a.label}_{ts}.csv")
    combined.to_csv(combined_path, index=False, encoding="utf-8")
    s = summarise(runs, a.label, pipe.config, a.judge_model)
    with open(os.path.join(a.out, f"rag_eval_{a.label}_{ts}_summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = summary_markdown(s)
    with open(os.path.join(a.out, f"rag_eval_{a.label}_{ts}_summary.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print("\n" + md)
    print(f"\nCombined CSV ({len(combined)} rows): {combined_path}")


if __name__ == "__main__":
    main()
