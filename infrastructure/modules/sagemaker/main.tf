# ── modules/sagemaker ────────────────────────────────────────────────────────
# The ML development environment: one Studio Domain inside the VPC and one
# user profile (MLEngineer). Only these two resource types live here.
#
# The Domain is by far the slowest resource in the stack (8-12 minutes to
# create, several to delete). It also requires the service-linked role
# AWSServiceRoleForAmazonSageMakerNotebooks to exist in the account.

locals {
  name_prefix = "${var.project}-${var.environment}"
}

resource "aws_sagemaker_domain" "this" {
  domain_name = "${local.name_prefix}-domain"
  auth_mode   = "IAM"
  vpc_id      = var.vpc_id
  subnet_ids  = var.subnet_ids

  # Lab 1 places Studio in a public subnet with direct internet egress. Lab 2
  # moves it to a private subnet and switches this to VpcOnly with a NAT.
  app_network_access_type = "PublicInternetOnly"

  default_user_settings {
    execution_role  = var.execution_role_arn
    security_groups = var.security_group_ids

    # Notebook output sharing writes rendered notebooks to an S3 location
    # outside the data bucket's prefix model; keep it off.
    sharing_settings {
      notebook_output_option = "Disabled"
    }

    # Default kernel size for JupyterLab spaces. ml.t3.medium is the cheapest
    # Studio-capable instance and enough for the Lab 1 smoke test.
    jupyter_lab_app_settings {
      default_resource_spec {
        instance_type = var.instance_type
      }
    }
  }

  # Studio creates an EFS filesystem for home directories that Terraform never
  # manages. With the default (Retain) it survives DeleteDomain, its mount
  # target pins the subnet, and `terraform destroy` hangs on the subnet and
  # security group before failing. Delete makes destroy finish cleanly.
  retention_policy {
    home_efs_file_system = "Delete"
  }

  tags = {
    Name = "${local.name_prefix}-domain"
  }
}

# The single Lab 1 persona. DataEngineer and ModelMonitor profiles arrive in
# Lab 2 alongside the roles they need.
resource "aws_sagemaker_user_profile" "ml_engineer" {
  domain_id         = aws_sagemaker_domain.this.id
  user_profile_name = "MLEngineer"

  user_settings {
    execution_role  = var.execution_role_arn
    security_groups = var.security_group_ids
  }

  tags = {
    Name = "${local.name_prefix}-MLEngineer-profile"
  }
}
