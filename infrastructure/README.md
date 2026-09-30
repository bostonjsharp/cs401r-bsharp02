# infrastructure/ - Terraform for the NorthStar platform

Started as the Lab 1 Part B skeleton; now holds the full Lab 1 + Lab 2 stack.

Verify it starts clean before you add anything:

```bash
cd environments/dev
terraform init
terraform fmt -check -recursive ../..   # no output = pass
terraform validate                      # exits 0
```

Both must still pass when you submit — that is 5 of the 15 points in B1.

## Layout

```
modules/vpc/            aws_vpc, aws_subnet x2 (public, private), aws_internet_gateway,
                        aws_eip + aws_nat_gateway (Lab 2), aws_route_table x2, aws_route,
                        aws_route_table_association x2, aws_vpc_endpoint (S3), aws_security_group
modules/storage/        aws_s3_bucket + public_access_block, versioning,
                        server_side_encryption_configuration, aws_s3_object x4,
                        aws_s3_bucket_lifecycle_configuration (Lab 2, 5 rules)
modules/iam/            three roles (MLEngineer; DataEngineer + ModelMonitor in Lab 2), each with
                        one aws_iam_policy and one attachment; a time_sleep for IAM propagation
modules/sagemaker/      aws_sagemaker_domain (VpcOnly, private subnet), aws_sagemaker_user_profile
modules/glue/           (Lab 2) aws_glue_catalog_database, aws_glue_classifier, aws_glue_crawler,
                        aws_security_group, aws_glue_connection, aws_s3_object x3,
                        aws_cloudwatch_log_group x4, aws_glue_job x2, aws_glue_workflow,
                        aws_glue_trigger x3
modules/feature_store/  (Lab 2) aws_sagemaker_feature_group
```

Lab 2 wiring: `environments/dev/main.tf` calls all six modules; `environments/local`
calls vpc, storage, and iam with the NAT, S3 endpoint, and lifecycle rules disabled.
See the repository README for how to run the pipeline.

Each module contains **only** its designated resources — that is graded.

## The rule that catches people

**No hardcoded names.** The rubric runs:

```bash
grep -rn '"northstar-dev"' infrastructure/modules/
```

and expects nothing. Build names from `var.project` and `var.environment`
(`"${var.project}-${var.environment}-data"`), and give every variable a
`description` — that is also graded.
