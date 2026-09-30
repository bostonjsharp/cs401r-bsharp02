# ── modules/storage ──────────────────────────────────────────────────────────
# One versioned, encrypted, non-public data bucket with four stage prefixes.
# ONE bucket, four prefixes - not four buckets. Only these resource types
# live here: aws_s3_bucket, aws_s3_bucket_public_access_block,
# aws_s3_bucket_versioning, aws_s3_bucket_server_side_encryption_configuration,
# aws_s3_bucket_lifecycle_configuration (Lab 2),
# and aws_s3_object (one per prefix).

# Bucket names are globally unique across all AWS accounts, so the account ID
# is appended: northstar-dev-data-829485866627 on AWS,
# northstar-local-data-000000000000 on LocalStack.
data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.project}-${var.environment}-data-${data.aws_caller_identity.current.account_id}"
}

# force_destroy lets `terraform destroy` empty the bucket first. Without it a
# versioned bucket that has ever held an object fails with BucketNotEmpty,
# because deleting the prefix objects only writes delete markers.
resource "aws_s3_bucket" "data" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = {
    Name = local.bucket_name
  }
}

# All four public-access blocks on. Nothing in this platform is ever served
# directly from S3 to the internet.
resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning protects training data and model artifacts from accidental
# overwrite - a retrain that clobbers last week's features is unrecoverable
# without it.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES-256): encryption at rest with S3-managed keys. Customer-managed
# KMS keys are deferred until the governance labs introduce key policies.
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 has no directories. A zero-byte object whose key ends in "/" is how a
# prefix is made visible before any real data lands in it. The IAM module
# scopes MLEngineer's object permissions to two of these four prefixes.
resource "aws_s3_object" "prefix" {
  for_each = toset(var.prefixes)

  bucket  = aws_s3_bucket.data.id
  key     = each.value
  content = ""

  # Versioning must be on before the first object is written, otherwise the
  # prefix markers end up as unversioned "null" versions.
  depends_on = [aws_s3_bucket_versioning.data]
}

# ── Lab 2: lifecycle rules ───────────────────────────────────────────────────
# Versioning keeps every overwritten object forever unless something prunes
# it. "expiration" acts on current versions; "noncurrent_version_expiration"
# acts on the old versions left behind by an overwrite or delete.
# artifacts/ has no rule on purpose: model artifacts are kept indefinitely.
locals {
  lifecycle_rules = {
    expire-raw-data           = { prefix = "raw/", current_days = 90, noncurrent_days = null }
    expire-raw-versions       = { prefix = "raw/", current_days = null, noncurrent_days = 30 }
    expire-processed-versions = { prefix = "processed/", current_days = null, noncurrent_days = 30 }
    expire-feature-versions   = { prefix = "features/", current_days = null, noncurrent_days = 60 }
    # Nothing writes datacapture/ until Lab 5; the retention exists before
    # the writer does.
    expire-datacapture = { prefix = "datacapture/", current_days = 7, noncurrent_days = null }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  count  = var.enable_lifecycle_rules ? 1 : 0
  bucket = aws_s3_bucket.data.id

  dynamic "rule" {
    for_each = local.lifecycle_rules

    content {
      id     = rule.key
      status = "Enabled"

      filter {
        prefix = rule.value.prefix
      }

      dynamic "expiration" {
        for_each = rule.value.current_days == null ? [] : [rule.value.current_days]
        content {
          days = expiration.value
        }
      }

      dynamic "noncurrent_version_expiration" {
        for_each = rule.value.noncurrent_days == null ? [] : [rule.value.noncurrent_days]
        content {
          noncurrent_days = noncurrent_version_expiration.value
        }
      }
    }
  }

  # Noncurrent-version rules are only meaningful on a versioned bucket.
  depends_on = [aws_s3_bucket_versioning.data]
}
