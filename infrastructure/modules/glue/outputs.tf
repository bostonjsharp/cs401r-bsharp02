# Scripts read these with `terraform output` instead of hardcoding names.

output "database_name" {
  description = "Name of the Glue catalog database"
  value       = aws_glue_catalog_database.this.name
}

output "table_name" {
  description = "Name of the table the crawler registers"
  value       = var.dataset
}

output "crawler_name" {
  description = "Name of the raw data crawler"
  value       = aws_glue_crawler.raw.name
}

output "transform_job_name" {
  description = "Name of the transform job"
  value       = aws_glue_job.transform.name
}

output "feature_engineer_job_name" {
  description = "Name of the feature engineering job"
  value       = aws_glue_job.feature_engineer.name
}

output "workflow_name" {
  description = "Name of the workflow that runs the whole pipeline, or null when disabled"
  value       = var.enable_workflow ? aws_glue_workflow.pipeline[0].name : null
}

output "processed_path" {
  description = "S3 URI the transform job writes Parquet to"
  value       = local.processed_path
}

output "features_path" {
  description = "S3 URI the feature engineering job writes Parquet to"
  value       = local.features_path
}

output "security_group_id" {
  description = "ID of the Glue workers' security group"
  value       = aws_security_group.glue.id
}
