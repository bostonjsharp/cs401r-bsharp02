# Downstream modules and environments/dev/main.tf consume these.

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.this.id
}

output "public_subnet_id" {
  description = "ID of the public subnet"
  value       = aws_subnet.public.id
}

output "security_group_id" {
  description = "ID of the SageMaker Studio security group"
  value       = aws_security_group.this.id
}

output "private_subnet_id" {
  description = "ID of the private subnet"
  value       = aws_subnet.private.id
}

output "availability_zone" {
  description = "Availability Zone the subnets live in"
  value       = aws_subnet.private.availability_zone
}
