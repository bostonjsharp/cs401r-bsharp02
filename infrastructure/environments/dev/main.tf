# ── environments/dev ─────────────────────────────────────────────────────────
# Wires the six modules together. Each module call passes var.project and
# var.environment down; nothing in modules/ hardcodes a name.
#
#   vpc, storage, iam, sagemaker   Lab 1, extended in Lab 2
#   feature_store, glue            Lab 2 data pipeline

module "vpc" {
  source             = "../../modules/vpc"
  project            = var.project
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
  availability_zone  = var.availability_zone

  private_subnet_cidr = var.private_subnet_cidr
  enable_nat_gateway  = var.enable_nat_gateway
}

module "storage" {
  source      = "../../modules/storage"
  project     = var.project
  environment = var.environment

  enable_lifecycle_rules = var.enable_lifecycle_rules
}

module "iam" {
  source      = "../../modules/iam"
  project     = var.project
  environment = var.environment
}

module "sagemaker" {
  source             = "../../modules/sagemaker"
  project            = var.project
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = [module.vpc.private_subnet_id]
  security_group_ids = [module.vpc.security_group_id]
  execution_role_arn = module.iam.ml_engineer_role_arn
  instance_type      = var.sagemaker_instance_type

  app_network_access_type = var.sagemaker_network_access_type
}

# The Feature Group is created before the Glue module because the feature
# engineering job takes its name as an argument.
module "feature_store" {
  source             = "../../modules/feature_store"
  project            = var.project
  environment        = var.environment
  bucket_name        = module.storage.bucket_name
  execution_role_arn = module.iam.data_engineer_role_arn
}

module "glue" {
  source                 = "../../modules/glue"
  project                = var.project
  environment            = var.environment
  bucket_name            = module.storage.bucket_name
  data_engineer_role_arn = module.iam.data_engineer_role_arn
  vpc_id                 = module.vpc.vpc_id
  private_subnet_id      = module.vpc.private_subnet_id
  availability_zone      = module.vpc.availability_zone
  feature_group_name     = module.feature_store.feature_group_name

  # Files at the repo root, resolved relative to this environment directory.
  transform_script_path = "${path.root}/../../../glue-scripts/transform.py"
  feature_script_path   = "${path.root}/../../../glue-scripts/feature_engineer.py"
  raw_sample_path       = "${path.root}/../../../northstar-raw-sample.csv"

  number_of_workers   = var.glue_number_of_workers
  job_timeout_minutes = var.glue_job_timeout_minutes
}
