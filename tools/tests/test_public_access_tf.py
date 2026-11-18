"""scenario-lab TASK-01: the public access and controller Terraform (REQ-PA, REQ-LC-02, REQ-TF).

There is no HCL parser in the test environment, so these read the text of the modules. The
plan and the apply were checked by Terraform itself; see the status in the spec.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
INFRA = REPO / "infra"
PUBLIC = INFRA / "modules" / "public_access"
CONTROLLER = INFRA / "modules" / "lb_controller"


def text(*parts: str) -> str:
    return (INFRA.joinpath(*parts)).read_text()


def public_all() -> str:
    return "".join(p.read_text() for p in sorted(PUBLIC.glob("*.tf")))


def resource(source: str, kind: str, name: str) -> str:
    """The body of one resource block, by brace matching."""
    start = source.index(f'resource "{kind}" "{name}"')
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[open_at:i + 1]
    raise AssertionError(f"unbalanced braces in {kind}.{name}")


# ------------------------------------------------------------------- the ALB (PA-02..04)


def test_the_alb_is_internal_and_in_the_private_subnets() -> None:
    body = resource(public_all(), "aws_lb", "internal")
    assert re.search(r"internal\s*=\s*true", body)
    assert 'load_balancer_type = "application"' in body
    assert "subnets            = var.private_subnet_ids" in body
    assert "drop_invalid_header_fields = true" in body


def test_nothing_in_the_module_is_public() -> None:
    source = public_all()
    assert not re.search(r"internal\s*=\s*false", source)
    assert "0.0.0.0/0" not in source and "::/0" not in source
    assert "cidr_ipv4" not in source and "cidr_blocks" not in source
    assert "associate_public_ip" not in source and "aws_eip" not in source
    assert "public_subnet" not in source


def test_the_listener_is_http_80_and_forwards_to_the_target_group() -> None:
    body = resource(public_all(), "aws_lb_listener", "http")
    assert "port              = 80" in body and 'protocol          = "HTTP"' in body
    assert 'type             = "forward"' in body
    assert "aws_lb_target_group.orchestrator.arn" in body


def test_the_target_group_is_ip_on_8080_with_a_healthz_check() -> None:
    source = public_all()
    body = resource(source, "aws_lb_target_group", "orchestrator")
    assert 'target_type          = "ip"' in body
    assert "port                 = var.target_port" in body
    assert re.search(r'variable "target_port"[^}]*default\s*=\s*8080', source, re.S)
    assert re.search(r'variable "health_check_path"[^}]*default\s*=\s*"/healthz"', source, re.S)
    assert 'matcher             = "200"' in body


def test_security_group_rules_name_security_groups_never_cidrs() -> None:
    source = public_all()
    ingress = resource(source, "aws_vpc_security_group_ingress_rule", "alb_from_cloudfront")
    assert "referenced_security_group_id = data.aws_security_group.vpc_origin.id" in ingress
    assert "from_port                    = 80" in ingress
    assert "to_port                      = 80" in ingress
    egress = resource(source, "aws_vpc_security_group_egress_rule", "alb_to_nodes")
    assert "referenced_security_group_id = var.node_security_group_id" in egress
    assert "var.target_port" in egress
    nodes = resource(source, "aws_vpc_security_group_ingress_rule", "nodes_from_alb")
    assert "referenced_security_group_id = aws_security_group.alb.id" in nodes
    assert "security_group_id            = var.node_security_group_id" in nodes
    # No other rule exists: the ALB opens exactly these three.
    rules = re.findall(r'resource "aws_vpc_security_group_(?:ingress|egress)_rule"', source)
    assert len(rules) == 3
    assert "ingress {" not in source and "egress {" not in source  # no inline rules


def test_the_vpc_origin_security_group_is_looked_up_after_the_origin_exists() -> None:
    body = resource_data(public_all(), "aws_security_group", "vpc_origin")
    assert "CloudFront-VPCOrigins-Service-SG" in body
    assert "depends_on = [aws_cloudfront_vpc_origin.alb]" in body


def resource_data(source: str, kind: str, name: str) -> str:
    start = source.index(f'data "{kind}" "{name}"')
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[open_at:i + 1]
    raise AssertionError("unbalanced")


# --------------------------------------------------------------- CloudFront (PA-01)


def test_the_vpc_origin_points_to_the_alb_over_http_only() -> None:
    body = resource(public_all(), "aws_cloudfront_vpc_origin", "alb")
    assert "arn                    = aws_lb.internal.arn" in body
    assert 'origin_protocol_policy = "http-only"' in body
    assert "http_port              = 80" in body


def test_the_distribution_is_https_only_uncached_with_the_default_certificate() -> None:
    source = public_all()
    body = resource(source, "aws_cloudfront_distribution", "this")
    assert 'viewer_protocol_policy   = "redirect-to-https"' in body
    assert "cloudfront_default_certificate = true" in body
    assert "aliases" not in body and "acm_certificate_arn" not in body  # no custom domain
    assert "data.aws_cloudfront_cache_policy.disabled.id" in body
    assert "data.aws_cloudfront_origin_request_policy.all_viewer.id" in body
    assert 'name = "Managed-CachingDisabled"' in source
    assert 'name = "Managed-AllViewer"' in source
    assert "vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id" in body
    assert "custom_origin_config" not in body and "s3_origin_config" not in body
    assert "web_acl_id      = aws_wafv2_web_acl.this.arn" in body
    for method in ("GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"):
        assert f'"{method}"' in body
    cached = re.search(r"cached_methods\s*=\s*\[([^\]]*)\]", body)
    assert cached and set(re.findall(r'"(\w+)"', cached.group(1))) == {"GET", "HEAD"}


# -------------------------------------------------------------------------- WAF (PA-05)


def test_the_web_acl_has_a_rate_rule_and_the_common_rule_set() -> None:
    source = public_all()
    body = resource(source, "aws_wafv2_web_acl", "this")
    assert 'provider = aws.us_east_1' in body
    assert 'scope       = "CLOUDFRONT"' in body
    assert "allow {}" in body
    assert 'aggregate_key_type = "IP"' in body
    assert "limit              = var.rate_limit_per_5_minutes" in body
    assert re.search(r'variable "rate_limit_per_5_minutes"[^}]*default\s*=\s*300', source, re.S)
    rate = body[body.index('name     = "rate-limit-per-ip"'):]
    assert "block {}" in rate[: rate.index("rule {") if "rule {" in rate else None]
    assert 'name        = "AWSManagedRulesCommonRuleSet"' in body
    assert 'vendor_name = "AWS"' in body
    assert "override_action" in body and "none {}" in body
    assert body.count("cloudwatch_metrics_enabled = true") == 3
    assert body.count("sampled_requests_enabled   = true") == 3


def test_the_alias_provider_is_us_east_1_and_passed_to_the_module() -> None:
    root = text("main.tf")
    alias = r'provider "aws" \{\s*alias\s*=\s*"us_east_1"\s*region\s*=\s*"us-east-1"'
    assert re.search(alias, root)
    call = resource_module(root, "public_access")
    assert "aws.us_east_1 = aws.us_east_1" in call
    assert "configuration_aliases = [aws.us_east_1]" in (PUBLIC / "main.tf").read_text()


def resource_module(source: str, name: str) -> str:
    start = source.index(f'module "{name}"')
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[open_at:i + 1]
    raise AssertionError("unbalanced")


# --------------------------------------------------------- the controller's role (LC-02)

ALLOWED_WRITES = {"elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets"}


def controller_actions() -> list[str]:
    return re.findall(r'"((?:ec2|elasticloadbalancing|iam|s3|\*)[:*][A-Za-z*]*)"',
                      (CONTROLLER / "main.tf").read_text())


def test_the_controller_can_only_read_and_register_targets() -> None:
    actions = controller_actions()
    reads = [a for a in actions if a not in ALLOWED_WRITES]
    assert len(reads) == 29, len(reads)
    for action in reads:
        verb = action.split(":")[1]
        assert verb.startswith(("Describe", "Get")), action
    assert set(actions) & ALLOWED_WRITES == ALLOWED_WRITES
    for action in actions:
        assert not action.startswith("iam:") and not action.endswith("*"), action
        verb = action.split(":")[1]
        assert not verb.startswith(("Create", "Delete", "Modify", "Set", "Add", "Remove")), action


def test_target_registration_is_limited_to_the_one_target_group() -> None:
    source = (CONTROLLER / "main.tf").read_text()
    statement = source[source.index('sid    = "RegisterPodsInTheOneTargetGroup"'):]
    assert "resources = [var.target_group_arn]" in statement
    assert "targetgroup/*" not in source
    assert source.count('resources = ["*"]') == 1, "only the read-only statement may use *"
    root = text("main.tf")
    assert "target_group_arn = module.public_access.target_group_arn" in resource_module(
        root, "lb_controller")


def test_the_controller_role_is_trusted_only_for_its_own_service_account() -> None:
    source = (CONTROLLER / "main.tf").read_text()
    assert 'identifiers = ["pods.eks.amazonaws.com"]' in source
    for tag in ("eks-cluster-arn", "kubernetes-namespace", "kubernetes-service-account"):
        assert f"aws:RequestTag/{tag}" in source
    assert re.search(r'variable "namespace"[^}]*default\s*=\s*"kube-system"', source, re.S)
    sa = r'variable "service_account"[^}]*default\s*=\s*"aws-load-balancer-controller"'
    assert re.search(sa, source, re.S)
    assert "aws_eks_pod_identity_association" in source


def test_every_action_comes_from_the_official_policy_at_the_pinned_tag() -> None:
    """The 29 reads are the read-only part of docs/install/iam_policy.json at v3.6.3."""
    expected = {
        "ec2:DescribeAccountAttributes", "ec2:DescribeAddresses", "ec2:DescribeAvailabilityZones",
        "ec2:DescribeCoipPools", "ec2:DescribeInstances", "ec2:DescribeInternetGateways",
        "ec2:DescribeIpamPools", "ec2:DescribeNetworkInterfaces", "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "ec2:DescribeTags",
        "ec2:DescribeVpcPeeringConnections", "ec2:DescribeVpcs", "ec2:GetCoipPoolUsage",
        "ec2:GetSecurityGroupsForVpc", "elasticloadbalancing:DescribeCapacityReservation",
        "elasticloadbalancing:DescribeListenerAttributes",
        "elasticloadbalancing:DescribeListenerCertificates",
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:DescribeLoadBalancerAttributes",
        "elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeRules",
        "elasticloadbalancing:DescribeSSLPolicies", "elasticloadbalancing:DescribeTags",
        "elasticloadbalancing:DescribeTargetGroupAttributes",
        "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetHealth",
        "elasticloadbalancing:DescribeTrustStores",
    }
    assert set(controller_actions()) - ALLOWED_WRITES == expected


# ---------------------------------------------------------------- root and budget


def test_the_budget_default_is_400_and_the_outputs_exist() -> None:
    variables = text("variables.tf")
    assert re.search(r'variable "budget_limit_usd"[^}]*default\s*=\s*400', variables, re.S)
    assert "default     = 120" not in variables
    outputs = text("outputs.tf")
    for name in ("public_url", "target_group_arn", "vpc_id", "private_subnet_cidrs",
                 "lb_controller_role_arn"):
        assert f'output "{name}"' in outputs, name
    outputs_tf = text("modules", "public_access", "outputs.tf")
    assert 'value       = "https://${aws_cloudfront_distribution.this.domain_name}"' in outputs_tf


def test_the_existing_cluster_resources_are_untouched() -> None:
    """REQ-TF-03: the new work adds outputs to vpc and eks and changes no resource in them."""
    for module in ("vpc", "eks"):
        source = text("modules", module, "outputs.tf")
        assert source.count('output "') >= 4
    git = shutil.which("git")
    assert git
    diff = subprocess.run(  # noqa: S603
        [git, "-C", str(REPO), "diff", "--stat", "HEAD", "--", "infra/modules/eks/main.tf",
         "infra/modules/vpc/main.tf", "infra/modules/pod_identity", "infra/modules/github_oidc"],
        capture_output=True, text=True, check=False)
    assert diff.stdout.strip() == ""


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform is not installed")
def test_terraform_formatting_is_clean() -> None:
    terraform = shutil.which("terraform")
    assert terraform
    result = subprocess.run(  # noqa: S603
        [terraform, f"-chdir={INFRA}", "fmt", "-check", "-recursive"],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout
