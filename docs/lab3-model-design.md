Track declaration: Track B (RAG offer generation)

# Lab 3 Model Design

<!-- Numbers are from the local dry-run of the final configuration (xgb-shallow)
on .dryrun/features.csv. Refresh from docs/lab3-evidence/evaluation_metrics.json
after the Phase 4 graded run; the argument does not depend on the last digit. -->

## Churn model

NorthStar's churn problem is 10,000 customers, 13 Feature Store columns (11
numeric RFM and behavioural features, the loyalty tier, and a recency heuristic
held out as the baseline), a 22% positive rate, and a retention team that acts
on the top 10% of scores and wants to know why a customer is on the list.
Gradient-boosted trees fit that shape better than either alternative. Logistic
regression needs features roughly linear in the log-odds, and ours are not:
`days_since_last_purchase` and `purchase_frequency_180d` interact (a 47-day
lapse means something different for a weekly buyer than for a twice-a-year
one) and the spend columns are heavy-tailed. Trees find those thresholds and
interactions unaided, and gain importance gives the retention team a readable
"why". A neural network has nothing to offer at 10k rows and 11 columns: no
representation to learn, more hyperparameters than signal, less
interpretability. XGBoost also weights the minority class directly
(`scale_pos_weight` = negatives/positives, 3.55) and stops early on validation,
which is what kept the final model at 115 shallow trees instead of 400.

We would switch if free text arrived (reviews, support transcripts), where
learned embeddings start to pay; if the business required a monotone, auditable
score, where constrained logistic regression is the honest choice; or if the
labelled population shrank to a few hundred (a new region or tier), where
boosting overfits.

## What the features bought

The baseline is a model trained on `days_since_last_purchase` alone, the rule
the business already has for free: AUC 0.7225 on the 3,000-row validation
split. The full 11-feature model scores 0.7835, a lift of 0.061 with a
bootstrap 95% CI of [0.0425, 0.0797]. The lift is real but modest. At the
top-10% contact list, precision is 0.717 and recall 0.326 (215 of 660
churners). Recency carries most of the signal; the other features sharpen the
ranking rather than transform it.

Gain importance shows where the lift comes from. `purchase_frequency_180d` and
`days_since_last_purchase` tie at the top (about 87 each), then
`purchase_frequency_30d` (41) and `avg_order_value` (31). Frequency buys the
lift: it separates the lapsed-but-regular customer from the lapsed-and-fading
one, which recency alone cannot. The bottom of the chart is the honest part.
`customer_tenure_days`, `online_to_store_ratio` and `category_diversity_score`
sit at 13 to 15, a sixth of the leaders; the kit's claim that the behavioural
features "catch churners who still look active" is not what this dataset
shows, and the ablation that would settle it (drop both, compare AUC) is the
next experiment. `churn_risk_score` is decorative: adding it moved AUC to
0.7787 and cut the best iteration from 115 to 12, a model leaning on a
pre-computed recency rule instead of learning. It stays out.

## LLM system

The offer generator is retrieval-augmented generation over the four policy
documents with Claude Haiku 4.5 on Bedrock, not a fine-tuned model. The facts an
offer must get right (tier thresholds, earn rates, return windows, 45-day
validity) change on NorthStar's schedule: the loyalty terms reserve the right to
change with 60 days' notice. Fine-tuning freezes those facts in weights and
cannot cite a source; RAG re-reads the current document at request time and
returns chunk IDs we log beside every offer. Fine-tuning would also need a
labelled set of good offers that does not exist, and 48 chunks is far too small
to tune on.

The primary production risk is a confident sentence the context does not
support. Evaluation found it twice: a Gold offer saying that redeeming points
resets the expiration clock (only a purchase does), and a Silver offer computing
a dollar gap to Gold from a lifetime-value figure the policy does not use.
RAGAS faithfulness catches these offline; in production each one reaches a
customer.

## Failure cases

From `docs/lab3-evidence/track-b-examples.md`: (1) rule fusion, two adjacent
facts in one chunk becoming one false causal sentence; fix with a
claim-verification call before send and a one-line prompt rule. (2) Tier
arithmetic from the wrong field, because the feature-store tier derives from
lifetime value while the policy uses trailing-12-month spend; fix by
precomputing the only tier statement the data supports and forbidding dollar
math in the prompt.

## With 10x the time or data

Replace the single 70/15/15 split with time-based validation (train before a
cutoff, validate on the next quarter) so the reported AUC is the one the
retention team will see, and allocate the contact list per tier instead of at a
global top-10% cutoff, which hands Bronze two thirds of the slots and leaves
Platinum recall at 3 of 21. For the offer system, rewrite the evaluation
fixture so ground truths are policy claims rather than grading commentary,
making context recall a retrieval metric again, and have the retention team
score a held-out set of real offers to calibrate the LLM judge against humans.
