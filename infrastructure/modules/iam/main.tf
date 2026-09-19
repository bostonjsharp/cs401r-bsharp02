# ── modules/iam ──────────────────────────────────────────────────────────────
# The MLEngineer identity: exactly one role, one policy, one attachment.
#
# The policy body is the Lab 1 handout's NorthStarMLEngineerPolicy verbatim,
# with the bucket ARNs derived from var.project / var.environment instead of
# a hardcoded literal. `sagemaker:RegisterModel` is kept as written: the IAM
# console flags it as an unknown action, but course staff asked that the
# policy stay as specified (the IAM API accepts the string; it simply grants
# nothing until a matching action exists).

locals {
  name_prefix = "${var.project}-${var.environment}"
  # Wildcard on the account-ID suffix so the same policy works for
  # northstar-dev-data-829485866627 and northstar-local-data-000000000000.
  data_bucket_arn = "arn:aws:s3:::${local.name_prefix}-data-*"
}

# Trust policy: only the SageMaker service may assume this role. Studio and
# training jobs run as it; no human principal ever does.
resource "aws_iam_role" "ml_engineer" {
  name        = "${local.name_prefix}-MLEngineer"
  description = "Execution role for SageMaker Studio and training jobs (MLEngineer persona)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${local.name_prefix}-MLEngineer"
  }
}

resource "aws_iam_policy" "ml_engineer" {
  name        = "${local.name_prefix}-MLEngineerPolicy"
  description = "Least-privilege permissions for the MLEngineer role: SageMaker jobs, Studio self-service, artifacts/ and features/ prefixes, logs, ECR pull"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SageMakerCore"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob",
          "sagemaker:CreateEndpoint", "sagemaker:DescribeEndpoint", "sagemaker:DeleteEndpoint",
          "sagemaker:CreateEndpointConfig", "sagemaker:DeleteEndpointConfig",
          "sagemaker:CreateMlflowApp", "sagemaker:DescribeMlflowApp", "sagemaker:ListMlflowApps",
          "sagemaker:CreatePresignedMlflowAppUrl",
          "sagemaker:RegisterModel", "sagemaker:DescribeModelPackage", "sagemaker:ListModelPackages",
        ]
        Resource = "*"
      },
      {
        # Studio itself runs as this role: opening the UI calls DescribeDomain
        # / ListApps, and launching or stopping JupyterLab is CreateApp /
        # DeleteApp. Scoped to Studio resource types only.
        Sid    = "StudioSelfService"
        Effect = "Allow"
        Action = [
          "sagemaker:DescribeDomain", "sagemaker:ListDomains",
          "sagemaker:DescribeUserProfile", "sagemaker:ListUserProfiles",
          "sagemaker:DescribeSpace", "sagemaker:ListSpaces", "sagemaker:CreateSpace",
          "sagemaker:UpdateSpace", "sagemaker:DeleteSpace",
          "sagemaker:DescribeApp", "sagemaker:ListApps", "sagemaker:CreateApp", "sagemaker:DeleteApp",
          "sagemaker:CreatePresignedDomainUrl",
          # The Studio UI tags every space it creates; without these, "Create
          # JupyterLab space" fails with AccessDenied on sagemaker:AddTags.
          # Not in the handout policy (masked there by AmazonSageMakerFullAccess).
          "sagemaker:AddTags", "sagemaker:ListTags", "sagemaker:DeleteTags",
        ]
        Resource = [
          "arn:aws:sagemaker:*:*:domain/*", "arn:aws:sagemaker:*:*:user-profile/*",
          "arn:aws:sagemaker:*:*:space/*", "arn:aws:sagemaker:*:*:app/*",
        ]
      },
      {
        # Object actions only on artifacts/ and features/. raw/ and processed/
        # are intentionally absent - denied by omission. Never add the bare
        # bucket ARN here: a trailing * would also match /raw/*.
        Sid    = "S3ArtifactsAndFeatures"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${local.data_bucket_arn}/artifacts/*",
          "${local.data_bucket_arn}/features/*",
        ]
      },
      {
        Sid      = "S3BucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = local.data_bucket_arn
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"
      },
      {
        Sid      = "ECRRead"
        Effect   = "Allow"
        Action   = ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:GetAuthorizationToken"]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "${local.name_prefix}-MLEngineerPolicy"
  }
}

resource "aws_iam_role_policy_attachment" "ml_engineer" {
  role       = aws_iam_role.ml_engineer.name
  policy_arn = aws_iam_policy.ml_engineer.arn
}
