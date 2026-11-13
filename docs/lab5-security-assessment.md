# Lab 5: Security, Privacy and Compliance Assessment

NorthStar Retail churn model, CS 401R, repository `cs401r-bsharp02`. Scope: the churn
prediction system as built in Labs 1-5: the data bucket `northstar-dev-data-829485866627`
(prefixes `raw/`, `processed/`, `features/`, `artifacts/`, `datacapture/`), the Glue pipeline,
the Feature Group `northstar-dev-customer-features`, the CI build, the Model Registry group
`northstar-churn-models`, and the real-time endpoint `northstar-churn-prod`.

Every statement about what the platform does today cites the file that makes it true.
Where a control is recommended but not built, the text says so.

The three IAM roles referred to throughout (`infrastructure/modules/iam/main.tf`):

| Role | Assumed by | Can | Cannot (denied by omission) |
|---|---|---|---|
| `northstar-dev-MLEngineer` | SageMaker (training jobs, the pipeline, the endpoint) | Read/write `features/*` and `artifacts/*`; write-only `datacapture/*`; create training jobs, endpoints, model packages | Touch `raw/` or `processed/`; call Glue or Athena; pass any role but itself |
| `northstar-dev-DataEngineer` | Glue, Lambda, SageMaker Feature Store | Read/write `raw/*`, `processed/*`, `features/*`; `PutRecord` to the Feature Group | Write `artifacts/*` (read-only on `artifacts/glue/*`); create training jobs or endpoints |
| `northstar-dev-ModelMonitor` | SageMaker | Read `artifacts/*`; write CloudWatch metrics and alarms | Write to S3 at all; invoke or modify an endpoint; start a job |

---

## Task 3a: STRIDE Threat Model

Eight threats across all six STRIDE categories. "Role boundary" names the Lab 2 role that
contains the blast radius; "Gap" names what the roles do not close.

| # | Threat | STRIDE Category | Likelihood | Impact | Mitigation | AWS Control |
|---|---|---|---|---|---|---|
| 1 | A long-lived access key of the deploying engineer's IAM user leaks (committed to this public repo, or taken from a laptop) and an attacker deploys, invokes or deletes as that engineer. | Spoofing | M | H | Keep keys out of the repo and make stolen keys short-lived. Built: `.gitignore` excludes `.env`, `*.tfvars`, `*.tfstate`; `scripts/check-secrets.sh` scans every revision; the three service roles have no human principal in their trust policies, so a stolen human key cannot assume them. **Gap:** the human operator still uses a static key with broad rights; none of the three roles limits it. | **IAM Identity Center** with short-lived STS credentials in place of the IAM user; an IAM policy condition `aws:MultiFactorAuthPresent` on `sagemaker:CreateEndpoint*`, `DeleteEndpoint` and `UpdateModelPackage`; the **CodeBuild** `pre_build` bandit scan in `buildspec.yml` (built) fails the build on any HIGH-severity finding in `models/` and `data/`. |
| 2 | A model artifact under `artifacts/` is replaced with a poisoned `model.tar.gz` after evaluation, and the endpoint serves it. | Tampering | L | H | **Role boundary: only `MLEngineer` can write `artifacts/*`.** `DataEngineer` has read-only access to `artifacts/glue/*` and `ModelMonitor` has `s3:GetObject` only, so a compromised Glue job or monitor cannot swap a model. Deployment goes through the registry: `deployment/deploy-canary.sh` refuses any package that is not `Approved`. **Gap:** the package records an S3 key, not a version or hash, so `MLEngineer` itself (any training job) can overwrite an approved artifact in place. | **S3 Versioning** (enabled, `modules/storage/main.tf`) to detect and restore an overwrite; **SageMaker Model Registry** approval status as the release gate; add **S3 Object Lock** (compliance mode) on `artifacts/training/` and record the object version ID in the package metadata. |
| 3 | A compromised training dependency (a malicious PyPI package in the training container) reads customer data and sends it out. | Information Disclosure | M | H | **Role boundary: the training job runs as `MLEngineer`, which has no access to `raw/` or `processed/`.** The transaction-level history never reaches it; the most it can read is `features/` (11 aggregates per pseudonymous `customer_id`). **Gap:** the job is not in the VPC and has outbound internet, so what it can read it can exfiltrate. | **IAM** least privilege on `northstar-dev-MLEngineerPolicy` (statement `S3ArtifactsAndFeatures`); add `VpcConfig` on the training job with the Lab 1 private subnet and an **S3 gateway VPC endpoint** policy limited to the data bucket; `EnableNetworkIsolation` on the serving model. |
| 4 | A caller with invoke rights sends many crafted requests to `northstar-churn-prod` to reconstruct the model (extraction) or to test whether a customer's feature vector was in the training set (membership inference). | Information Disclosure | L | M | The endpoint has no public URL: every call needs a SigV4-signed request from an IAM principal. `ModelMonitor` cannot invoke (its policy has no `sagemaker:InvokeEndpoint`), and neither can `DataEngineer`. The response is a single probability, with no per-feature explanation. **Gap:** no policy names who *may* invoke; today that is anyone with the operator's credentials. | **IAM** policy granting `sagemaker:InvokeEndpoint` on the one endpoint ARN to a dedicated scoring role only; if ever exposed to another team, front it with **API Gateway** usage plans (per-key throttling) and **AWS WAF** rate-based rules. |
| 5 | Data capture stores every request and response in `datacapture/`. Anyone with read access to the bucket reads raw model inputs and churn scores. | Information Disclosure | M | M | Capture is written by `MLEngineer` through a **write-only** statement (`S3DataCaptureWrite`: `PutObject`, `AbortMultipartUpload`, no `GetObject`), so the serving container cannot read captures back. Retention is 7 days plus 1 day for noncurrent versions (`expire-datacapture`, `modules/storage/main.tf`). **Gap:** the bucket uses SSE-S3, so any principal with `s3:GetObject` on the prefix gets plaintext and there is no per-read audit record. | **S3 Lifecycle** rule `expire-datacapture`; **S3 Block Public Access** (all four settings on); move the prefix to **SSE-KMS** with a customer managed key (`DataCaptureConfig.KmsKeyId`) whose key policy lists only the monitoring role for `kms:Decrypt`. |
| 6 | Someone approves an unreviewed model, shifts canary weights, or deletes the endpoint, and later denies it; NorthStar cannot show who did what. | Repudiation | M | M | Every control-plane call (`UpdateModelPackage`, `UpdateEndpointWeightsAndCapacities`, `DeleteEndpoint`) is recorded with the caller's identity in CloudTrail event history; the deployment plan names the approving role; the registry keeps `LastModifiedBy` on each package. **Gap:** event history lasts 90 days and no trail is defined in Terraform, so there is no durable, tamper-evident record and no record of S3 object reads. | **AWS CloudTrail** organization trail to a separate log bucket with log file validation enabled; **CloudTrail data events** for the data bucket's `raw/`, `features/` and `datacapture/` prefixes; **SageMaker Model Registry** approval history. |
| 7 | The production endpoint becomes unavailable: a request flood exhausts one instance, or a compromised training job deletes the endpoint. | Denial of Service | M | H | Load: target-tracking auto-scaling on the champion variant (`deployment/configs/scaling-policy.json`, 1 to 2 instances) and a latency alarm (`rollback-alarm.json`). **Gap the roles do not close:** statement `SageMakerCore` grants `sagemaker:DeleteEndpoint` and `DeleteEndpointConfig` on `Resource "*"`. Training and serving share `MLEngineer`, so any training job can delete the endpoint it has nothing to do with. | **Application Auto Scaling** target tracking; **CloudWatch** alarm; split the role: a deploy-only role holding `CreateEndpoint`/`DeleteEndpoint` scoped to `arn:aws:sagemaker:us-east-1:829485866627:endpoint/northstar-churn-*`, and remove those actions from the training role. |
| 8 | Code running in a training job tries to gain more rights by starting a new job with a more powerful role (for example `DataEngineer`, which holds `glue:*` and `raw/`). | Elevation of Privilege | L | H | **Role boundary: `MLEngineer` can pass only itself.** Statement `PassSelfToSageMaker` allows `iam:PassRole` on its own ARN, conditioned on `iam:PassedToService = sagemaker.amazonaws.com`. It has no `iam:*` write actions and no `sts:AssumeRole`, so a compromised job stays inside the `MLEngineer` boundary. | **IAM** `iam:PassRole` scoped to one role ARN with the `iam:PassedToService` condition key; add an **IAM permissions boundary** on all three roles so a future policy edit cannot widen them past the prefix model. |

**Summary of gaps the three roles do not close** (rows 1, 2, 3, 4, 5, 6, 7): the roles
separate *data prefixes* well and *SageMaker actions* poorly. `MLEngineer` is at once the
training identity, the pipeline identity and the endpoint identity, and its SageMaker
statement is `Resource "*"`. The first fix in priority order is row 7 (separate deploy role),
then row 6 (a durable trail), then row 5 (SSE-KMS on capture).

---

## Task 3b: Data Classification

Tiers: **Public** (already published), **Internal** (no harm to individuals if leaked),
**Confidential** (personal or commercially sensitive; need-to-know), **Restricted** (direct
identifiers or regulated data; named access only).

Encryption today: **every object in the data bucket is SSE-S3 (AES-256)**
(`infrastructure/modules/storage/main.tf`, `sse_algorithm = "AES256"`); no KMS key exists
in the platform. The "Encryption" column is the standard each asset *should* have and why.
The deciding question is the same each time: SSE-S3 encrypts the disk but every principal
with `s3:GetObject` gets plaintext, and no one can see who decrypted what. SSE-KMS with a
customer managed key adds a second, independent authorization (the key policy), a CloudTrail
record of every `kms:Decrypt`, and the ability to revoke access to all copies by disabling
one key. That is worth its cost only where a leak harms a person or the business.

| Asset | Where it lives | Tier | Encryption standard and reason | IAM access policy | Third-party sharing |
|---|---|---|---|---|---|
| **Customer PII** (`customers.csv`: hashed email, `zip_code`, `state`, `age_band`, loyalty data, keyed by `customer_id`) | CRM; `raw/` when ingested. Our Lab 2 sample carries only `customer_id` from this set | **Restricted** | **SSE-KMS**, dedicated key. Direct and quasi-identifiers under GDPR and CCPA: need key-level audit of every read and one switch to cut off access. A SHA-256 email hash is still personal data (it is linkable). Raw email would need **CSE** before upload; the schema already forbids storing it | `DataEngineer` only, on `raw/*`. `MLEngineer` and `ModelMonitor` denied by omission. Key policy: `kms:Decrypt` for `DataEngineer` only | **No.** Only to a processor under a data processing agreement, for the stated purpose; never sold |
| **Transaction history** (`transaction_id`, `customer_id`, dates, amounts, `payment_method`, `store_id`) | `raw/customers/`, `processed/customers/` | **Confidential** | **SSE-KMS**, same key as PII. Every row is linked to a person, and the purchase pattern is what GDPR protects; the key policy keeps the training role out even if an S3 statement is widened by mistake | `DataEngineer` read/write on `raw/*`, `processed/*`. `MLEngineer` has no access (statement `S3ArtifactsAndFeatures` lists neither prefix) | **Conditional.** Aggregated and without `customer_id`: yes. Row-level: processors under contract only |
| **Behavioral clickstream** (`session_id`, events, `search_query`) | Kinesis, then S3. Not in our pipeline; classified as a NorthStar asset | **Confidential**; `search_query` is **Restricted** until masked (customers type email addresses into search) | **SSE-KMS** on the Kinesis stream and the landing prefix. High-volume behavioral profile tied to `customer_id` for 65% of events; free-text PII risk | A streaming-ingest role with write-only access to the landing prefix; `DataEngineer` reads; no ML role reads unmasked events | **Conditional.** The analytics vendor already acts as a processor. Nothing further without masking `search_query` and removing `customer_id` |
| **Product catalog** (12,000 SKUs: names, prices, stock) | PIM; S3 for RAG in Lab 3 | **Internal** (most fields are published on northstar.com) | **SSE-S3.** No personal data and little commercial sensitivity; default encryption meets the at-rest baseline, and KMS would add per-request cost and throttling risk on a high-read asset for no audit benefit | Read for any platform role; write for the ingest role only | **Yes**, to vendors and marketplaces |
| **Model weights** (`model.tar.gz`, XGBoost booster) | `artifacts/training/`; Model Registry | **Confidential** (intellectual property) | **SSE-S3.** The booster holds split thresholds and leaf weights over 11 aggregates, no customer rows or IDs. The real risk is tampering, not reading, and that is handled by versioning and the registry gate (STRIDE row 2), not by a key | `MLEngineer` read/write `artifacts/*`; `ModelMonitor` read-only; `DataEngineer` none (except `artifacts/glue/*`) | **Conditional.** Under NDA to an auditor or a hosting vendor; not published, since a public model invites extraction and inversion attempts |
| **Inference logs** (data capture `.jsonl`: every input vector and score; endpoint container logs) | `datacapture/`; CloudWatch `/aws/sagemaker/Endpoints/northstar-churn-prod` | **Confidential** | **SSE-KMS** (`DataCaptureConfig.KmsKeyId`; a KMS key on the log group). A churn score is a profiling output about a person; today the records carry no `customer_id`, but the 11 values can be joined back to the feature table | `MLEngineer` write-only (`S3DataCaptureWrite`). No role can read today; Lab 6 must grant a monitoring role read on `datacapture/*`. Retention 7 + 1 days | **No.** Not needed by anyone outside the platform team |
| **Feature Store records** (`customer_id`, 11 features, `loyalty_tier`, `churn_risk_score`, `churn_label`) | Online store; offline store at `features/offline-store/`; `features/customers/` | **Confidential** (pseudonymous personal data) | **SSE-KMS** on both stores (`online_store_config.security_config.kms_key_id` and `offline_store_config.s3_storage_config.kms_key_id`). One row per identifiable customer including a predicted behavior; read by two roles, so key-level audit matters | `DataEngineer` writes (`FeatureStoreIngest`, `S3DataPrefixes`); `MLEngineer` reads `features/*` from S3. No role holds `GetRecord` or `DeleteRecord` today | **No** at row level. Aggregate feature statistics may be shared |

---

## Task 4: Privacy Impact Assessment

Regulatory frame from `northstar-scenario-overview.md`: customer PII is subject to GDPR and
CCPA and to NorthStar's 24-month raw-data retention policy. The Chief Privacy Officer owns
this assessment.

### 1. Personal data inventory

`customer_id` (format `CUST-` + 8 digits) is a pseudonymous identifier: it is personal data
because NorthStar's CRM can resolve it to a person.

| Pipeline stage | Location | Personal data present | Written by | Retention |
|---|---|---|---|---|
| Raw ingest | `raw/customers/*.csv` | `customer_id`, `transaction_id`, `purchase_date`, `order_value`, `num_items`, `payment_method`, `channel`, `store_id`, `product_category` (one row per purchase) | `DataEngineer` | 90 days current, 30 days noncurrent (`expire-raw-data`, `expire-raw-versions`); production policy is 24 months |
| Cleaned | `processed/customers/` (Parquet), Glue table `northstar_dev.customers` | Same nine columns, typed and deduplicated | `DataEngineer` (Glue `transform`) | Overwritten each run; old versions 30 days |
| Features | `features/customers/` (Parquet), Feature Group online and offline stores | `customer_id`, `event_time`, 11 behavioral aggregates, `loyalty_tier`, `churn_risk_score`, `churn_label` (one row per customer) | `DataEngineer` (Glue `feature-engineer`) | Offline store is append-only; old S3 versions 60 days |
| Training | Training job's local copy of `features/customers/`; the CI build's staged copy | Same rows; `customer_id` is read but dropped before the model sees it | `MLEngineer`; CodeBuild role | Ephemeral: gone when the job or build ends |
| Trained model | `artifacts/training/.../model.tar.gz`, `evaluation_metrics.json` | No rows and no identifiers; tree parameters learned from all customers; aggregate metrics | `MLEngineer` | Kept indefinitely (no lifecycle rule on `artifacts/`) |
| Inference | Request and response of `northstar-churn-prod` | 11 feature values in, one probability out; no `customer_id` in the payload | Caller | In transit only (TLS) |
| Inference logs | `datacapture/northstar-churn-prod/<variant>/...jsonl` | The same 11 values and the score, with a timestamp and event ID | `MLEngineer` (endpoint) | 7 days, then permanently deleted 1 day later |

### 2. Lawful basis

**Legitimate interests (GDPR Article 6(1)(f)).** The three-part test, applied to NorthStar:

- **Purpose.** Retaining existing customers is a real, lawful commercial interest: churn
  costs $128.5M a year (2.1M customers x 18% x $340), and Recital 47 recognizes marketing to
  one's own customers as a possible legitimate interest.
- **Necessity.** Predicting who will go quiet requires that customer's own purchase recency,
  frequency and spend; no less intrusive data would work. Processing is cut down to 11
  aggregates (section 3), not the raw history.
- **Balancing.** The people scored are existing customers, 1.4M of them loyalty members who
  gave their data in a purchasing relationship, and a retailer using purchase history to send
  a retention offer is within what they reasonably expect. The effect on the individual is
  small and favorable: the output selects the top 10% for an offer; no one is refused a
  product, a price or a service. It is therefore not a decision with legal or similarly
  significant effect under Article 22.

Conditions that keep the balance in NorthStar's favor: the privacy notice states that
purchase history is used to predict churn; any customer may object (Article 21), and an
objection is honored by a suppression list applied before feature engineering; outreach
still follows each customer's CRM contact preferences.

Why not the others. **Contractual necessity** fails because NorthStar can complete a sale
and run the loyalty program without predicting churn; the processing serves NorthStar, not
the performance of the customer's contract. **Consent** would be lawful but is the wrong
tool: it must be freely given and withdrawable at any time, so the training population
would shrink to those who opted in and shift with every withdrawal, and a customer about to
leave is the least likely to opt in. This basis covers the churn model only; the
credit-offer component named in the scenario (FCRA/ECOA) needs its own assessment.

### 3. Data minimization

The model input is the 11 columns of `FEATURE_COLUMNS` (`models/churn/churn_model.py`), all
aggregates over a customer's own purchases. Considered and excluded:

| Data | Decision | Reason |
|---|---|---|
| `customer_id` | Join key only; dropped by column selection in `preprocess_features` | An identifier has no predictive meaning and would let the model memorize individuals |
| `email` (hashed), `zip_code`, `state`, `age_band` | Never ingested into the platform | Demographic and location fields are not needed to measure purchase behavior, and `zip_code` and `age_band` are proxies for protected characteristics |
| `payment_method`, `store_id` | Present in `raw/` and `processed/`, not aggregated into any feature | No expected signal for churn; `store_id` is a location proxy |
| `product_category` values | Reduced to a count (`category_diversity_score`) | What a person buys can reveal sensitive facts; how varied their purchases are is enough |
| `transaction_id`, exact `purchase_date`, exact `order_value` | Reduced to windowed counts, means and recency | The model needs behavior over 30/90/180 days, not individual purchases |
| Clickstream (`search_query`, page views, `session_id`) | Not used | Highest-volume and most intrusive source; `search_query` contains typed-in email addresses; purchase aggregates already reach AUC 0.78 |
| `loyalty_tier` | Stored with features, **not** a model input | Kept only to measure recall per tier in `tests/test_fairness.py` |
| `churn_risk_score` | Stored with features, not a model input | A hand-built heuristic; using it would leak a second model into this one |

Remaining weakness: `payment_method` and `store_id` are still carried through `processed/`
although no consumer uses them. Dropping them in `glue-scripts/transform.py` is a contract
change (`docs/lab2-data-contract.md`, breaking, new prefix) and is recommended.

### 4. Right to erasure workflow

Request: erase `customer_id = C00123456` (in our data the same customer would be written
`CUST-00123456`). GDPR Article 12(3) gives one month. Steps 0 to 4 are run by the Data
Engineer on instruction from the Chief Privacy Officer, who records completion.

**Step 0: stop re-ingestion.** Add the ID to an erasure suppression list and have
`glue-scripts/transform.py` drop listed customers (not built yet; a small change). Without this the next nightly load from the source
systems puts the customer straight back. (The source systems, the CRM and warehouse, erase
under their own procedure and are outside this platform.)

**(a) Raw S3 data.** The customer's purchases are rows inside shared files, so there is no
object to delete.
1. Rewrite each file in `raw/customers/` and `processed/customers/` without the customer's
   rows (re-run the transform with the suppression list; for raw, a one-off filter job as
   `DataEngineer`).
2. The bucket is versioned, so the old file still exists as a noncurrent version. List and
   delete those versions explicitly rather than wait 30 days for the lifecycle rule:
   `aws s3api list-object-versions --bucket northstar-dev-data-829485866627 --prefix raw/customers/`
   then `aws s3api delete-object --bucket ... --key <key> --version-id <id>` for each
   noncurrent version. Repeat for `processed/customers/`.

**(b) Feature Store.** Two stores, two different operations.
- **Online store** (key-value, latest record per customer): one call removes it.
  `aws sagemaker-featurestore-runtime delete-record --feature-group-name northstar-dev-customer-features --record-identifier-value-as-string C00123456 --event-time <now> --deletion-mode HardDelete`
  A later `get-record` returns nothing. A new grant is needed first: no role holds
  `sagemaker:DeleteRecord` today.
- **Offline store** (append-only Parquet under `features/offline-store/`): `DeleteRecord`
  does **not** remove history. It appends a tombstone row (`is_deleted = true`) and every
  earlier row for the customer stays in S3. Erasure means finding the files that hold the
  customer (Athena: `SELECT DISTINCT "$path" FROM <offline table> WHERE customer_id = 'C00123456'`),
  rewriting each without those rows, and deleting the originals **and their noncurrent
  versions** as in (a). The same rewrite applies to `features/customers/`, which the
  training job reads.

**(c) Model training data and the trained model.**
- *Training data.* Training reads `features/customers/` directly, so after (b) the next
  training run no longer contains the customer. Copies made by a training job or a CI build
  are ephemeral. Developer laptops hold a local copy (`.dryrun/features/`), which must be
  refreshed.
- *Trained model.* The booster stores no rows, but its split points and leaf weights were
  fitted on data that included the customer. There is nothing to delete. The step is to
  **retrain**: run the CI pipeline on the cleaned features, approve the new package, release
  it with `docs/lab5-deployment-plan.md`, then set every earlier package to `Rejected` and
  delete its `model.tar.gz` (all versions) from `artifacts/training/`. In practice erasures
  are batched into the next scheduled retrain inside the one-month limit, and the privacy
  office records why residual risk in the interim is low (in production, depth-3 trees fitted
  to aggregates of 2.1M customers do not reproduce one individual's values).

**(d) Inference logs.** Data capture records hold 11 numbers and a score, with no
`customer_id`, so this customer's records **cannot be located** and no targeted delete is
possible. The control is retention: the `expire-datacapture` rule hides every capture object
at 7 days and permanently deletes it 1 day later (the noncurrent-version expiry added as
Lab 5 prerequisite 6), so all inference records that could relate to the customer are gone
within 8 days of the request, well inside one month. Once (b) is done, the feature row
needed to link a captured vector back to the customer no longer exists either.

**Hardest step: the trained model, (c).** Every other store holds the customer's data as
rows, and rows can be found and removed, even where that means rewriting files. A trained
model holds no row to remove. The customer's purchases shifted split thresholds and leaf
weights by some small amount that is spread across hundreds of trees and mixed with every other
customer's contribution; no operation subtracts one person's influence from those numbers.
Deletion is therefore not a delete: it is a full retrain on data that excludes them,
followed by a governed redeployment and the retirement of every model version trained
before the request. It is also the only step that costs compute, takes a release cycle, and
changes what production serves. The offline Feature Store is the runner-up for a related
reason: its history is append-only, so the delete API adds a tombstone rather than removing
anything, and real erasure is a file rewrite.
