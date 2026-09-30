# Mirrors environments/dev minus the SageMaker output (not in LocalStack).
# The Makefile reads s3_bucket_name from here for `make local-validate`.

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_id" {
  description = "ID of the public subnet"
  value       = module.vpc.public_subnet_id
}

output "s3_bucket_name" {
  description = "Name of the data bucket"
  value       = module.storage.bucket_name
}

output "ml_engineer_role_arn" {
  description = "ARN of the MLEngineer role"
  value       = module.iam.ml_engineer_role_arn
}

output "private_subnet_id" {
  description = "ID of the private subnet"
  value       = module.vpc.private_subnet_id
}

output "data_engineer_role_arn" {
  description = "ARN of the DataEngineer role"
  value       = module.iam.data_engineer_role_arn
}

output "model_monitor_role_arn" {
  description = "ARN of the ModelMonitor role"
  value       = module.iam.model_monitor_role_arn
}
