# ── environments/dev ─────────────────────────────────────────────────────────
# Wires the four modules together. Each module call passes var.project and
# var.environment down; nothing in modules/ hardcodes a name.

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
