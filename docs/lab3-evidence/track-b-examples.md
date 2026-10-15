# Track B examples: retention-offer RAG pipeline

Five correct input/output pairs and two documented failures from the pipeline in
`rag/` (see `docs/lab3-evaluation-report.md`, section 2, for the scores and the
before/after experiment).

**Pipeline configuration for every example below**: retrieval strategy `AB`
(two-pass: profile query, then tier-specific query, merged), section chunking
(48 chunks), `k=5` per pass (up to 10 chunks after the merge), generator
`us.anthropic.claude-haiku-4-5-20251001-v1:0` via the Converse API,
`temperature=0.2`, `maxTokens=400`. Embeddings `amazon.titan-embed-text-v2:0`
(1024-d, normalised, FAISS inner product).

**Judge**: RAGAS 0.4.3 (`faithfulness`, `answer_relevancy`, `context_recall`)
with `us.anthropic.claude-haiku-4-5-20251001-v1:0` as the judge LLM
(`langchain-aws` `ChatBedrock`, temperature 0, max_tokens 4096) and Titan V2 for
the answer-relevancy embeddings. Scores quoted per example are from
`docs/lab3-evidence/rag_eval.csv` (column `label` = `before` means the judge saw
only the policy chunks; `after` means the customer profile was appended to the
contexts). The *generator* prompt is identical in both labels; the flag only
changes what the judge is allowed to count as support.

All offers were checked by hand against `northstar-policy-docs/` (the retrieved
corpus); section references are to `loyalty-program-terms.md` (POL-LOY-011),
`return-policy.md` (POL-RET-004), `shipping-policy.md` (POL-SHP-007) and
`customer-faq.md` (KB-FAQ-022).

Retrieved chunk IDs per customer (retrieval is deterministic, so every run of a
case retrieves the same set):

| customer | retrieved chunk IDs (pass A then pass B, merged) |
|---|---|
| CUST-10000317 (Gold) | POL-LOY-011#4, KB-FAQ-022#loyalty, POL-LOY-011#2.3, POL-LOY-011#2.1, POL-RET-004#1.1, POL-LOY-011#5.1, POL-LOY-011#5, POL-RET-004#4, KB-FAQ-022#returns, KB-FAQ-022#guidance-for-automated-agents |
| CUST-10000904 (Bronze) | POL-LOY-011#4, KB-FAQ-022#loyalty, POL-RET-004#1.1, POL-LOY-011#2.3, POL-LOY-011#2, POL-LOY-011#5.1, POL-LOY-011#5, POL-RET-004#4, KB-FAQ-022#returns, KB-FAQ-022#guidance-for-automated-agents |
| CUST-10000122 (Platinum) | POL-LOY-011#4, KB-FAQ-022#loyalty, POL-RET-004#1.1, POL-LOY-011#2.3, POL-LOY-011#2, POL-LOY-011#5.1, POL-LOY-011#5, POL-RET-004#4, KB-FAQ-022#returns, KB-FAQ-022#guidance-for-automated-agents |
| CUST-10000588 (Silver) | POL-LOY-011#4, KB-FAQ-022#loyalty, POL-LOY-011#2, POL-LOY-011#2.3, POL-RET-004#1.1, POL-LOY-011#5.1, POL-LOY-011#5, POL-RET-004#4, KB-FAQ-022#returns, KB-FAQ-022#guidance-for-automated-agents |

`POL-LOY-011#4` is the tier-benefit table, `#2.x` the tier thresholds and
upgrade/downgrade rules, `#5.x` the retention-offer rules (45-day validity),
`POL-RET-004#1.1` the 30/60-day return window, `#4` the return-shipping fees.

---

## Correct pair 1: Gold, online shopper (CUST-10000317)

**Input profile**

```
churn_probability: 81%        loyalty_tier: Gold
days_since_last_purchase: 47  total_lifetime_value: $3,120.40
avg_order_value: $186.25      category_diversity_score: 0.5
online_to_store_ratio: 0.72   top_categories: Outdoor, Footwear
```

Question: *Generate a personalized retention offer for this at-risk Gold member
who shops mostly online for outdoor gear and footwear.*

**Generated offer** (label `before`, run 1; faithfulness 0.385, answer relevancy
0.400, context recall 0.750)

> As a Gold member, you receive free return shipping on all eligible
> merchandise, which applies to both outdoor gear and footwear ordered online.
> Your last purchase was 47 days ago, and your next order qualifies you for free
> standard shipping at any amount—no minimum threshold required. Gold members
> also earn points at 1.5× the standard rate, and every 100 points you
> accumulate can be redeemed for $5 in credit toward future purchases. This
> offer is valid for 45 days from today and can be applied to your next order of
> outdoor or footwear items.

**Why it is correct**: every benefit is a Gold entitlement in POL-LOY-011 §4
(free return shipping, free standard shipping at any amount, 1.5× points);
100 points = $5 is §3; the 45-day validity is §5.1. It names only the
customer's own categories, states no 60-day window (Platinum only) and no
expedited shipping. The low faithfulness score despite zero factual errors is
discussed in the report: the judge treats "which applies to both outdoor gear
and footwear ordered online" and "can be applied to your next order of outdoor
or footwear items" as unsupported inferences, and in the `before` label the
"47 days" profile fact has no context to be verified against.

## Correct pair 2: Bronze, in-store apparel shopper (CUST-10000904)

**Input profile**

```
churn_probability: 74%        loyalty_tier: Bronze
days_since_last_purchase: 68  total_lifetime_value: $312.80
avg_order_value: $41.15       category_diversity_score: 0.125
online_to_store_ratio: 0.20   top_categories: Apparel
```

Question: *Generate a retention offer for this Bronze member who shops in store,
buys only apparel, and has not purchased in over two months.*

**Generated offer** (label `after`, run 1; faithfulness 0.500, answer relevancy
0.471, context recall 0.600)

> As a Bronze member, you earn 1× points on every apparel purchase, and those
> points never expire as long as you make a purchase within 18 months—your
> clock resets with each order. Your next apparel purchase will bring you back
> into earning range, and 100 points equals $5 toward anything in the store.
> Stop by to browse new apparel and redeem any points you've accumulated;
> returns are free at any location, and you have 30 days from purchase to
> return eligible items. This offer is valid for 45 days.

**Why it is correct**: 1× points and the 30-day window are the Bronze row of
POL-LOY-011 §4; the 18-month clock that resets on any purchase is §3 and
KB-FAQ-022; "returns are free at any location" is KB-FAQ-022 ("Returns to a
store are always free"), which is exactly the right angle for a 80%-in-store
customer who would otherwise pay $7.95 to return by mail. It claims nothing
from a higher tier (no free return shipping, no early access) and the
incentive stays modest for a $312 lifetime value, as the ground truth asks.

## Correct pair 3: Platinum, high-value, broad interest (CUST-10000122)

**Input profile**

```
churn_probability: 66%        loyalty_tier: Platinum
days_since_last_purchase: 39  total_lifetime_value: $8,940.00
avg_order_value: $447.00      category_diversity_score: 0.75
online_to_store_ratio: 0.55   top_categories: Outdoor, Electronics, Home
```

Question: *Generate a retention offer for this high-value Platinum member with
broad category interest and a large average order.*

**Generated offer** (label `after`, run 2; faithfulness 0.500, answer relevancy
0.343, context recall 0.333)

> As a Platinum member, you earn 2× points on every purchase across Outdoor,
> Electronics, and Home categories—and your points never expire as long as you
> make a purchase at least once every 18 months. Your $100 annual gear credit
> is ready to apply toward any eligible item, and you have 60 days to return
> anything that doesn't work out, with free return shipping included. Redeem
> 100 points for $5 in credit whenever you're ready, and use this offer within
> 45 days to lock in these benefits on your next order.

**Why it is correct**: 2× points, the $100 annual gear credit, the 60-day
return window and free return shipping are all Platinum entitlements
(POL-LOY-011 §4, §4.1; POL-RET-004 §1.1). It leads with recognition and the
unused gear credit rather than a percentage discount, which is what the ground
truth and the (corrected) tier guidelines ask for on a $447 average order.
Context recall is stuck at 0.333 for every Platinum run because two of the
three ground-truth sentences are grading commentary ("a heavy percentage
discount destroys margin", "the strongest available lever") that no policy
chunk states; see the report.

## Correct pair 4: Silver, just under the Gold threshold (CUST-10000588)

**Input profile**

```
churn_probability: 70%        loyalty_tier: Silver
days_since_last_purchase: 52  total_lifetime_value: $1,985.00
avg_order_value: $132.30      category_diversity_score: 0.375
online_to_store_ratio: 0.61   top_categories: Home, Beauty
```

Question: *Generate a retention offer for this Silver member whose lifetime
value is just under the Gold threshold.*

**Generated offer** (label `after`, run 1; faithfulness 0.727, answer relevancy
0.343, context recall 1.000)

> As a Silver member, you earn 1.25× points on every purchase, and your points
> don't expire as long as you make a purchase within 18 months—your clock
> resets with each order. Your last purchase was 52 days ago, so a home or
> beauty order now would keep your balance active and put you within $15 of
> Gold tier status, where you'd unlock free standard shipping at any order
> amount and free return shipping on all eligible items. You also get 12-hour
> early access to sales as a Silver member, giving you first pick on home and
> beauty inventory. This offer is valid for 45 days.

**Why it is correct**: it cites only Silver entitlements as active (1.25×
points, 12-hour early access, POL-LOY-011 §4) and phrases the Gold benefits
conditionally ("where you'd unlock"), which is the behaviour the trap in this
test case is checking. The near-threshold framing is legitimate per the ground
truth. One caveat carried into failure 2 below: "within $15 of Gold" silently
equates lifetime value with the trailing-12-month spend that POL-LOY-011 §2
actually uses, so the number is plausible but not something the policy
supports.

## Correct pair 5: Bronze, second run (CUST-10000904)

Same profile and question as pair 2.

**Generated offer** (label `before`, run 1; faithfulness 0.286, answer relevancy
0.303, context recall 0.600)

> As a Bronze member, you earn 1× points on every apparel purchase, and your
> points don't expire as long as you make a purchase within 18 months—your
> clock resets with each order. Your next apparel purchase will put you back on
> track and keep your current points balance active. When you reach 100 points,
> you can redeem them for $5 in credit toward gear in any category. This offer
> is valid for 45 days from today.

**Why it is correct**: shorter than pair 2 and every claim is in POL-LOY-011 §3
and §4. It is included because it is the cleanest illustration of the
faithfulness ceiling: an offer with no false statement scores 0.286 when the
judge cannot see the profile. The judge decomposes it into roughly seven
statements and marks "on every apparel purchase", "put you back on track",
"keep your current points balance active" and "toward gear in any category" as
not inferable from the chunks, because each one blends a profile fact
(category, lapse) or a paraphrase into a policy fact.

---

## Failure 1: redeeming points "resets the expiration clock" (CUST-10000317, Gold)

Produced identically in label `before` run 2 (faithfulness 0.222) and label
`after` runs 1 and 2 (0.444, 0.333); temperature 0.2 makes Haiku nearly
deterministic, so the same wrong sentence recurs.

**Generated offer**

> As a Gold member, you currently receive free return shipping on all orders,
> which applies to your outdoor gear and footwear purchases. Your points earn at
> 1.5× the standard rate, and any order qualifies for free standard shipping—no
> minimum spend required. **Since your last purchase 47 days ago, redeeming 100
> of your accumulated points for $5 toward your next outdoor or footwear order
> will reset your points expiration clock to 18 months from that purchase
> date.** This offer is valid for 45 days from today.

**What is wrong**: POL-LOY-011 §3 (chunk `POL-LOY-011#4` carries the table,
`KB-FAQ-022#loyalty` the FAQ wording) says "Any qualifying *purchase* resets the
expiration clock on the member's entire balance". Redemption does not reset
anything; the model fused the redemption rule (100 points = $5) with the
expiration rule from the same chunk into one causal sentence. A customer who
redeemed points without buying would be told their balance is safe when it is
not. The judge caught it: this is the lowest-scoring Gold run.

**Proposed fix** (two layers):

1. *Targeted*: add to `rag/prompts/system_prompt.md` the rule "Only a qualifying
   purchase resets the 18-month expiration clock; redeeming points does not.
   Do not combine two rules into one cause-and-effect sentence." Cheap, and
   this failure is deterministic, so it is testable with one re-run.
2. *General*: a verification pass in `rag/pipeline.py`. After generation, make
   one more Haiku call that lists each claim in the offer and labels it
   `supported` / `unsupported` against the retrieved chunks plus the profile
   (the same decomposition RAGAS faithfulness does, but at generation time).
   If any claim is `unsupported`, regenerate once with the offending sentence
   quoted back as "remove or correct this claim". Cost: roughly one extra call
   per offer (about 4k input tokens, under a cent) and one regeneration in the
   minority of cases. This also catches the Platinum embellishment seen in
   `after` run 1 ("gear credit that you can apply to outdoor, electronics, or
   home purchases"), which POL-LOY-011 §4.1 does not say.

## Failure 2: tier arithmetic from the wrong number (CUST-10000588, Silver)

Label `before`, run 2 (faithfulness 0.308, answer relevancy 0.352, context
recall 1.000).

**Generated offer**

> As a Silver member, you earn 1.25× points on every purchase, and your points
> don't expire as long as you make a purchase within 18 months—your clock resets
> with each order. Your last purchase was 52 days ago, so you're well within
> that window. **Spend $500 more in the next 12 months and you'll reach Gold**,
> unlocking free standard shipping on all orders, free return shipping, and 2
> free gear repairs per year. **Stock up on home and beauty items now and watch
> your points grow at the higher rate** while you close the gap to Gold.

**What is wrong**: two things. The "$500 more" figure is arithmetic the model
invented: Gold starts at $2,000 of trailing-12-month spend (POL-LOY-011 §2,
chunk `POL-LOY-011#2`), the profile gives a *lifetime* value of $1,985, and
neither number yields $500. Underneath that is the trap the test case was
built for: the feature store derives `loyalty_tier` from lifetime value while
the policy uses trailing-12-month spend, so the model has no number from which
the gap to Gold can be computed at all. The second bolded phrase then implies
the Gold earn rate ("the higher rate") applies to the purchase the customer is
being asked to make now, which is a higher-tier benefit stated as active. The
`after` runs of the same case phrase the gap as "$15" and the Gold benefits
conditionally, so this is a run-to-run instability, not a constant error.

**Proposed fix**: stop asking the model to do arithmetic or to reason about
tier thresholds from raw profile numbers. In `rag/generate.py`, extend
`interpret_profile()` so the pipeline precomputes the only tier statement the
data can support: if the feature store carries trailing-12-month spend (Lab 2's
`purchase_frequency_180d` and the raw transactions make a `spend_365d` feature
a one-line addition to the feature pipeline), emit "Trailing-12-month spend is
$X; Gold begins at $2,000; gap $Y". If it does not, emit "The tier in this
profile is derived from lifetime value, which is not how the policy defines
tiers; do not state a dollar gap to the next tier and describe the current
tier as 'currently'." Then add to the system prompt: "Do not compute or state
dollar amounts that are not written in the context or the profile." The model
can only misstate a gap if it is asked to produce one.

---

### Run-to-run notes

- Retrieval is deterministic (same chunk IDs every run of a case), so every
  variation between runs comes from the generator (temperature 0.2) and from
  the judge, which on identical input scored the Silver `after` offer's context
  recall at 1.000 then 0.500.
- One judge call returned NaN during an earlier batch (Platinum faithfulness).
  The cause was the judge's own reply hitting `langchain-aws`'s default
  `max_tokens`, not throttling; `evaluation_harness.build_bedrock_judge` now sets
  `max_tokens=4096`, and the batch was re-run under that single judge
  configuration. No NaN in `rag_eval.csv`.
