# Lab 3 To-Do: Model Development

Due **Sat Oct 17, midnight**. Tag `lab3-submit`, push the tag, paste the repo URL in Canvas.
Points: Task 1 Churn model (35) + Task 2 LLM system, Track B or C (35) + Task 3 Design doc (20)
+ Task 4 Repo quality (10) = 100. Teardown evidence is a **gate**: without
`docs/lab3-teardown-output.txt`, Task 4 is capped at half credit.

Handout: `C:\Users\bosto\Downloads\Lab_3--Model Development (1).html` (updated 2026-10-12;
the Oct 8 copy without " (1)" is stale).
Starter kit: unpacked into the repo on 2026-10-08; updated kit (`Lab3-Starter-Kit (1).zip`)
copied in on 2026-10-14 (Phase 0 below says where).

### Changes 2026-10-12 (read first)

1. **Evidence You Submit.** The TA grades only the `lab3-submit` tag and cannot see AWS.
   New kit script `scripts/collect-lab3-evidence.sh` (+ `collect_lab3_evidence.py`) exports
   the Athena query, MLflow runs and Registry versions into `docs/lab3-evidence/`, checks
   every required file by exact name, and cross-checks that the numbers in
   `evaluation_metrics.json`, one MLflow run and the registered version agree ("one chain
   of numbers", tolerance 6e-5). Run it after the final training run, **before teardown**.
   The Task 1 evaluation report moves to its own file `docs/lab3-evaluation-report.md`; the
   demo notebook is `notebooks/lab3-demo.ipynb` committed **with outputs** from one
   Restart & Run All (execution counts must be 1..N). Missing evidence costs no points this
   term (TA asks for a 10-minute screen-share instead), but a clean collector report means
   no follow-up.
2. **Track B RAGAS targets are no longer graded.** The instructor's reference pipeline
   scored faithfulness 0.47-0.68, answer relevancy 0.38-0.45, context recall 0.53-0.58
   (ceiling 0.61-0.67 even with the whole corpus as context). The 15 evaluation points are
   now: **5** harness run on all 4 cases at least twice, per-case scores + mean + range,
   judge model named; **6** for each metric, a specific explanation of what limits it,
   citing cases or claims; **4** one pipeline change with before/after scores and an honest
   judgement (a change that did not help still earns full marks).
3. Track C is now Amazon Bedrock AgentCore deployed from a separate Terraform root (6.x
   provider). Irrelevant to us; the kit's `agent/` and `infrastructure/environments/agent/`
   were deliberately **not** copied in.

---

## What we are building, in one paragraph

Lab 2 produced one labeled row per customer (9,999 rows, 13 features, `churn_label`
= 1 if the customer bought nothing in the 90 days after T = 2026-04-01; ~22% churn).
Lab 3 trains an **XGBoost churn classifier** on those rows, pulled from the Feature
Store offline store **through Athena** (not from a CSV, not from the Parquet). The
model must beat a "recency only" model (`days_since_last_purchase` alone) by a margin
whose 95% bootstrap CI excludes zero, hit precision >= 0.50 and recall >= 0.25 in the
top 10% of scores, log >= 3 runs to a SageMaker **MLflow App**, and land in the Model
Registry as `PendingManualApproval`. Then we build ONE LLM system on Bedrock: a RAG
offer generator (Track B, ours) or an AgentCore customer-service agent (Track C),
evaluate it with the provided harness, write a ~700-word design doc and a separate
evaluation report, and export the AWS-side evidence into `docs/lab3-evidence/`.

Why it is hard: about a third of churners are still buying right up to T, so recency
alone misses them. The behavioural features from Lab 2 (category diversity, channel
mix, basket size) are what should separate those customers. The rubric checks that
they actually did.

---

## DECISION: Track B (RAG offer generation), chosen 2026-10-11

Declared at the top of `docs/lab3-model-design.md` (done). Same 35 points either way:
runs end to end (10), evaluation implemented and documented (15), >= 2 failure modes
with concrete fixes (10).

| | Track B: Offer generation (RAG) - **ours** | Track C: Customer service agent (AgentCore, since 2026-10-12) |
|---|---|---|
| Build | Titan embeddings -> FAISS -> Haiku 4.5 generates a 2-3 sentence offer grounded in the 4 policy docs | Bedrock AgentCore Harness + Gateway + Cedar policy, deployed from a separate Terraform root on the 6.x provider |
| Eval | RAGAS (faithfulness, answer relevancy, context recall) judged by a second Haiku call. **Targets no longer graded**; graded on running it >= 2x, reporting per-case/mean/range, explaining each metric's limits, and one measured before/after change | 5 scripted scenarios run >= 2x, plus `policy_tests.py` 7/7 at the gateway |
| Dependency risk | `ragas 0.4.3`, `langchain-community 0.3.31`, `langchain-aws`, `faiss 1.15.1` all import on Python 3.14 (verified 2026-10-11) | Terraform 6.x provider, boto3 >= 1.43.57, AgentCore permissions |
| Deliverables | `docs/lab3-evidence/rag_eval.csv`, `docs/lab3-evidence/track-b-examples.md` (5 pairs + 2 failures, each with profile, offer, retrieved chunk IDs; fixes for failures; judge named), chunking/embedding justification, the 3 planted errors in `prompt_templates/` found and named, the tier-definition gap handled | `agent_eval.csv`, `agent-traces.jsonl`, `agent-cost.csv`, `policy-tests.json`, `track-c-failures.md` |
| Bedrock spend | Generation + judge: roughly $0.10-0.30 per full eval; plan on 4 evals (2 before, 2 after) | AgentCore sessions ~$0.003 each |

The plan's original lean toward Track C rested on "Track B's grade depends on an LLM
judge". The 2026-10-12 change removed exactly that risk, so Track B stands.

---

## COST MAP (read before any AWS step)

Budget alarm for the whole course: **$10/month**. Lab 2's full session cost under $1.

| Thing | Cost | Needed? | Rule |
|---|---|---|---|
| Lab 2 stack re-apply: NAT Gateway + public IPv4 | ~$0.05/hr while up | YES, the Feature Store has to exist for the Athena query | Apply, run everything, tear down **the same day** |
| Glue crawler + 2 jobs (re-populate the Feature Group) | ~$0.20-0.30 per pipeline run | YES, once | Run once; do not re-run the feature job (append-only offline store, duplicates) |
| Athena query over the offline store | $5/TB scanned; our table is a few MB | YES | effectively $0 |
| **MLflow App** (`create-mlflow-app`) | **no charge**, serverless | YES, once, keep it for Labs 4 and 6 | The ONLY MLflow product we touch |
| **MLflow Tracking Server** (`create-mlflow-tracking-server`) | **$0.60/hr until deleted, ~$43 over a weekend** | NO. NEVER. | If anything asks for an instance size (Small/Medium) you are on the wrong product. Stop. |
| Model Registry (model package group + package) | free | YES | artifact tar.gz goes under `artifacts/` in the Lab 1 bucket |
| Local XGBoost training | free | YES | every Track A rubric item is reachable locally |
| SageMaker Training Job on spot `ml.m5.large` | < $0.01/run | optional, skip | on-demand quota is 0; spot quota is 4; `use_spot_instances=True, max_wait>=max_run` |
| SageMaker endpoint | $0.05-0.10/hr until deleted | NOT needed | Do not create one. `teardown-lab3.sh` deletes any that exist |
| Studio kernels / Studio Domain apps | ~$0.05/hr | NOT needed | Do not open Studio for this lab |
| Bedrock Haiku 4.5 via `us.anthropic.claude-haiku-4-5-20251001-v1:0` | ~$1/M input, ~$5/M output tokens | YES for Track B/C | cents at lab scale; log tokens per run |
| Bedrock Titan Embeddings V2 `amazon.titan-embed-text-v2:0` | ~$0.02/M tokens | Track B yes, Track C optional | 52 chunks embeds for well under a cent |

Working estimate for the whole lab: **$1-3**, almost all of it the NAT Gateway hours.

---

## Where things stand (2026-10-08)

- AWS account `829485866627` is **clean**: no data bucket, no Feature Group, no Domain,
  no NAT, no MLflow App, no tracking server, no endpoints, no model package groups.
  Only `northstar-tfstate-829485866627` (remote state) exists. So Phase 4 starts with
  a full `terraform apply` of the Lab 2 stack.
- `docs/bedrock-access-verification.txt` shows PASS for Titan and Haiku 4.5 (Task 4, 2 pts, done).
- Toolchain: AWS CLI 2.36.40 (**has** `create-mlflow-app`), Python 3.14.2, boto3 1.43.95,
  `mlflow` 3.16.0 and `sagemaker-mlflow` 0.5.0 already installed, pandas/sklearn present.
- Gaps: **`xgboost` not installed**; **`sagemaker` is 3.22.0**, which dropped the
  `feature_store` module the skeleton's Athena path imports (the skeleton calls this
  "defect 55" and says pin `<3.0.0`); `ragas`/`langchain-*`/`faiss` not installed.
- Lab 2 teardown left the Glue trigger `enabled = false` and dropped the Feature Group
  offline-store trailing slash, so the rebuild creates things fresh. Start the pipeline
  with `bash scripts/run-lab2-pipeline.sh`, never by enabling the trigger.

---

## Phase 0 - Setup (FREE)

Starter kit placement (done 2026-10-08, mirrors the kit's own layout, same as Lab 2):

| Kit file | Repo location |
|---|---|
| `churn_training_skeleton.py` | `churn_training_skeleton.py` (repo root) |
| `evaluation_harness.py` | `evaluation_harness.py` (repo root) |
| `prompt_templates/offer_generation_prompts.md` | `prompt_templates/` |
| `northstar-policy-docs/*.md` (4 docs) | `northstar-policy-docs/` |
| `scripts/teardown-lab3.sh` | `scripts/teardown-lab3.sh` (replaced 2026-10-14 by the updated kit's 5-step version) |
| `scripts/collect-lab3-evidence.sh`, `scripts/collect_lab3_evidence.py` (new 2026-10-12) | `scripts/` |
| `agent/`, `infrastructure/environments/agent/` (Track C only) | **not copied** |
| (ours) | `docs/lab3-evidence/` (collector output), `notebooks/` (demo notebook) |

- [x] Copy the kit in (above). Nothing existing was overwritten.
- [x] (done 2026-10-14) Copy the updated kit's three changed scripts in, byte-identical to
      the zip; create `docs/lab3-evidence/` and `notebooks/`. The new `teardown-lab3.sh`
      adds a Track C destroy step (skips itself when `infrastructure/environments/agent/.terraform`
      is absent) and four read-only `bedrock-agentcore-control` checks; all four return 0
      on our CLI 2.36.40, so the teardown evidence will not trip on them.
- [x] (done 2026-10-14) Smoke-run the collector from the venv so Phase 4 does not
      discover a missing import: `python scripts/collect_lab3_evidence.py --track B`.
      **Windows note:** the `.sh` wrapper calls `python3`, which here is NOT the venv
      interpreter. Run the `.py` directly from the activated venv with the same tee:
      `python scripts/collect_lab3_evidence.py --track B 2>&1 | tee docs/lab3-evidence/collect-output.txt`.
- [x] Python environment (done 2026-10-11: `.venv` on Python 3.14.2, every package imports, xgboost 3.4.1). Python 3.14 is a risk for `xgboost` wheels and for the
      Track B pin set. Create a venv and try 3.14 first; fall back to `python3`
      (3.13 in Git Bash) if a wheel is missing:
      `python -m venv .venv && source .venv/Scripts/activate`
- [x] `requirements-lab3.txt` (done 2026-10-11, all 14 imports verified) (the skeleton references a `requirements.txt` the kit
      does not ship): `xgboost`, `scikit-learn`, `pandas`, `pyarrow`, `matplotlib`,
      `boto3`, `mlflow`, `sagemaker-mlflow`. Track B adds
      `ragas datasets "langchain-community<0.4" langchain-aws faiss-cpu`.
      **Import each package after installing**; a clean pip install is not evidence.
- [x] Decide the Athena loader approach (decided 2026-10-11: plain boto3) (see Phase 1): plain boto3 Athena (recommended,
      no `sagemaker` SDK needed) vs pinning `sagemaker>=2.200,<3` in the venv.
- [x] `.gitignore` (done 2026-10-11): add `.venv/`, `model/`, `output/`, `evaluation_results/`, `*.tar.gz`,
      `*.faiss`. Keep `*.tfvars` and `.env` lines (committed secrets = automatic zero).
- [x] (done 2026-10-11, `.dryrun/features.csv`, 9,999 x 16, 22.0% churn) Produce a **local copy of the feature table** for free development, from the
      Lab 2 dry run (same 9,999 customers the real Feature Group will hold):
      ```bash
      docker run --rm -v "$PWD:/work" -w /work amazon/aws-glue-libs:glue_libs_4.0.0_image_01 \
        spark-submit glue-scripts/local_dry_run.py --csv northstar-raw-sample.csv --out /work/.dryrun
      ```
      then convert `.dryrun/features/*.parquet` to `.dryrun/features.csv` (pandas).
      Feed it to the skeleton with `--local-data-path` while developing. This is
      dev-only: the graded path is Athena, and the final runs in Phase 4 use it.
- [x] Write the track declaration line at the top of `docs/lab3-model-design.md` (Track B, done 2026-10-11).

## Phase 1 - Track A code: `churn_training_skeleton.py` (FREE)

**Status 2026-10-14: code complete; local smoke test passes on all three planned configs.**
The Athena loader, MLflow logging and registry call are written but can only be exercised
in Phase 4 (no App, no Feature Group, no bucket exist yet). Local numbers on
`.dryrun/features.csv` (XGBoost 3.4.1, 6,999 / 3,000 split, baseline AUC 0.7225 in all three):

| run | config | best_iter | AUC | P@10 | R@10 | lift 95% CI |
|---|---|---|---|---|---|---|
| xgb-baseline | depth 6, eta 0.1, 200 rounds, 11 features | 13 | 0.7818 | 0.6967 | 0.3167 | [0.0395, 0.0784] |
| xgb-shallow | depth 3, eta 0.05, 400 rounds | 115 | **0.7835** | **0.7167** | **0.3258** | [0.0425, 0.0797] |
| xgb-plus-risk | run 1 params + `churn_risk_score` | 12 | 0.7787 | 0.7100 | 0.3227 | [0.0376, 0.0755] |

Findings to carry into the report: (1) `xgb-shallow` is the final-run candidate; (2) adding
`churn_risk_score` does not help (it is redundant with `days_since_last_purchase`, the
top-2 gain feature), so it is decorative; (3) with one global top-10% cutoff, Bronze
takes ~200 of the 300 contact slots and Gold/Silver/Platinum all sit >10pp below the
aggregate recall; Platinum has 21 churners in 307 rows, enough to report but read its
recall (3 of 21) with that granularity in mind. Windows gotcha fixed in the script:
stdout is forced to UTF-8 so the kit's box-drawing characters survive `| tee`.

Every TODO in the skeleton, in the order `main()` calls them. Test each against
`--local-data-path .dryrun/features.csv` so nothing here touches AWS.

### 1a. `load_features_from_feature_store` (rubric: 5 pts, "issues an Athena query, no CSV path in the data-loading code")
- [x] Keep the skeleton's query verbatim: `ROW_NUMBER() OVER (PARTITION BY customer_id
      ORDER BY event_time DESC, write_time DESC)`, `NOT is_deleted`, epoch-seconds
      bounds, `ORDER BY customer_id` (row order drives the split; without it the
      metrics change every run).
- [x] Implement with boto3: `sagemaker.describe_feature_group()` ->
      `OfflineStoreConfig.DataCatalogConfig` gives database + table name;
      `athena.start_query_execution` with `OutputLocation =
      s3://<bucket>/artifacts/athena-results/`; poll `get_query_execution` until
      SUCCEEDED; read the result object into a DataFrame. (Alternative: pin
      `sagemaker<3` and use the commented `feature_group.athena_query()` block.)
- [x] Print **`Athena QueryExecutionId: <id>`** (exact prefix) as soon as the query
      reaches SUCCEEDED. The collector regexes this line out of `train-final.log` and
      looks the ID up in Athena to export the SQL, state and bytes scanned
      (`docs/lab3-evidence/athena-query.json`). Our boto3 loader already has the ID from
      `start_query_execution`. A final log that shows "Loading features from local file"
      fails the item.
- [x] Bucket and feature group names come from `terraform output` (Task 4: "reused, not
      recreated"), passed as `--artifacts-bucket` / `--feature-group-name`.
- [x] Date window defaults (2026-01-01 .. 2027-01-01) bound the Lab 2 **ingest** time,
      not the feature window; leave them.

### 1b. `preprocess_features`
- [x] Decide and document imputation (dry run showed zero nulls; XGBoost handles NaN
      natively; the `-1` sentinel is fine but say why).
- [x] Cast `churn_label` to int (it is Integral in the Feature Group).

### 1c. Split carrying the tier
- [x] `train_test_split(X, y, df[SLICE_COLUMN], test_size=0.30, random_state=42, stratify=y)`
      so `tier_val` lines up with `y_val`. Expect 6,999 / 3,000.

### 1d. `evaluate_model` extras (rubric: thresholds 8 pts, baseline CI 7 pts)
- [x] Already implemented in the kit: AUC, precision/recall @ top 10%, recency-only
      baseline (`train_recency_baseline`), paired bootstrap CI (2,000 replicates).
      Reference on XGBoost 3.2.0: full 0.7696, baseline 0.7233, lift +0.0464,
      CI [0.0254, 0.0670], P@10 0.6833, R@10 0.3106. Expect close, not identical.
- [x] Add: confusion matrix at the chosen threshold (document the threshold; the
      top-10% cutoff is the business one) saved as `<output-data-dir>/confusion-matrix.png`,
      feature importance (gain) saved as `<output-data-dir>/feature-importance.png`.
      Dev runs write to `./output` (ignored); the final run writes to
      `docs/lab3-evidence/`, so the committed PNGs come from the registered model.
- [x] Record `xgb.__version__` in metrics and in the evaluation report.

### 1e. `evaluate_slices` (rubric: 5 pts)
- [x] Per `loyalty_tier`: AUC, recall@10% (threshold computed on the full validation
      set, applied within the slice), **n and positives per slice**. Flag any tier
      whose recall is > 10pp below aggregate, and call out any tier too small to
      conclude from (handout: ~300 Platinum test rows is enough; ~33 was not).
- [x] Save the table as `<output-data-dir>/slice-evaluation.csv`, one row per tier,
      columns exactly `tier, n, positives, auc, recall, too_small` (the collector checks
      for tier/n/auc/recall column names; the handout asks for positives and the
      too-small flag). It is embedded in `docs/lab3-evaluation-report.md`.

### 1f. `track_run` (rubric: 5 pts, >= 3 runs with params AND metrics via `mlflow.search_runs`)
- [x] `mlflow.set_tracking_uri(args.mlflow_arn)`, `set_experiment("northstar-churn")`,
      `log_params` (all hyperparameters, the **resolved** `scale_pos_weight`,
      `xgboost_version`, feature set name), `log_metrics` (every numeric metric,
      plus per-tier metrics with a `tier_<name>_` prefix).
      Log the metrics dict **as `evaluate_model` returns it** (already rounded to 4 dp).
      The collector requires `evaluation_metrics.json.auc_roc` to equal some run's
      `metrics.auc_roc` within 6e-5, and requires at least one `params.*` column other
      than seed to differ across runs (our depth/eta/feature-set design does; the feature
      set must be logged as a param).
- [x] Planned experiment (three runs that mean something, not three seeds):
      1. `xgb-baseline`  : depth 6, eta 0.1, 200 rounds, 11 features
      2. `xgb-shallow`   : depth 3-4, eta 0.05, 400 rounds (early stopping decides)
      3. `xgb-plus-risk` : run 1 params + `churn_risk_score` added, to report
         with/without as the handout requires (it is a recency heuristic; leakage check)
      Optional 4: `scale_pos_weight` 1.0 vs computed 3.545 to show the imbalance handling.
- [x] Add `--feature-set` (or `--include-risk-score`) argument so run 3 is a flag,
      not an edit. No literals in the training call (Task 4, 3 pts).
- [ ] ~~Script that calls `mlflow.search_runs()` and writes `docs/lab3-mlflow-runs.txt`~~
      Superseded: the collector writes `docs/lab3-evidence/mlflow-runs.csv` and
      `mlflow-app.json` itself.

### 1g. `save_and_register_model` (rubric: 5 pts, `PendingManualApproval`)
- [x] Tar `model.json` (XGBoost JSON format; the binary `.xgb` format is deprecated in 3.x) + `model_metadata.json` into `model.tar.gz`, upload to
      `s3://<bucket>/artifacts/models/churn/<run-name>/model.tar.gz`.
- [x] `create_model_package_group` **`northstar-churn-models`** (exact name; Lab 5
      deploys from it), idempotent if it exists.
- [x] `create_model_package` with `InferenceSpecification` pointing at the SageMaker
      XGBoost 1.7-1 container for us-east-1, `ModelApprovalStatus="PendingManualApproval"`,
      metrics in `CustomerMetadataProperties` (`{k: str(v)}` of the same rounded dict, so
      `auc_roc`, `precision_top10`, `recall_top10` match `evaluation_metrics.json` within
      6e-5; the collector checks this) and `ModelMetrics`. Never "Approved" (WARN).
- [x] Note for Lab 5: a model saved by XGBoost 3.x may not load in the 1.7 container.
      Save in JSON (`model.json`) and check compatibility then, not now.
- [x] Only register when `evaluate_model` passed (it raises otherwise). Register from
      the **final graded run only** (a `--register` flag, off for the dev runs): that run
      is simultaneously the one tee'd to `train-final.log`, the one whose
      `evaluation_metrics.json` lands in `docs/lab3-evidence/`, one of the >= 3 MLflow
      runs, and the registered version. One chain of numbers.

### 1h. Local end-to-end check before paying for anything
- [x] `python churn_training_skeleton.py --local-data-path .dryrun/features.csv
      --artifacts-bucket placeholder --run-name local-smoke` runs clean with no
      `--mlflow-arn` (tracking skipped) and no `--register` (registry skipped; the flag
      is on only for the final graded run in Phase 4). Confirm `./output/` now holds
      `evaluation_metrics.json`, `slice-evaluation.csv`, `confusion-matrix.png`,
      `feature-importance.png`, the same four files the collector expects in
      `docs/lab3-evidence/`.
- [x] Metrics land near the reference table; CI excludes zero; both thresholds pass.

## Phase 2 - Track B or C code (FREE to write; Bedrock calls cost cents)

### Track C (not chosen; kept for reference only)
Since 2026-10-12 Track C is Amazon Bedrock AgentCore (Harness + Gateway + Cedar policy)
deployed from `infrastructure/environments/agent/` on the Terraform 6.x provider, with
the kit's `agent/` package doing the tool, trace and cost logging. The earlier
hand-rolled ReAct-loop plan is obsolete and was removed from this file. Nothing from
the Track C kit was copied into the repo.

### Track B: `rag/` (offer generation)

**Status 2026-10-15 afternoon: Phase 2 complete.** Harness run 2x for `before` (profile
out of contexts) and `after` (profile in), plus strategies A/B/C once each; no NaN.
`after`: faithfulness 0.554 (0.552-0.556), answer relevancy 0.391, context recall 0.671;
`before` faithfulness 0.358. Reference ranges met. Evidence written:
`docs/lab3-evidence/rag_eval.csv` (16 rows), `rag_eval_strategies.csv`,
`track-b-examples.md`, report section 2 of `docs/lab3-evaluation-report.md`,
`notebooks/lab3-demo.ipynb` executed (counts 1..7). Fixes along the way: the one NaN was
the judge's reply hitting langchain's default max_tokens (not throttling), so
`build_bedrock_judge` now sets `max_tokens=4096`; banned filler phrases moved into the
user turn (Haiku ignored them in the system prompt); the store count came out of the
persona line because the model kept quoting it. Bedrock spend today about $1.50.
Previous status (2026-10-14 evening): pipeline built and generating; harness runs not yet done.**
`rag/corpus.py` (section chunks, 48; fixed-window alternative kept for comparison),
`rag/bedrock.py` (Titan V2 + Haiku Converse, throttling retry, usage counters),
`rag/index.py` (FAISS inner-product over normalised vectors, cached in `rag/index/`),
`rag/retrieve.py` (A, B, C, AB), `rag/generate.py` (prompt assembly + profile
interpretation), `rag/pipeline.py` (`OfferPipeline.invoke`, `--no-profile` switch),
`rag/run_eval.py` (N runs, NaN re-judge of the same answer, per-run + combined CSV,
summary md/json with judge named and generator cost), `rag/prompts/` (system prompt,
user prompt, corrected tier guidelines with the 3 errors named). Harness main's
placeholder is wired to the pipeline. First offers showed the instructor's two
failure modes exactly (filler openers, invented "1,000 bonus points"); prompt now
forbids both and restricts incentives to benefits present in the context.
- [x] Fix the **three planted errors** in Template 3 of
      `prompt_templates/offer_generation_prompts.md` against `loyalty-program-terms.md`
      and `shipping-policy.md` and name them in the report:
      1. Gold threshold is $2,000-$4,999.99 (template says to $9,999.99)
      2. Platinum starts at $5,000 (template says $10,000)
      3. "free expedited shipping on orders over $200" exists at no tier
- [x] Fix the two documented weaknesses: tier never reaches the model (Template 1);
      raw numbers with no interpretation (Template 2).
- [x] Chunk by markdown section with doc ID metadata (size/overlap documented), embed
      with Titan V2 (justify vs Cohere), FAISS locally, implement the three retrieval
      queries in Template 4 and measure context recall for each.
- [x] Generation with Haiku 4.5 inference profile; `temperature` or `topP`, not both.
      Prompt hygiene (each of these is an unsupported claim to the judge): no warm-up
      filler ("We've missed you!"), no invented terms ("use within 14 days"); every
      sentence must trace to a retrieved chunk or to the customer profile.
- [x] Handle the tier-definition gap (feature store = lifetime value, policy =
      trailing 12 months): phrase tier benefits conditionally, say so in the doc.
- [x] Wire `rag_invoke_fn(customer_context, question) -> {"answer", "contexts"}` into
      the harness; `build_bedrock_judge` is already there so no OpenAI key is needed.
      **`contexts` = retrieved chunks + one extra entry
      `"Customer profile: loyalty_tier=Gold; days_since_last_purchase=47; top_categories=..."`**,
      behind a `--profile-in-contexts` flag (default on). The judge cannot see the profile
      otherwise and marks every profile fact in the offer as unsupported (instructor
      measured faithfulness 0.38 -> 0.60 from this alone). The flag is also our
      before/after experiment.
- [x] Runner `rag/run_eval.py` around `RAGEvaluator` (which already returns per-case rows
      via `scores.to_pandas()`): run all 4 cases N times (default 2; the handout says 2-3
      and saw about +/-0.1 on faithfulness between identical runs); keep every per-run
      CSV; write a summary with per-case scores, per-metric mean and min-max range, and
      the judge model ID (`us.anthropic.claude-haiku-4-5-20251001-v1:0`). **NaN = a
      failed judge call (Bedrock throttling), not a zero**: re-run that case (bounded
      retries), never average it in or report it. Concatenate the runs with a `run`
      column into `docs/lab3-evidence/rag_eval.csv` (collector wants >= 4 rows).
- [x] Before/after experiment (4 pts): profile NOT in contexts (2 runs) vs profile in
      contexts (2 runs). Report both tables and say whether it helped. Honest measurement
      earns full marks either way. Result: faithfulness +0.196 (0.358 -> 0.554, ranges do
      not overlap), answer relevancy +0.018 (noise), context recall unchanged. Report 2.4.
- [x] Per-metric limits (6 pts), seeded from the handout, confirmed on our own per-case
      output and cited by case: **context recall** ceiling 0.61-0.67 because ground
      truths contain grading commentary the corpus never states (case 3 "a heavy
      percentage discount destroys margin", case 2 "proportionate to a $312 lifetime
      value", case 4 "feature store tier is derived from lifetime value"); **answer
      relevancy** reverse-generates questions from the answer and compares them to the
      original, but our "questions" are instructions and the answers are marketing copy,
      ceiling about 0.73; **faithfulness** is the one we can move, and it penalises
      profile facts unless the profile is a context. Written as report 2.3, citing the Gold
      redemption/expiration hallucination, the Bronze 0.286-with-no-errors case, Platinum
      recall stuck at 0.333 under every strategy, Silver recall 1.0/0.5 on identical contexts.
- [x] Reference ranges to sanity-check against (instructor pipeline, 2026-10-12):
      faithfulness 0.47-0.68, answer relevancy 0.38-0.45, context recall 0.53-0.58.
      If ours sit there, the pipeline is fine. A stronger judge does not change them.
      Ours (after): 0.554 / 0.391 / 0.671, inside on the first two, at the recall ceiling.
- [x] `docs/lab3-evidence/track-b-examples.md`: 5 correct input/output pairs + 2
      documented failure cases. For each: customer profile, generated offer, IDs of the
      retrieved chunks. For each failure: the fix we propose. Record the judge model.
- [x] Track B section of `docs/lab3-evaluation-report.md`: the two run tables, mean and
      range, judge, per-metric limits, before/after with judgement.
- [x] `notebooks/lab3-demo.ipynb`: load the index, run the 4 cases, show one
      before/after. Execute with **Kernel > Restart & Run All** in the venv, save, commit
      with outputs. The collector fails it without outputs and warns if execution counts
      are not exactly 1..N. Built and executed headlessly by nbclient (see
      scratch `build_notebook.py` flow: nbformat cells -> NotebookClient.execute with cwd =
      repo root); 7 code cells, counts 1..7, no errors; reads the two committed evidence
      CSVs so it makes only 5 generator calls. Re-execute the same way if rag/ changes.

## Phase 3 - Docs (FREE)

- [ ] `docs/lab3-evaluation-report.md` (new file, required since 2026-10-12; the
      collector checks it exists). Section 1, Task 1 churn evaluation: metric table (AUC,
      P@10, R@10, baseline AUC, lift + 95% CI, XGBoost version, n_val) matching
      `evaluation_metrics.json` exactly | confusion matrix (embed
      `lab3-evidence/confusion-matrix.png`) | feature importance (embed
      `lab3-evidence/feature-importance.png`) | slice table from
      `lab3-evidence/slice-evaluation.csv` with n and positives per tier, weakest tier
      discussed, too-small tiers called out | MLflow experiment summary (3 runs, what
      varied, what it did). Section 2, Track B evaluation (Phase 2 bullets above).
      **2026-10-15: file created; section 2 (Track B) complete; section 1 is a scaffold with
      the local dry-run numbers in an HTML comment, to be replaced from the Phase 4 final run.**
- [x] `docs/lab3-model-design.md` (~700 words, not counting the track line; the
      evaluation tables now live in the report and are cross-referenced, not repeated):
      Churn model: why gradient boosting for ~10k rows, 13 tabular features,
      interpretability for the retention team; what would make you switch | What the
      features bought: baseline vs full with the headline numbers, which features were
      decorative | LLM system: RAG vs fine-tuning, primary production risk | Failure
      cases and mitigations (summary; detail in `track-b-examples.md`) | With 10x time
      or data. **2026-10-15: written from the local xgb-shallow numbers (HTML comment
      marks them for refresh after Phase 4); names the behavioural-feature ablation as
      the next experiment.** Ablation run 2026-10-16 (`experiments/ablation.py`): behavioural
      pair -0.0054 AUC, purchase_frequency_180d -0.0005, tenure -0.0012; doc updated.
- [x] README: Lab 3 section covering how to train (local and the optional spot job),
      how to evaluate, where artifacts land (`artifacts/models/`, `artifacts/mlflow/`,
      `artifacts/athena-results/`, Model Registry group), how to run the harness and
      the evidence collector, and what `docs/lab3-evidence/` contains. **2026-10-15 done:
      layout entries, 'Lab 3: model development' section, evidence paragraph.**
- [~] Commit everything so far (no AWS evidence yet). Phase 2 committed by Boston as
      d3d8fbc "phase 3" (2026-10-15 15:31); the Phase 3 files (README, design doc, this
      file) are still uncommitted.

## Phase 4 - AWS session (**COSTS MONEY** - one sitting, same-day teardown)

Ask before step 1. Expected wall clock 60-90 min; expected spend $1-2.

**Status 2026-10-16 17:40: steps 1-9 done in one sitting (17:11-17:45).** Apply 53 resources
in 3 min (`docs/lab3-apply-output.txt`); pipeline 3/3 SUCCEEDED, 9,999 rows
(`docs/lab3-pipeline-output.txt`); offline store lagged ~4 min behind the Glue job before
Athena saw rows (poll `features/offline-store/` for Parquet first); MLflow App
`northstar-mlflow` arn `...mlflow-app/app-ARLX53NAWKLU` (`docs/lab3-mlflow-app-arn.txt`),
Created in under 5 min; 4 runs (xgb-baseline 0.7818, xgb-shallow 0.7835, xgb-plus-risk
0.7787, xgb-final 0.7835 registered as northstar-churn-models v1 PendingManualApproval);
every number reproduced the local dry-run to 4 dp; collector 25 OK / 0 WARN / 0 FAIL first
try; teardown-lab3 all OK; teardown-lab2 run (output copied to
`docs/lab3-destroy-output.txt`, Lab 2's file restored from git). Report section 1 written
from the exported files. Step 10 (commit, push) is Boston's.

1. **[$] Re-deploy the Lab 2 stack.** `terraform plan -out=tfplan` in
   `infrastructure/environments/dev`, read the plan, then `terraform apply tfplan`
   (the classifier blocks `-auto-approve`). Starts the NAT meter (~$0.05/hr).
   Keep the Lab 2 evidence files untouched; tee this apply to
   `docs/lab3-apply-output.txt`.
2. **[$] Populate the Feature Group.** `bash scripts/run-lab2-pipeline.sh` (crawler ->
   transform -> feature-engineer, ~6 min, ~$0.25). Confirm 9,999 records; **run it once**.
3. **[free, one-time, keep forever] Create the MLflow App.**
   ```bash
   BUCKET=$(terraform -chdir=infrastructure/environments/dev output -raw s3_bucket_name)
   ROLE=$(terraform -chdir=infrastructure/environments/dev output -raw ml_engineer_role_arn)
   aws sagemaker create-mlflow-app --name northstar-mlflow \
     --artifact-store-uri s3://$BUCKET/artifacts/mlflow/ --role-arn $ROLE --query Arn --output text
   ```
   Poll `describe-mlflow-app` until `Created` (~5 min). Export `MLFLOW_APP_ARN`.
   Note: the artifact store sits in the data bucket that Lab 2 teardown destroys.
   Params and metrics live in the App itself, so `search_runs` keeps working; Lab 4's
   apply recreates the bucket with the same name.
4. **[~$0] Training runs through Athena.** The three runs from Phase 1f as dev runs
   (`--output-data-dir ./output`, no `--register`), each `--mlflow-arn $MLFLOW_APP_ARN`,
   names and bucket from `terraform output`. Then the **final graded run** with the best
   configuration:
   ```bash
   python churn_training_skeleton.py <same args as the best run> --register \
     --output-data-dir docs/lab3-evidence 2>&1 | tee docs/lab3-evidence/train-final.log
   ```
   That one run produces `train-final.log` (with the `Athena QueryExecutionId:` and
   `Training XGBoost with params:` lines), `evaluation_metrics.json`,
   `slice-evaluation.csv`, `confusion-matrix.png`, `feature-importance.png`, a fourth
   MLflow run, and the registered version. Same rounded numbers in all of them.
5. **[free] Verify the registration.**
   `aws sagemaker list-model-packages --model-package-group-name northstar-churn-models`
   shows exactly one `PendingManualApproval` version. (The collector exports it in
   step 8.5; no separate txt file needed.)
6. **[free] MLflow check.** Console: SageMaker > MLflow > the App > `northstar-churn`
   shows 4 runs with params and metrics. (Collector exports `mlflow-runs.csv`.)
7. **[$ cents] Track B evaluation.** `python rag/run_eval.py` for the before and after
   configurations (4 harness runs total, roughly $0.50-1.50 in Bedrock). Copy the
   combined CSV to `docs/lab3-evidence/rag_eval.csv`; write `track-b-examples.md` and the
   Track B section of the evaluation report from the per-case output. Execute
   `notebooks/lab3-demo.ipynb` with Restart & Run All and save. (Bedrock does not need
   the Lab 2 stack, so this step can also happen before step 1 on another day.)
8. **Console tour (learning, not graded):** SageMaker > MLflow (the App, its
   experiments and runs), SageMaker > Model Registry (the group, the pending package),
   Athena query history (find the QueryExecutionId from the log), Bedrock > model
   catalog. Nothing to click that creates resources.
8.5. **[free, BEFORE any teardown] Collect the evidence.**
   ```bash
   python scripts/collect_lab3_evidence.py --track B 2>&1 | tee docs/lab3-evidence/collect-output.txt
   ```
   Fix every FAIL and re-run until the summary shows 0 FAIL; read every WARN. It
   exports `account.json`, `athena-query.json`, `mlflow-app.json`, `mlflow-runs.csv`,
   `model-registry.json`. It must run while the Lab 2 stack is still up: the Lab 2
   teardown destroys the bucket that holds the model artifacts, MLflow artifacts and
   Athena results.
9. **[stops the meter] Teardown, in this order:**
   `bash scripts/teardown-lab3.sh` -> `docs/lab3-teardown-output.txt` must show every
   check OK (including the four AgentCore checks, which read 0 for us) and **zero
   tracking servers**; then `bash scripts/teardown-lab2.sh` (destroys NAT, Domain, Glue,
   Feature Group, bucket). Keep the MLflow App (the script skips it on purpose). Re-run
   the read-only checks: no NAT, no endpoints, no tracking server.
10. Commit everything under `docs/` and `notebooks/` plus the code, then push.

## Phase 5 - Submit

- [ ] `bash scripts/check-secrets.sh`
- [ ] `aws sagemaker list-endpoints` empty, `list-mlflow-tracking-servers` empty
- [ ] `git tag lab3-submit && git push origin lab3-submit`
- [ ] Paste the repo URL into Canvas

---

## Rubric cross-reference

Evidence column added 2026-10-12. Paths are under `docs/lab3-evidence/` unless noted.
Missing evidence files cost no points this term (screen-share fallback), but the
collector must report 0 FAIL for a no-follow-up grade.

| Points | Item | Closed by | Evidence |
|---|---|---|---|
| 5 | Athena query against the offline store; the graded run used it, not `--local-data-path` | Phase 1a, Phase 4 step 4 | `train-final.log` (QueryExecutionId line), `athena-query.json` (exported) |
| 8 | P@10 >= 0.50, R@10 >= 0.25 | Phase 1d, Phase 4 step 4 | `evaluation_metrics.json` matching the report |
| 7 | Beats recency baseline, 95% CI excludes 0 (point estimate alone = 3) | Phase 1d | `evaluation_metrics.json` (`baseline_auc`, `lift_ci_low/high`) + report table |
| 5 | >= 3 MLflow App runs with params + metrics; one of them is the graded run | Phase 1f, Phase 4 steps 3-6 | `mlflow-runs.csv`, `mlflow-app.json` (exported) |
| 5 | Model Registry, PendingManualApproval, graded run's metrics in metadata | Phase 1g, Phase 4 step 4-5 | `model-registry.json` (exported) |
| 5 | Slice eval per tier with n; weakest tier discussed; too-small tiers flagged | Phase 1e | `slice-evaluation.csv` + report discussion |
| 10 | LLM system runs end to end: notebook ran top to bottom in one clean run | Phase 2 notebook, Phase 4 step 7 | `notebooks/lab3-demo.ipynb` with outputs |
| 15 | Evaluation: harness >= 2x with per-case/mean/range + judge named (5); per-metric limits cited (6); one before/after change (4) | Phase 2 runner + report, Phase 4 step 7 | `rag_eval.csv` + report section 2 |
| 10 | >= 2 failure cases with fixes | Phase 2 examples file | `track-b-examples.md` |
| 6+6+5+3 | Design doc (~700 words; evaluation report is a separate file) | Phase 3 | `docs/lab3-model-design.md`, `docs/lab3-evaluation-report.md` |
| 3 | Training script fully parameterized | Phase 1f (no literals) | code + `Training XGBoost with params:` line in `train-final.log` |
| 2 | Lab 1-2 infra reused | `terraform output` everywhere | code + `mlflow-app.json` (MLEngineer role, `artifacts/` prefix) |
| 2 | Nothing left running | Phase 4 step 9 | `docs/lab3-teardown-output.txt` |
| 2 | Bedrock verification file | already in `docs/` | `docs/bedrock-access-verification.txt` |
| 1 | README | Phase 3 | `README.md` |
| gate | `docs/lab3-teardown-output.txt` | Phase 4 step 9 | same |
| check | Collector 0 FAIL, numbers agree across json / MLflow / registry | Phase 4 step 8.5 | `collect-output.txt`, `account.json` |

## Gotchas collected from the handout and kit

- Two MLflow products. App = free. Tracking Server = $0.60/hr. Tutorials describe the
  server. Instance-size prompt means wrong product.
- `pip install mlflow sagemaker-mlflow` both; the second is the SigV4 plugin, and
  without it the error never mentions credentials.
- Athena runs under our IAM user (AdministratorAccess), not MLEngineer. MLEngineer has
  no Athena/Glue/DescribeFeatureGroup by design; a query from inside a training
  container as MLEngineer gets AccessDenied.
- `event_time` is epoch seconds (Fractional). ISO strings in the query -> TYPE_MISMATCH.
- Offline store is append-only; dedup by `customer_id` with the window function. A
  global `MAX(write_time)` filter is wrong.
- `ORDER BY customer_id` or the split (and every metric) changes run to run.
- `eval_metric` order: XGBoost early-stops on the LAST metric; keep `"auc"` last.
- `scale_pos_weight` computed from the training split (3.545 on reference), logged as
  the resolved value.
- No absolute AUC gate; the lift CI is the gate. Paired bootstrap, resample rows once
  per replicate and score both models on them.
- Reference metrics are XGBoost 3.2.0; the SageMaker container is 1.7 and gives
  slightly different numbers. Log the version.
- Model package group name must be exactly `northstar-churn-models` (Lab 5).
- Bedrock: inference-profile IDs (`us.` prefix) only; Claude 3 Haiku is refused;
  `temperature` and `topP` are mutually exclusive on Claude 4+; Bedrock Agents
  (managed) is closed to new accounts, build the loop by hand.
- Track B pins: `ragas datasets "langchain-community<0.4" langchain-aws`; RAGAS needs
  `llm=` and `embeddings=` or it looks for an OpenAI key (harness already handles it).
- `scripts/teardown-lab3.sh` sums paginated counts and treats a failed AWS call as a
  failed check; it writes `docs/lab3-teardown-output.txt` itself. The 2026-10-12 version
  adds four `bedrock-agentcore-control` checks (need CLI >= 2.36; ours returns 0 on all
  four) and a Track C `terraform destroy` that skips itself for us.
- Evidence (2026-10-12): the collector runs **before** teardown, from the venv via
  `python scripts/collect_lab3_evidence.py` (the `.sh` wrapper's `python3` is not the
  venv on this machine). The final graded run must be a tracked MLflow run and the
  registered version, with identical rounded metrics (tolerance 6e-5) in
  `evaluation_metrics.json`, `metrics.auc_roc`, and `CustomerMetadataProperties`.
  `train-final.log` must contain `Athena QueryExecutionId:` and must not contain
  `Loading features from local file`.
- Demo notebook must be committed with outputs from one Restart & Run All; execution
  counts other than 1..N draw a WARN.
- RAGAS (2026-10-12): a NaN cell is a throttled judge call, re-run it, never a zero. The
  customer profile must be passed as a context or every profile fact is "unsupported".
  Expect roughly +/-0.1 on faithfulness between identical runs; context recall cannot
  exceed about 0.67 and answer relevancy about 0.73 on this harness, by design defect.
- From Lab 2: the ON_DEMAND Glue trigger fires during apply if enabled; `aws logs`
  paths in Git Bash need `MSYS_NO_PATHCONV=1`; use `plan -out` then `apply <file>`.
