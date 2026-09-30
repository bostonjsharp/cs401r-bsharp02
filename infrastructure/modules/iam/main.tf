# ── modules/iam ──────────────────────────────────────────────────────────────
# Three personas, each one role + one policy + one attachment:
#   MLEngineer   (Lab 1) trains and deploys; reads features/, writes artifacts/
#   DataEngineer (Lab 2) runs the Glue pipeline; writes raw/ processed/ features/
#   ModelMonitor (Lab 2) observes; writes CloudWatch metrics and nothing else
# Every boundary is enforced by omission: there are no Deny statements, a
# role simply is not granted what it must not do.
#
# MLEngineer:
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

# ── Lab 2: DataEngineer ──────────────────────────────────────────────────────
# The identity the Glue crawler and both ETL jobs run as, and the execution
# role of the Feature Group. Lambda is trusted for the ingestion triggers that
# arrive in later labs. SageMaker is trusted because CreateFeatureGroup
# rejects any role it cannot assume ("The execution role ARN is invalid").
resource "aws_iam_role" "data_engineer" {
  name        = "${local.name_prefix}-DataEngineer"
  description = "Runs the Glue data pipeline and writes Feature Store records (DataEngineer persona)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = ["glue.amazonaws.com", "lambda.amazonaws.com", "sagemaker.amazonaws.com"]
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${local.name_prefix}-DataEngineer"
  }
}

resource "aws_iam_policy" "data_engineer" {
  name        = "${local.name_prefix}-DataEngineerPolicy"
  description = "Least-privilege permissions for the DataEngineer role: Glue, VPC network interfaces, raw/ processed/ features/ prefixes, Feature Store ingest, logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # glue:GetConnection is already inside glue:*; it is spelled out
        # because Glue resolves the NETWORK connection before the script
        # starts, and that is the first thing to fail without it.
        Sid      = "GlueFullAccess"
        Effect   = "Allow"
        Action   = ["glue:*", "glue:GetConnection"]
        Resource = "*"
      },
      {
        # Glue workers join the private subnet through network interfaces
        # they create themselves. Describe* calls do not support
        # resource-level permissions, hence the wildcard.
        Sid    = "GlueNetworkInterfaces"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface",
          "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups", "ec2:DescribeVpcs",
          "ec2:DescribeVpcEndpoints", "ec2:DescribeRouteTables",
          "ec2:DescribeVpcAttribute",
        ]
        Resource = "*"
      },
      {
        # Glue tags every interface it creates and fails the run if it cannot.
        Sid      = "GlueNetworkInterfaceTags"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags", "ec2:DeleteTags"]
        Resource = "arn:aws:ec2:*:*:network-interface/*"
      },
      {
        # The three data-plane prefixes. artifacts/ is absent, so writing a
        # model artifact is denied by omission.
        Sid    = "S3DataPrefixes"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${local.data_bucket_arn}/raw/*",
          "${local.data_bucket_arn}/processed/*",
          "${local.data_bucket_arn}/features/*",
        ]
      },
      {
        # Glue downloads its own job script from here. Read only.
        Sid      = "S3GlueScriptsReadOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${local.data_bucket_arn}/artifacts/glue/*"
      },
      {
        # GetBucketAcl: Feature Store checks the bucket ACL before accepting
        # it as an offline store ("Invalid S3Uri provided" without it).
        Sid      = "S3BucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketAcl"]
        Resource = local.data_bucket_arn
      },
      {
        # The offline store writes objects with an ACL attached.
        Sid      = "S3FeatureStoreObjectAcl"
        Effect   = "Allow"
        Action   = ["s3:PutObjectAcl"]
        Resource = "${local.data_bucket_arn}/features/*"
      },
      {
        Sid      = "FeatureStoreIngest"
        Effect   = "Allow"
        Action   = ["sagemaker:PutRecord", "sagemaker:CreateFeatureGroup", "sagemaker:DescribeFeatureGroup"]
        Resource = "arn:aws:sagemaker:*:*:feature-group/*"
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws-glue/*"
      },
    ]
  })

  tags = {
    Name = "${local.name_prefix}-DataEngineerPolicy"
  }
}

resource "aws_iam_role_policy_attachment" "data_engineer" {
  role       = aws_iam_role.data_engineer.name
  policy_arn = aws_iam_policy.data_engineer.arn
}

# IAM is eventually consistent. A role that was created one second ago can
# still be rejected by Glue or Feature Store as unassumable. Consumers read
# the DataEngineer ARN through this pause, so nothing uses the role until it
# and its policy have had time to propagate.
resource "time_sleep" "data_engineer_propagation" {
  create_duration = var.iam_propagation_delay

  triggers = {
    role_arn   = aws_iam_role.data_engineer.arn
    attachment = aws_iam_role_policy_attachment.data_engineer.id
  }
}

# ── Lab 2: ModelMonitor ──────────────────────────────────────────────────────
# Observes and reports. It cannot write to S3, invoke an endpoint, or start a
# processing job; the drift analysis itself runs under a separate execution
# role in Lab 6. An alarm that cannot remediate is a design choice.
resource "aws_iam_role" "model_monitor" {
  name        = "${local.name_prefix}-ModelMonitor"
  description = "Read-only observer of model artifacts and drift runs; writes CloudWatch metrics (ModelMonitor persona)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${local.name_prefix}-ModelMonitor"
  }
}

resource "aws_iam_policy" "model_monitor" {
  name        = "${local.name_prefix}-ModelMonitorPolicy"
  description = "Least-privilege permissions for the ModelMonitor role: CloudWatch metrics and alarms, read-only processing jobs and artifacts/, logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # CloudWatch metric and alarm actions have no resource-level scoping.
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData", "cloudwatch:GetMetricStatistics",
          "cloudwatch:PutMetricAlarm", "cloudwatch:DescribeAlarms",
        ]
        Resource = "*"
      },
      {
        Sid      = "ProcessingJobsReadOnly"
        Effect   = "Allow"
        Action   = ["sagemaker:ListProcessingJobs", "sagemaker:DescribeProcessingJob"]
        Resource = "*"
      },
      {
        Sid      = "S3ArtifactsReadOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${local.data_bucket_arn}/artifacts/*"
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
    ]
  })

  tags = {
    Name = "${local.name_prefix}-ModelMonitorPolicy"
  }
}

resource "aws_iam_role_policy_attachment" "model_monitor" {
  role       = aws_iam_role.model_monitor.name
  policy_arn = aws_iam_policy.model_monitor.arn
}
