## ADR-001: NorthStar Platform Foundation

### Status
Accepted

### Context

NorthStar Retail is building one AI platform to host three systems: a weekly batch churn model, a real-time RAG offer generator, and an agentic customer-service system. The business case is a $128.5M annual churn problem (2.1M customers x 18% churn x $340 LTV); the first deliverable is a churn model that must score by Monday 6 AM ET weekly. Before anything can be trained, the platform needs somewhere to run notebooks, somewhere to keep data, and rules about who may touch it.

Those rules cannot be bolted on later. The platform will ingest customer PII subject to GDPR and CCPA, and the offer system's credit component falls under FCRA/ECOA. If raw customer data and trained models share one bucket and one all-powerful role, there is no way to later prove the training identity never read raw PII. Lab 1 therefore fixes the storage tiers and identity boundaries now, with one role and zero data, so later labs inherit rather than retrofit them. The foundation must also cost nearly nothing idle: the account has $200 of credit for six months.

### Decision

**Network.** One VPC, `northstar-dev-vpc` (10.0.0.0/16), with a single public subnet `northstar-dev-public-1` (10.0.100.0/24) in us-east-1a, an attached Internet Gateway, and a route table sending 0.0.0.0/0 to it. Studio runs in that subnet behind `northstar-dev-sagemaker-sg` (inbound from 10.0.0.0/16 only, all outbound) with `PublicInternetOnly` access, reaching S3 and ECR through the IGW. Starting at .100 leaves low ranges free for Lab 2's private subnets.

**Storage.** One bucket, `northstar-dev-data-829485866627`, with four prefixes: `raw/`, `processed/`, `features/`, `artifacts/`. Versioning on, SSE-S3 on, all public access blocked. The prefixes are the data lifecycle: POS and Shopify exports land in `raw/`, cleaned tables in `processed/`, the weekly churn feature set in `features/`, models and evaluation output in `artifacts/`. Versioning exists because the churn model retrains weekly; a retrain that silently overwrote last week's features would make a bad Monday score impossible to diagnose.

**Identity.** One role, `northstar-dev-MLEngineer`, trusted only by `sagemaker.amazonaws.com`. Its policy grants SageMaker training/endpoint/registry actions, Studio self-service scoped to Studio resource types, object read/write on `features/*` and `artifacts/*` only, CloudWatch Logs write, and ECR pull. `raw/` and `processed/` are denied by omission. This persona trains the churn model: features in, artifacts out, nothing else. Keeping raw PII out of its reach is the platform's first concrete GDPR/CCPA control, enforced by policy shape rather than convention.

**Development environment.** One SageMaker Studio domain, `northstar-dev-domain`, IAM auth, attached to the VPC, subnet and security group above, with `northstar-dev-MLEngineer` as default execution role and a single `MLEngineer` profile. Default kernel is ml.t3.medium ($0.05/hour), the cheapest Studio instance. Notebook output sharing is disabled so rendered notebooks never leave the prefix model.

### Consequences

#### What this makes easy
- **Adding roles without moving data.** Lab 2's DataEngineer gets `raw/` and `processed/`; ModelMonitor gets read on `artifacts/`. Each is a new policy against prefixes that already exist.
- **Auditing PII exposure.** "Who can read raw customer data?" is answered by grepping policies for `raw/*`. Today: none.
- **Cheap idle state.** With Studio stopped the foundation costs about $0.30/month (EFS home directory plus kilobytes in S3), so the $200 credit goes to training compute.
- **Rebuilding from scratch.** Every name derives from `project` and `environment`; the same modules produce `northstar-local-*` on LocalStack.

#### What this makes harder
- **Studio has a public route.** S3 traffic leaves via the IGW and re-enters AWS. Nothing inbound is permitted, but a compromised notebook could exfiltrate anywhere. Lab 2 moves Studio to a private subnet with a NAT gateway and an S3 gateway endpoint.
- **Single AZ.** A us-east-1a outage takes Studio down. Fine for dev; not for the customer-service agent's 99.5% availability target.
- **Prefix-level IAM is coarse.** It cannot express "Canadian customers only." GDPR data-subject requests will need a catalog layer, not S3 prefixes.
- **The provided policy has two defects the console build hid.** `sagemaker:RegisterModel` is not a real IAM action (kept as specified at course staff's request; Lab 3 will need `CreateModelPackage`), and the Studio UI's "Create space" needs `sagemaker:AddTags`, which the policy omits. Part A never noticed because the console wizard attached `AmazonSageMakerFullAccess`; the strict Terraform role exposed it, and `AddTags`/`ListTags`/`DeleteTags` were added scoped to Studio resources.

#### What would cause you to revisit this decision
- The first real customer record landing in `raw/` — the public-subnet placement must be gone by then.
- A second data-consuming team. Two teams sharing one bucket with prefix IAM works; five with overlapping needs argues for a bucket per domain.
- Foundation cost above roughly $30/month, which would mean a Studio space left running or EFS growth this design does not monitor.

### Alternative Considered

**Four buckets instead of four prefixes** (`northstar-dev-raw`, `-processed`, `-features`, `-artifacts`) with bucket-level IAM. This is workable and easier to audit: bucket policies are simpler than prefix ARNs, and a wildcard mistake cannot expose a sibling stage. It was rejected because the churn pipeline moves data between stages weekly: cross-bucket copies cost more, the 24-month retention rule would live in four places, and four times as many globally unique names are needed across dev/staging/prod. One bucket with strict prefix policies gives the same isolation with one retention policy and one access log.

### AWS Service Selection
- **Networking isolation model:** Amazon VPC with one public subnet and security-group scoping, because it gives Studio a private address space that Lab 2 can tighten without recreating the environment.
- **Storage design:** Amazon S3 with stage prefixes, versioning and SSE-S3, because the churn model's inputs and outputs are batch files and every SageMaker service reads S3 natively.
- **Identity model:** IAM roles with service-principal trust and prefix-scoped policies, because roles give Studio short-lived credentials with no keys to leak, and the policy shape encodes the data-tier boundaries.
- **ML development environment:** SageMaker Studio, because it runs as the MLEngineer role inside the VPC, feeds the training jobs and registry later labs use, and bills only while a space runs.
