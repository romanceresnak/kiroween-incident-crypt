# ---------------------------------------------------------------------------
# CloudFront VPC origin (REQ-PA-02): a private path from CloudFront to the internal ALB.
# CloudFront-to-ALB is HTTP inside the VPC, because the default *.cloudfront.net
# certificate cannot be installed on an ALB without a domain. Viewers only speak HTTPS.
# ---------------------------------------------------------------------------

resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${local.prefix}-internal-alb"
    arn                    = aws_lb.internal.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"

    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  tags = { Name = "${local.prefix}-internal-alb" }
}

# CloudFront creates this security group in the VPC once the VPC origin exists; the ALB
# accepts port 80 from it and from nothing else. If the lookup finds nothing, the apply stops.
data "aws_security_group" "vpc_origin" {
  vpc_id = var.vpc_id

  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }

  depends_on = [aws_cloudfront_vpc_origin.alb]
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = data.aws_security_group.vpc_origin.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "CloudFront VPC origin to the internal ALB"
}

# ---------------------------------------------------------------------------
# Distribution (REQ-PA-01): HTTPS only with the default certificate, nothing cached, every
# viewer header, cookie and query string forwarded so the login and its cookie work.
# ---------------------------------------------------------------------------

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  comment         = "${local.prefix} incident crypt (judging access)"
  price_class     = "PriceClass_100"
  http_version    = "http2and3"
  is_ipv6_enabled = true
  web_acl_id      = aws_wafv2_web_acl.this.arn

  origin {
    origin_id   = "orchestrator"
    domain_name = aws_lb.internal.dns_name

    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id         = "orchestrator"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "${local.prefix}-incident-crypt" }
}

# ---------------------------------------------------------------------------
# WAF (REQ-PA-05). Scope CLOUDFRONT web ACLs exist only in us-east-1.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "this" {
  provider = aws.us_east_1

  name        = "${local.prefix}-incident-crypt"
  description = "Rate limit and the AWS common rule set for the judging endpoint."
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit-per-ip"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_per_5_minutes
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-rate-limit-per-ip"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-managed-common-rule-set"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-common-rule-set"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.prefix}-incident-crypt"
    sampled_requests_enabled   = true
  }

  tags = { Name = "${local.prefix}-incident-crypt" }
}
