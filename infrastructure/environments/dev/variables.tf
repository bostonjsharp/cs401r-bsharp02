variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
  default     = "northstar"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR block for the public subnet"
  type        = string
  default     = "10.0.100.0/24"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private subnet that holds SageMaker and the Glue workers"
  type        = string
  default     = "10.0.1.0/24"
}

variable "enable_nat_gateway" {
  description = "Create the NAT Gateway and its Elastic IP (the only hourly-billed network resource)"
  type        = bool
  default     = true
}

variable "enable_lifecycle_rules" {
  description = "Attach the lifecycle configuration to the data bucket"
  type        = bool
  default     = true
}

variable "sagemaker_network_access_type" {
  description = "How Studio apps reach the network: VpcOnly routes through the VPC and the NAT"
  type        = string
  default     = "VpcOnly"
}

variable "availability_zone" {
  description = "Availability Zone for the public and private subnets"
  type        = string
  default     = "us-east-1a"
}

variable "sagemaker_instance_type" {
  description = "Default kernel instance type for SageMaker Studio apps"
  type        = string
  default     = "ml.t3.medium"
}
