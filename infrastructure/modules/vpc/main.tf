# ── modules/vpc ──────────────────────────────────────────────────────────────
# Lab 1 network layer: one VPC, one public subnet in a single AZ, an Internet
# Gateway, a route table that sends 0.0.0.0/0 to that gateway, and the
# security group Studio runs behind. Only these six resource types live here.
#
# Every name is derived from var.project and var.environment; nothing under
# modules/ hardcodes a project-environment literal.

locals {
  name_prefix = "${var.project}-${var.environment}"
}

# The network boundary. DNS support + hostnames are both required for Studio
# to resolve S3/ECR endpoints and for instances to receive resolvable names.
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name_prefix}-vpc"
  }
}

# Single public subnet. Lab 2 adds private subnets; the Tier tag is what
# scripts/verify-lab1.sh uses to confirm none exist yet.
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name_prefix}-public-1"
    Tier = "public"
  }
}

# One IGW per VPC (1:1), so the attachment is expressed directly via vpc_id.
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${local.name_prefix}-igw"
  }
}

# The default route is what makes the subnet "public". The 10.0.0.0/16 local
# route is implicit on every route table and does not need declaring.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${local.name_prefix}-public-rt"
  }
}

# Without this association the subnet would fall back to the VPC's main route
# table, which has no IGW route.
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Studio's security group: anything inside the VPC may reach it, nothing on
# the public internet may initiate a connection. Security groups are stateful,
# so replies to Studio's own outbound requests are still allowed back in.
resource "aws_security_group" "this" {
  name        = "${local.name_prefix}-sagemaker-sg"
  description = "SageMaker Studio - inbound from VPC CIDR only, all outbound"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "All traffic from within the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-sagemaker-sg"
  }
}
