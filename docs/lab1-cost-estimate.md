# Lab 1 Monthly Cost Estimate - NorthStar Platform Foundation (dev)

Region: us-east-1. Prices are on-demand list prices from the AWS Price List
API as of 2026-09-18 (SageMaker rate confirmed via the pricing API; others
from the AWS Pricing Calculator). Estimate is steady state for a single
developer using the dev environment during the semester, with Studio shut
down at the end of every session.

| Component | Monthly Estimate | Key Assumptions | One Optimization |
|---|---|---|---|
| SageMaker Studio | **$2.65** | JupyterLab space on ml.t3.medium at **$0.05/hr**, 2 hrs/day x 22 working days = 44 hrs = $2.20. Plus the space's 5 GB gp3 EBS volume at $0.08/GB-mo = $0.40 while the space exists, and the Domain's EFS home directory (~150 MB of notebooks) at $0.30/GB-mo = $0.05. The Domain itself and the user profile are free. | Enable the Studio idle-shutdown lifecycle configuration (60 min idle). Cuts forgotten-session hours: if one 8-hour overnight leak per month is prevented, that alone saves 8 x $0.05 = $0.40/mo; it also caps the worst case ($36/mo for a space left running all month). |
| S3 storage | **$0.24** | 10 GB in the data bucket (raw + processed + features + artifacts for the simulated NorthStar datasets) at **$0.023/GB-mo** = $0.23, plus ~5,000 PUT/GET requests = $0.01. Versioning roughly doubles storage for objects that are rewritten weekly; the 10 GB figure already includes one prior version of the features set. | Add a lifecycle rule expiring noncurrent versions after 30 days. Holds versioned overhead to one extra copy instead of unbounded growth. |
| Internet Gateway | **$0.00** | The IGW has no hourly charge. Data transfer OUT to the internet is $0.09/GB after the first 100 GB/month, which is free across the account. Studio pulling container images and reading S3 is traffic INTO the VPC, which is free. Estimated outbound: ~2 GB/month (notebook UI traffic), within the free tier. The handout's placeholder rate of $0.01/GB would give $0.02. | None needed at this scale. Lab 2's S3 gateway endpoint keeps S3 traffic off the IGW entirely, which matters once data volumes grow. |
| DynamoDB (state lock) | **$0.00** | `northstar-tfstate-lock`, on-demand. One lock/unlock pair per plan or apply; ~40 Terraform runs/month = ~80 write requests at $1.25 per million = $0.0001. Table storage is one item, < 1 KB. | Nothing to optimize; Terraform 1.10+ can use S3-native locking (`use_lockfile = true`) and drop the table entirely, saving $0.00 but one resource. |
| S3 state bucket | **$0.00** | `northstar-tfstate-829485866627`, versioned. Lab 1 state file is ~30 KB; with 40 versions retained that is ~1.2 MB at $0.023/GB-mo = $0.00003. ~80 requests/month = $0.00. | Lifecycle rule expiring noncurrent state versions after 90 days keeps this at zero as later labs grow the state file. |
| **Total** | **$2.89 / month** | $0.45/month of that is fixed (EBS volume + EFS) whether or not Studio is opened; the remaining $2.44 scales with Studio hours and data volume. | |

## Notes

- **Where the money actually goes.** ~76% of the estimate is Studio compute
  hours ($2.20 of $2.89), and ~92% is the Studio line overall. Everything the Terraform in this lab creates - VPC, subnet, IGW,
  route table, security group, S3 configuration, IAM role and policy, the
  Domain and user profile - is free to have sitting idle. That is what makes
  `terraform apply` / `terraform destroy` cheap to repeat.
- **What this does not include.** Training jobs, endpoints, and the NAT
  gateway arrive in later labs. A NAT gateway alone is $0.045/hr =
  $32.40/month before data processing, which will become the largest fixed
  line item the moment Lab 2 adds it.
- **Against the $200 credit.** At $2.89/month the foundation would consume
  about $17 over six months, leaving ~$183 for training and inference.
- **Quantified optimization (rubric item).** The idle-shutdown lifecycle
  config is the one worth doing now. Realistic saving: 4 to 8 hours of
  forgotten Studio time per month, $0.20 to $0.40 (7-14% of the estimate).
  More importantly it bounds the failure mode: without it, one forgotten
  space costs $1.20/day and $36/month, roughly 13x the entire planned spend.
