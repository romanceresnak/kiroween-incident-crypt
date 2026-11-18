terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}

variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "vpc_id" {
  description = "VPC the internal load balancer lives in."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for the internal load balancer."
  type        = list(string)
}

variable "node_security_group_id" {
  description = "Security group of the nodes (and so of the pods). It accepts the target port from the load balancer."
  type        = string
}

variable "target_port" {
  description = "Port the orchestrator listens on."
  type        = number
  default     = 8080
}

variable "health_check_path" {
  description = "Path the target group probes. The application leaves it open to unauthenticated requests."
  type        = string
  default     = "/healthz"
}

variable "rate_limit_per_5_minutes" {
  description = "Requests per 5 minutes from one IP before the WAF blocks it."
  type        = number
  default     = 300
}

locals {
  prefix = "${var.project}-${var.env}"
}

# ---------------------------------------------------------------------------
# Internal ALB (REQ-PA-02, PA-03, PA-04). No public IP, no internet-facing listener.
# The only caller is the CloudFront VPC origin; its managed security group is the only
# source the ALB security group accepts.
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-internal-alb"
  description = "Internal ALB for the orchestrator. Ingress only from the CloudFront VPC origin."
  vpc_id      = var.vpc_id

  tags = { Name = "${local.prefix}-internal-alb" }
}

resource "aws_lb" "internal" {
  name               = "${local.prefix}-internal"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.private_subnet_ids

  drop_invalid_header_fields = true
  enable_deletion_protection = false
  idle_timeout               = 60

  tags = { Name = "${local.prefix}-internal" }
}

resource "aws_lb_target_group" "orchestrator" {
  name                 = "${local.prefix}-orch"
  port                 = var.target_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${local.prefix}-orch" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.internal.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.orchestrator.arn
  }
}

# ALB -> pods, and pods <- ALB (REQ-PA-04). Nothing else is opened on the node group.
resource "aws_vpc_security_group_egress_rule" "alb_to_nodes" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = var.node_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = var.target_port
  to_port                      = var.target_port
  description                  = "ALB to the orchestrator pods"
}

resource "aws_vpc_security_group_ingress_rule" "nodes_from_alb" {
  security_group_id            = var.node_security_group_id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.target_port
  to_port                      = var.target_port
  description                  = "Internal ALB to the orchestrator pods"
}
