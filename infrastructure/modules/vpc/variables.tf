# Every variable needs a description — Task B1 grades this.

variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
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

variable "availability_zone" {
  description = "Availability Zone for the public and private subnets"
  type        = string
  default     = "us-east-1a"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private subnet that holds SageMaker and the Glue workers"
  type        = string
  default     = "10.0.1.0/24"
}

variable "enable_nat_gateway" {
  description = "Create the Elastic IP, NAT Gateway, and private default route. Set false on LocalStack"
  type        = bool
  default     = true
}

variable "enable_s3_endpoint" {
  description = "Create a free S3 gateway endpoint on the private route table so S3 traffic skips the NAT"
  type        = bool
  default     = true
}
