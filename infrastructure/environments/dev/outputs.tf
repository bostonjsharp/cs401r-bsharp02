# Surface what later labs need. scripts/verify-lab1.sh reads the first five
# with `terraform output -raw`; scripts/run-lab2-pipeline.sh reads the Glue
# and Feature Store names so nothing is hardcoded there either.

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_id" {
  description = "ID of the public subnet"
  value       = module.vpc.public_subnet_id
}

output "private_subnet_id" {
  description = "ID of the private subnet"
  value       = module.vpc.private_subnet_id
}

output "s3_bucket_name" {
  description = "Name of the data bucket"
  value       = module.storage.bucket_name
}

output "ml_engineer_role_arn" {
  description = "ARN of the MLEngineer role"
  value       = module.iam.ml_engineer_role_arn
}

output "data_engineer_role_arn" {
  description = "ARN of the DataEngineer role"
  value       = module.iam.data_engineer_role_arn
}

output "model_monitor_role_arn" {
  description = "ARN of the ModelMonitor role"
  value       = module.iam.model_monitor_role_arn
}

output "sagemaker_domain_id" {
  description = "ID of the SageMaker Domain"
  value       = module.sagemaker.domain_id
}

# ── Lab 2 pipeline ───────────────────────────────────────────────────────────

output "glue_database_name" {
  description = "Glue catalog database"
  value       = module.glue.database_name
}

output "glue_table_name" {
  description = "Table the crawler registers"
  value       = module.glue.table_name
}

output "glue_crawler_name" {
  description = "Raw data crawler"
  value       = module.glue.crawler_name
}

output "glue_transform_job_name" {
  description = "Transform job"
  value       = module.glue.transform_job_name
}

output "glue_feature_engineer_job_name" {
  description = "Feature engineering job"
  value       = module.glue.feature_engineer_job_name
}

output "glue_workflow_name" {
  description = "Workflow that runs crawler, transform, and feature-engineer in order"
  value       = module.glue.workflow_name
}

output "feature_group_name" {
  description = "SageMaker Feature Group"
  value       = module.feature_store.feature_group_name
}

output "processed_path" {
  description = "S3 URI of the processed Parquet"
  value       = module.glue.processed_path
}

output "features_path" {
  description = "S3 URI of the feature Parquet"
  value       = module.glue.features_path
}
