# ── environments/local ───────────────────────────────────────────────────────
# Calls the same modules as environments/dev. The sagemaker module is omitted
# on purpose: SageMaker is not in LocalStack Community.
#
# These calls are live, not commented out, so `make local-validate` works the
# moment your vpc, storage, and iam modules are implemented. Until then,
# terraform validate still passes — an empty module is a valid module.

module "vpc" {
  source      = "../../modules/vpc"
  project     = var.project
  environment = var.environment

  # NAT Gateways and VPC endpoints are not part of LocalStack Community.
  enable_nat_gateway = false
  enable_s3_endpoint = false
}

module "storage" {
  source      = "../../modules/storage"
  project     = var.project
  environment = var.environment

  enable_lifecycle_rules = false
}

# Creates all three roles: MLEngineer, DataEngineer, ModelMonitor.
module "iam" {
  source      = "../../modules/iam"
  project     = var.project
  environment = var.environment

  # The emulator has no propagation lag to wait out.
  iam_propagation_delay = "0s"
}
