resource "aws_vpc" "vigil" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.vigil.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-private"
  }
}

# Intentionally no Internet Gateway and no NAT Gateway.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.vigil.id

  tags = {
    Name = "${var.project_name}-private"
  }
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "instance" {
  name        = "${var.project_name}-instance"
  description = "No ingress. Egress only to DNS, interface endpoints, and S3 gateway endpoint."
  vpc_id      = aws_vpc.vigil.id

  tags = {
    Name = "${var.project_name}-instance"
  }
}

resource "aws_security_group" "vpce" {
  name        = "${var.project_name}-vpce"
  description = "HTTPS from the VIGIL EC2 instance only"
  vpc_id      = aws_vpc.vigil.id

  tags = {
    Name = "${var.project_name}-vpce"
  }
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https_from_instance" {
  security_group_id            = aws_security_group.vpce.id
  referenced_security_group_id = aws_security_group.instance.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "HTTPS from VIGIL EC2"
}

resource "aws_vpc_security_group_egress_rule" "instance_https_to_vpce" {
  security_group_id            = aws_security_group.instance.id
  referenced_security_group_id = aws_security_group.vpce.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "HTTPS to interface VPC endpoints"
}

# DNS queries to the Amazon-provided resolver stay inside the VPC.
resource "aws_vpc_security_group_egress_rule" "instance_dns_udp" {
  security_group_id = aws_security_group.instance.id
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "udp"
  from_port         = 53
  to_port           = 53
  description       = "DNS to VPC resolver"
}

resource "aws_vpc_security_group_egress_rule" "instance_dns_tcp" {
  security_group_id = aws_security_group.instance.id
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 53
  to_port           = 53
  description       = "DNS TCP fallback to VPC resolver"
}
