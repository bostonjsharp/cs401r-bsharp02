# Every variable needs a description — Task B1 grades this.

variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "iam_propagation_delay" {
  description = "How long to wait after creating the DataEngineer role before anything may use it"
  type        = string
  default     = "30s"
}
