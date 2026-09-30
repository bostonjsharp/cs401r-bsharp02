# ── modules/glue ─────────────────────────────────────────────────────────────
# The data pipeline: raw/ -> crawler -> catalog -> transform -> processed/
# -> feature-engineer -> features/ + Feature Store. Everything here runs as
# the DataEngineer role inside the private subnet.
#
# Resource types: aws_glue_catalog_database, aws_glue_classifier,
# aws_glue_crawler, aws_security_group, aws_glue_connection, aws_s3_object
# (scripts and sample data), aws_cloudwatch_log_group, aws_glue_job x2,
# aws_glue_workflow, aws_glue_trigger x3.
#
# Every name is derived from var.project / var.environment / var.dataset.

data "aws_region" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # One dataset name drives every path, so the crawler's table, the transform
  # input, and both job outputs can never drift apart.
  raw_path       = "s3://${var.bucket_name}/raw/${var.dataset}/"
  processed_path = "s3://${var.bucket_name}/processed/${var.dataset}/"
  features_path  = "s3://${var.bucket_name}/features/${var.dataset}/"
  script_prefix  = "artifacts/glue"

  # Scratch space for Spark shuffles. Not under artifacts/: DataEngineer can
  # only read there. Not under processed/<dataset>/: verify counts Parquet
  # files in that prefix.
  temp_dir = "s3://${var.bucket_name}/processed/_glue_temp/"

  # Shared by both jobs. Continuous logging streams driver output to
  # /aws-glue/jobs/logs-v2 while the job runs instead of after it ends.
  common_arguments = {
    "--job-language"                     = "python"
    "--TempDir"                          = local.temp_dir
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-job-insights"              = "false"
  }
}

# ── Catalog ──────────────────────────────────────────────────────────────────

# Catalog database names cannot contain hyphens.
resource "aws_glue_catalog_database" "this" {
  name        = replace("${var.project}_${var.environment}", "-", "_")
  description = "Tables discovered by the raw crawler and registered by ETL jobs"
}

# The built-in CSV classifier only treats the first row as a header when the
# data rows look "sufficiently different" (some column parses as a number).
# The sample data has stray whitespace in numeric columns, which can push
# every column to string and lose the header. Declaring it removes the guess.
resource "aws_glue_classifier" "csv" {
  name = "${local.name_prefix}-csv-header"

  csv_classifier {
    contains_header = "PRESENT"
    delimiter       = ","
    quote_symbol    = "\""
  }
}

# No table_prefix: the table is named after the S3 folder, so it is
# "customers", not "raw_customers". On demand: no schedule block.
resource "aws_glue_crawler" "raw" {
  name          = "${local.name_prefix}-raw-crawler"
  description   = "Discovers the schema of raw/${var.dataset}/ and registers it in the catalog"
  role          = var.data_engineer_role_arn
  database_name = aws_glue_catalog_database.this.name
  classifiers   = [aws_glue_classifier.csv.name]

  s3_target {
    path = local.raw_path
  }

  # Re-crawls update the existing table in place rather than versioning it.
  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }

  tags = {
    Name = "${local.name_prefix}-raw-crawler"
  }
}

# ── Network path into the private subnet ─────────────────────────────────────

# Glue insists on a security group with a self-referencing all-ports ingress
# rule: workers talk to each other through it. A rule written as the VPC CIDR
# is equivalent in effect but does not pass Glue's check; `self = true` does.
resource "aws_security_group" "glue" {
  name        = "${local.name_prefix}-glue-sg"
  description = "Glue workers - self-referencing all-ports ingress, all outbound"
  vpc_id      = var.vpc_id

  ingress {
    description = "All traffic between Glue workers in this group"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "All outbound traffic (S3, Feature Store, catalog via the NAT)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-glue-sg"
  }
}

# A NETWORK connection carries no credentials; it only tells Glue which
# subnet and security group to attach its workers to.
resource "aws_glue_connection" "network" {
  name            = "${local.name_prefix}-vpc-connection"
  description     = "Places Glue workers in the private subnet"
  connection_type = "NETWORK"

  physical_connection_requirements {
    subnet_id              = var.private_subnet_id
    security_group_id_list = [aws_security_group.glue.id]
    availability_zone      = var.availability_zone
  }

  tags = {
    Name = "${local.name_prefix}-vpc-connection"
  }
}

# ── Objects the pipeline needs in S3 ─────────────────────────────────────────

# Job scripts. source_hash (an md5 kept in state) makes an edited script
# re-upload on the next apply; without it Terraform only notices key changes.
resource "aws_s3_object" "transform_script" {
  bucket      = var.bucket_name
  key         = "${local.script_prefix}/${basename(var.transform_script_path)}"
  source      = var.transform_script_path
  source_hash = filemd5(var.transform_script_path)
}

resource "aws_s3_object" "feature_script" {
  bucket      = var.bucket_name
  key         = "${local.script_prefix}/${basename(var.feature_script_path)}"
  source      = var.feature_script_path
  source_hash = filemd5(var.feature_script_path)
}

# The sample dataset lands in raw/<dataset>/ on apply, replacing the manual
# `aws s3 cp` step in the handout. Regenerable synthetic data, so this is safe
# to manage as code; a real feed would arrive from an ingestion process.
resource "aws_s3_object" "raw_sample" {
  count = var.raw_sample_path == null ? 0 : 1

  bucket      = var.bucket_name
  key         = "raw/${var.dataset}/${basename(var.raw_sample_path)}"
  source      = var.raw_sample_path
  source_hash = filemd5(var.raw_sample_path)
}

# ── Logs ─────────────────────────────────────────────────────────────────────

# Glue creates these on first use with no retention and outside Terraform.
# Owning them here sets retention and makes `terraform destroy` remove them.
resource "aws_cloudwatch_log_group" "glue" {
  for_each = var.log_retention_days > 0 ? toset([
    "/aws-glue/crawlers",
    "/aws-glue/jobs/output",
    "/aws-glue/jobs/error",
    "/aws-glue/jobs/logs-v2",
  ]) : toset([])

  name              = each.value
  retention_in_days = var.log_retention_days
}

# ── Jobs ─────────────────────────────────────────────────────────────────────

# raw catalog table -> processed/<dataset>/ Parquet. Transaction level in,
# transaction level out.
resource "aws_glue_job" "transform" {
  name         = "${local.name_prefix}-transform"
  description  = "Casts types, imputes nulls, and deduplicates raw/${var.dataset}/ into processed/${var.dataset}/ Parquet"
  role_arn     = var.data_engineer_role_arn
  glue_version = var.glue_version
  # A failed job does not retry: rerunning a broken transform just bills
  # twice, and the failure is what you need to see.
  max_retries       = 0
  timeout           = var.job_timeout_minutes
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  connections       = [aws_glue_connection.network.name]

  command {
    name            = "glueetl"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.transform_script.key}"
    python_version  = "3"
  }

  default_arguments = merge(local.common_arguments, {
    "--database_name" = aws_glue_catalog_database.this.name
    "--table_name"    = var.dataset
    "--output_path"   = local.processed_path
  })

  execution_property {
    max_concurrent_runs = 1
  }

  depends_on = [aws_cloudwatch_log_group.glue]

  tags = {
    Name = "${local.name_prefix}-transform"
  }
}

# processed/<dataset>/ -> features/<dataset>/ Parquet + Feature Store
# PutRecord. Transaction level in, one row per customer out.
resource "aws_glue_job" "feature_engineer" {
  name              = "${local.name_prefix}-feature-engineer"
  description       = "Computes RFM features, loyalty tier, churn proxy, and churn label; writes features/${var.dataset}/ and the Feature Group"
  role_arn          = var.data_engineer_role_arn
  glue_version      = var.glue_version
  max_retries       = 0
  timeout           = var.job_timeout_minutes
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  connections       = [aws_glue_connection.network.name]

  command {
    name            = "glueetl"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.feature_script.key}"
    python_version  = "3"
  }

  default_arguments = merge(local.common_arguments, {
    "--input_path"         = local.processed_path
    "--output_path"        = local.features_path
    "--feature_group_name" = var.feature_group_name
    "--region"             = data.aws_region.current.id
  })

  execution_property {
    max_concurrent_runs = 1
  }

  depends_on = [aws_cloudwatch_log_group.glue]

  tags = {
    Name = "${local.name_prefix}-feature-engineer"
  }
}

# ── Workflow: crawler -> transform -> feature-engineer ───────────────────────
# One `aws glue start-workflow-run` runs the whole pipeline in order. Each
# stage starts only when the previous one reports SUCCEEDED, so a broken
# transform never feeds the feature job. The jobs remain runnable on their
# own with start-job-run; the workflow is a convenience layer on top.

resource "aws_glue_workflow" "pipeline" {
  count       = var.enable_workflow ? 1 : 0
  name        = "${local.name_prefix}-pipeline"
  description = "raw/${var.dataset}/ -> crawler -> transform -> feature-engineer -> Feature Store"

  tags = {
    Name = "${local.name_prefix}-pipeline"
  }
}

resource "aws_glue_trigger" "start" {
  count         = var.enable_workflow ? 1 : 0
  name          = "${local.name_prefix}-pipeline-start"
  type          = "ON_DEMAND"
  workflow_name = aws_glue_workflow.pipeline[0].name

  actions {
    crawler_name = aws_glue_crawler.raw.name
  }
}

resource "aws_glue_trigger" "after_crawl" {
  count         = var.enable_workflow ? 1 : 0
  name          = "${local.name_prefix}-pipeline-transform"
  type          = "CONDITIONAL"
  workflow_name = aws_glue_workflow.pipeline[0].name

  predicate {
    conditions {
      crawler_name = aws_glue_crawler.raw.name
      crawl_state  = "SUCCEEDED"
    }
  }

  actions {
    job_name = aws_glue_job.transform.name
  }
}

resource "aws_glue_trigger" "after_transform" {
  count         = var.enable_workflow ? 1 : 0
  name          = "${local.name_prefix}-pipeline-features"
  type          = "CONDITIONAL"
  workflow_name = aws_glue_workflow.pipeline[0].name

  predicate {
    conditions {
      job_name = aws_glue_job.transform.name
      state    = "SUCCEEDED"
    }
  }

  actions {
    job_name = aws_glue_job.feature_engineer.name
  }
}
