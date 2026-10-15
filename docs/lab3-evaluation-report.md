# Lab 3 evaluation report

Track: **B** (RAG retention-offer generation). Section 1 is the Task 1 churn-model
report; section 2 is the Track B evaluation.

## 1. Churn model (Task 1)

<!-- Filled from the final tracked training run in Phase 4 (the run that logs to
MLflow, registers the model and writes docs/lab3-evidence/). Until then the
numbers below are from the local dry-run on .dryrun/features.csv with the same
configuration (xgb-shallow: max_depth 3, eta 0.05, 400 rounds, early stopping 20,
scale_pos_weight = neg/pos). -->

### 1.1 Metrics

| metric | value |
|---|---|
| auc_roc | 0.7835 |
| precision_top10 | 0.7167 |
| recall_top10 | 0.3258 |
| baseline_auc_roc (days_since_last_purchase only) | 0.7225 |
| lift vs baseline (AUC) | +0.0610 |
| lift 95% CI (bootstrap, 1,000 resamples) | [0.0425, 0.0797] |
| best_iteration | 115 |

Confusion matrix at the top-10% cutoff: `docs/lab3-evidence/confusion-matrix.png`.
Feature importance (gain): `docs/lab3-evidence/feature-importance.png`.

### 1.2 Slices

Per-tier evaluation at the *global* top-10% cutoff: `docs/lab3-evidence/slice-evaluation.csv`.

(To be written from the final run: the Bronze tier absorbs roughly two thirds of
the contact budget; Gold, Silver and Platinum recall fall more than 10 points
below the aggregate; Platinum has too few positives for a stable estimate.)

## 2. Track B evaluation

### 2.1 Setup

- **Pipeline** (`rag/`): 4 policy documents -> 48 section chunks (23-252 words,
  median 71, chunk IDs like `POL-LOY-011#4`) -> `amazon.titan-embed-text-v2:0`
  (1024-d, normalised) in a FAISS inner-product index -> two-pass retrieval
  (`AB`: tier-benefit query, then constraint query, `k=5` each, merged without
  duplicates, up to 10 chunks) -> `us.anthropic.claude-haiku-4-5-20251001-v1:0`
  via the Converse API, temperature 0.2, max 400 output tokens. The corrected
  tier guidelines (three planted errors fixed, see
  `rag/prompts/tier_guidelines.md`) and the retrieved chunks are in the system
  prompt; the customer profile plus a plain-language interpretation of its
  numbers are in the user turn.
- **Harness**: `evaluation_harness.RAGEvaluator` on the four `OFFER_TEST_CASES`,
  driven by `rag/run_eval.py`, which runs the harness N times per configuration,
  re-judges any NaN row (bounded, same answer, never averaged in) and writes
  per-run and combined CSVs with a `run` column.
- **Judge**: RAGAS 0.4.3 `faithfulness`, `answer_relevancy`, `context_recall`
  with **`us.anthropic.claude-haiku-4-5-20251001-v1:0` as the judge LLM**
  (`langchain-aws` `ChatBedrock`, temperature 0, `max_tokens` 4096) and Titan V2
  for the answer-relevancy embeddings. The same model generates and judges; see
  2.3 for what that does and does not change.
- **Evidence**: `docs/lab3-evidence/rag_eval.csv` (16 rows: 2 labels x 2 runs x
  4 cases, no NaN), `docs/lab3-evidence/rag_eval_strategies.csv` (retrieval
  strategy comparison), `docs/lab3-evidence/track-b-examples.md` (5 correct pairs,
  2 failures, chunk IDs), `notebooks/lab3-demo.ipynb`.

### 2.2 Scores: two runs per configuration, all four cases

Label `before`: the judge sees only the retrieved policy chunks.
Label `after`: the customer profile is appended to the contexts the judge sees
(the pipeline change evaluated in 2.4). The generator prompt is identical in
both labels, so the offers come from the same system.

**Per-run means (4 cases each)**

| label | metric | run 1 | run 2 | mean | range |
|---|---|---|---|---|---|
| before | faithfulness | 0.384 | 0.333 | **0.358** | 0.333-0.384 |
| before | answer_relevancy | 0.350 | 0.396 | **0.373** | 0.350-0.396 |
| before | context_recall | 0.671 | 0.671 | **0.671** | 0.671-0.671 |
| after | faithfulness | 0.552 | 0.556 | **0.554** | 0.552-0.556 |
| after | answer_relevancy | 0.386 | 0.395 | **0.391** | 0.386-0.395 |
| after | context_recall | 0.733 | 0.608 | **0.671** | 0.608-0.733 |

**Per-case scores (run 1, run 2)**

| case | tier | faithfulness before | faithfulness after | answer_rel. before | answer_rel. after | context_recall before | context_recall after |
|---|---|---|---|---|---|---|---|
| CUST-10000317 | Gold | 0.385, 0.222 | 0.444, 0.333 | 0.400, 0.408 | 0.408, 0.408 | 0.750, 0.750 | 1.000, 1.000 |
| CUST-10000904 | Bronze | 0.286, 0.300 | 0.500, 0.500 | 0.303, 0.471 | 0.471, 0.470 | 0.600, 0.600 | 0.600, 0.600 |
| CUST-10000122 | Platinum | 0.500, 0.500 | 0.538, 0.500 | 0.353, 0.353 | 0.324, 0.343 | 0.333, 0.333 | 0.333, 0.333 |
| CUST-10000588 | Silver | 0.364, 0.308 | 0.727, 0.889 | 0.343, 0.352 | 0.343, 0.359 | 1.000, 1.000 | 1.000, 0.500 |

Run-to-run variation on identical configuration: faithfulness per case moves by
up to 0.16 (Silver after: 0.727 vs 0.889) and context recall by 0.5 on one case
(Silver after: 1.000 vs 0.500) even though retrieval is deterministic and that
offer's text differed by one clause. The run-level means are more stable
(faithfulness range 0.004-0.05) because the per-case noise partly cancels.

**Reference check** (instructor pipeline, 2026-10-12 announcement): faithfulness
0.47-0.68, answer relevancy 0.38-0.45, context recall 0.53-0.58, with a context
recall ceiling of 0.61-0.67. Our `after` configuration sits at 0.554 / 0.391 /
0.671: inside the range on the first two, and at the stated ceiling on recall.

### 2.3 What limits each metric

**Faithfulness (0.554 after; 0.358 before).** RAGAS splits the offer into atomic
statements and asks the judge whether each is inferable from the contexts; the
score is the supported fraction. Three things cap it here.

1. *Offers blend profile facts into policy facts.* "You earn 1× points on every
   **apparel** purchase" (Bronze, both runs) is one statement to the judge, and
   the word "apparel" comes from the profile, not from `POL-LOY-011#4`. With the
   profile outside the contexts the whole statement is unsupported; that is
   why the Bronze offer with no factual error scores 0.286 in `before` and
   0.500 in `after`, and why the Silver offer that mentions "52 days", "$1,985"
   and "home or beauty" jumps from 0.308 to 0.889. The remaining gap to 1.0 is
   mostly paraphrase and derivation: "no minimum spend required" (the chunk
   says "free standard shipping at any amount"), "twice the time" for a 60-day
   versus 30-day window, "$15 away from Gold". The judge is strict about
   inference even when the arithmetic is right.
2. *Real errors.* The Gold offer in `after` runs 1 and 2 (and `before` run 2)
   says "redeeming 100 of your accumulated points ... will reset your points
   expiration clock". `POL-LOY-011` §3 says only a qualifying *purchase* resets
   the clock. The three runs carrying that sentence are the three lowest Gold
   scores (0.222, 0.333, 0.444). This is a genuine hallucination, documented
   as failure 1 in `track-b-examples.md`; the judge is doing its job.
3. *Same model judging itself.* Generator and judge are both Haiku 4.5. That
   does not inflate faithfulness (the verdicts above are, if anything, harsh),
   but it means a misreading the generator makes is one the judge may share.
   The redemption/expiration fusion was caught; the Platinum embellishment
   "gear credit that you can apply to outdoor, electronics, or home purchases"
   (`after` run 1) was not penalised, and `POL-LOY-011` §4.1 does not say it.

**Answer relevancy (0.391 after; 0.373 before).** RAGAS asks the judge to
generate several questions that the answer would answer, embeds them and the
original question with Titan V2, and reports the mean cosine similarity. Our
"questions" are instructions ("Generate a retention offer for this Bronze member
who shops in store ...") and our answers are marketing copy that never restates
the task. The reverse-generated questions come out as "What benefits does a
Bronze member get?" or "How do loyalty points expire?", which are about the
*content* of the offer, not about generating one, so similarity to the original
instruction is structurally low. Two observations support that reading:
(a) the metric barely responds to anything we change: 0.373 vs 0.391 between
labels, 0.339-0.360 across retrieval strategies (2.5), and it is nearly flat
across cases (0.303-0.471); (b) the one case that scores highest, Bronze 0.47,
is the one whose instruction contains the most content words that the offer
repeats ("Bronze", "in store", "apparel", "purchased"). The ceiling the
instructor measured is about 0.73 and would require rewriting the questions
as questions, which is the fixture, not the pipeline. We report it, we do not
tune for it.

**Context recall (0.671 both labels; ceiling 0.61-0.67).** RAGAS splits the
ground truth into sentences and asks whether each is attributable to the
retrieved contexts. The ground truths in `OFFER_TEST_CASES` mix policy facts
with grading commentary, and no retrieval strategy can fetch commentary:

- Platinum (0.333 in every run of every strategy): the three ground-truth
  sentences are "emphasize recognition over a deep discount - a heavy
  percentage discount destroys margin on a $447 average order", the benefit
  list, and "the unused gear credit is the strongest available lever and costs
  nothing incremental". Only the benefit list exists in the corpus.
- Bronze (0.600 every run): "an incentive proportionate to a $312 lifetime
  value" and "a narrow category_diversity_score of 0.125 means one category out
  of eight" are not policy. Adding the profile as a context does not help
  because the profile has the numbers, not the interpretation.
- Gold (0.750 before, 1.000 after): the one sentence that moves is "acknowledge
  Gold status", which becomes attributable once the profile (with
  `loyalty_tier: Gold`) is a context.
- Silver (1.000 three times, 0.500 once): identical contexts across runs, so
  the 0.500 is judge noise on a 4-sentence ground truth where "the feature
  store tier is derived from lifetime value while policy uses trailing
  12-month spend" is borderline attributable.

So recall is capped by the fixture at roughly (policy sentences) / (all
sentences) and our 0.671 is at the instructor's ceiling. The strategy
comparison in 2.5 shows that retrieval *can* still lower it (strategy B drops
Platinum to 0.000) but cannot raise it above the cap.

### 2.4 Pipeline change: customer profile as a judged context

**Change.** `OfferPipeline(profile_in_contexts=True)` appends the customer
profile ("Customer profile:\n- loyalty_tier: Gold\n- days_since_last_purchase:
47 ...") as one more entry in the `contexts` list returned to the harness. The
generator already had the profile in its user turn; the change gives the judge
the same information. Motivation: an offer is supposed to be grounded in the
profile as much as in policy, and a faithfulness judge that cannot see the
profile must mark every tier, category and lapse statement as unsupported.

**Before / after** (2 runs each, same generator prompt, same judge configuration):

| metric | before (mean, range) | after (mean, range) | change |
|---|---|---|---|
| faithfulness | 0.358 (0.333-0.384) | 0.554 (0.552-0.556) | **+0.196** |
| answer_relevancy | 0.373 (0.350-0.396) | 0.391 (0.386-0.395) | +0.018 |
| context_recall | 0.671 (0.671-0.671) | 0.671 (0.608-0.733) | 0.000 |

**Judgement.** The faithfulness gain is real: the `before` and `after` ranges do
not overlap and the effect (+0.20) is well above the +/-0.05 run-to-run noise
at the run level. It is also exactly the size the instructor reported for the
same change (0.38 -> 0.60). It is not, however, a better *offer*: the generator
is unchanged, and the Gold hallucination scores 0.444 instead of 0.222 because
the sentences around it are now verifiable, not because it went away. The
change makes the metric measure what we want it to measure (is the offer
grounded in policy *and* profile) rather than improving the system. Answer
relevancy is unaffected within noise. Context recall is unaffected on average;
the Gold case gains the "acknowledge Gold status" sentence and the Silver case
loses 0.5 on one run to judge noise, which is a reminder that four cases times
two runs is a small sample.

One honest caveat about effect size. An earlier before/after pair (same day,
run under the previous system prompt, which still contained the retailer's
store count and had not yet banned the filler phrases, and under the judge's
default `max_tokens`, which produced one NaN) measured only +0.03 (0.530 ->
0.560; files `evaluation_results/rag_eval_*_20261015T2044*.csv`, not in the
evidence folder). Tightening the prompt pushed the generator to put a profile
fact in nearly every sentence, which is what makes the profile-as-context
change bite. The effect depends on how profile-heavy the offers are.

### 2.5 Retrieval strategy comparison (Template 4)

One run per strategy, profile in contexts; AB is run 1 of `after`.
`docs/lab3-evidence/rag_eval_strategies.csv`.

| strategy | query | faithfulness | answer_rel. | context_recall | recall per case (Gold, Bronze, Plat., Silver) |
|---|---|---|---|---|---|
| A | tier-benefit query, k=5 | 0.482 | 0.347 | 0.583 | 1.000, 0.400, 0.333, 0.600 |
| B | constraint query, k=5 | 0.281 | 0.360 | 0.350 | 0.750, 0.400, 0.000, 0.250 |
| C | hybrid query, doc filter {POL-LOY-011, POL-SHP-007}, k=5 | 0.556 | 0.339 | 0.508 | 1.000, 0.200, 0.333, 0.500 |
| **AB** (default) | A then B, merged, up to 10 chunks | 0.552 | 0.386 | 0.733 | 1.000, 0.600, 0.333, 1.000 |

Strategy B retrieves the same five chunks for every customer (`POL-LOY-011#5.1`,
`#5`, `POL-RET-004#4`, `KB-FAQ-022#returns`, `#guidance-for-automated-agents`):
its query has no tier in it, so it never fetches the benefit table, and the
generator then writes tier benefits from the corrected guidelines alone, which
the judge cannot see. Faithfulness and recall collapse together. Strategy C's
document filter drops the return policy, so the Bronze offer's "returns are free
at any location" and the 30-day window lose their support (Bronze recall 0.200).
The two-pass merge is the only variant that covers both the benefit table and
the constraints, at the cost of about 2,000 more input tokens per offer
(roughly 4,000 vs 2,700).

### 2.6 Cost and operations

Generator usage is metered in `rag/bedrock.py`: every 4-case run is 4 Converse
calls at about 4,000 input / 120 output tokens (about $0.02) plus 8 Titan query
embeddings. The judge's calls go through langchain and are not metered by our
code; at roughly 16 Haiku calls per run with 4-5k tokens each they cost on the
order of $0.10 per run. The day's Track B evaluation (13 harness runs including
the superseded pair, the index build, single-case tests and the notebook) came
to roughly $1.50 of Bedrock usage with nothing left running.

One operational finding: a NaN score is not always throttling. The one NaN we
hit was the judge's own reply truncated at `langchain-aws`'s default
`max_tokens` (ragas raises `LLMDidNotFinishException` and records NaN), which is
deterministic for that answer and so immune to the retry loop. Raising the
judge's `max_tokens` to 4096 in `evaluation_harness.build_bedrock_judge` fixed
it; `rag/run_eval.py` still re-judges NaN rows for the throttling case and never
averages a NaN in.
