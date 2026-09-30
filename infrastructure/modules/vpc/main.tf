# ── modules/vpc ──────────────────────────────────────────────────────────────
# Network layer. Lab 1: one VPC, one public subnet in a single AZ, an Internet
# Gateway, a route table that sends 0.0.0.0/0 to that gateway, and the
# security group Studio runs behind. Lab 2 adds a private subnet whose only
# way out is a NAT Gateway parked in the public subnet, plus a free S3 gateway
# endpoint so bucket traffic never touches the NAT.
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

# Public subnet. Since Lab 2 its only tenant is the NAT Gateway; Studio and
# the Glue workers moved to the private subnet below.
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

# ── Lab 2: private subnet behind a NAT Gateway ───────────────────────────────

# No public IPs and no route to the IGW: nothing on the internet can open a
# connection to anything in here. scripts/verify-lab2.sh finds this subnet by
# its Name tag.
resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name_prefix}-private-1"
    Tier = "private"
  }
}

# The NAT's fixed public address. Both NAT resources sit behind
# enable_nat_gateway: the NAT bills by the hour on AWS and LocalStack
# Community does not emulate it, so environments/local turns it off.
resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  # An EIP can only be allocated into a VPC that already has an IGW attached.
  depends_on = [aws_internet_gateway.this]

  tags = {
    Name = "${local.name_prefix}-eip"
  }
}

# Lives in the PUBLIC subnet: the NAT itself needs the IGW route to reach the
# internet on behalf of the private subnet.
resource "aws_nat_gateway" "this" {
  count         = var.enable_nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public.id

  depends_on = [aws_internet_gateway.this]

  tags = {
    Name = "${local.name_prefix}-nat"
  }
}

# The route is a separate resource (not an inline block) so the table still
# exists, with only the implicit local route, when the NAT is disabled.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${local.name_prefix}-private-rt"
  }
}

resource "aws_route" "private_nat" {
  count                  = var.enable_nat_gateway ? 1 : 0
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[0].id
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoints are free and add a prefix-list route to the private route
# table, so S3 reads and writes (the bulk of what Glue moves) stay on the AWS
# network instead of paying NAT data-processing charges.
data "aws_region" "current" {}

resource "aws_vpc_endpoint" "s3" {
  count             = var.enable_s3_endpoint ? 1 : 0
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${local.name_prefix}-s3-endpoint"
  }
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
