# ── modules/storage ──────────────────────────────────────────────────────────
# One versioned, encrypted, non-public data bucket with four stage prefixes.
# ONE bucket, four prefixes - not four buckets. Only these resource types
# live here: aws_s3_bucket, aws_s3_bucket_public_access_block,
# aws_s3_bucket_versioning, aws_s3_bucket_server_side_encryption_configuration,
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
