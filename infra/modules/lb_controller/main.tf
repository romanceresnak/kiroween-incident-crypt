variable "project" {
  description = "Project name prefix."
  type        = string
}

variable "env" {
  description = "Environment suffix."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name for the Pod Identity association."
  type        = string
}

variable "cluster_arn" {
  description = "EKS cluster ARN. The trust policy only accepts sessions tagged with this cluster."
  type        = string
}

variable "target_group_arn" {
  description = "The one target group the controller may register and deregister targets in."
  type        = string
}

variable "namespace" {
  description = "Namespace the controller runs in."
  type        = string
  default     = "kube-system"
}

variable "service_account" {
  description = "Service account the controller uses. The Argo CD Application must set the same name."
  type        = string
  default     = "aws-load-balancer-controller"
}

locals {
  prefix = "${var.project}-${var.env}"

  # The read-only half of the official policy at aws-load-balancer-controller v3.6.3
  # (docs/install/iam_policy.json): the controller reads the VPC, the subnets, the
  # security groups, the pod network interfaces and the load balancer to reconcile a
  # TargetGroupBinding. Nothing here can create, change or delete anything.
  read_actions = [
    "ec2:DescribeAccountAttributes",
    "ec2:DescribeAddresses",
    "ec2:DescribeAvailabilityZones",
    "ec2:DescribeCoipPools",
    "ec2:DescribeInstances",
    "ec2:DescribeInternetGateways",
    "ec2:DescribeIpamPools",
    "ec2:DescribeNetworkInterfaces",
    "ec2:DescribeRouteTables",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSubnets",
    "ec2:DescribeTags",
    "ec2:DescribeVpcPeeringConnections",
    "ec2:DescribeVpcs",
    "ec2:GetCoipPoolUsage",
    "ec2:GetSecurityGroupsForVpc",
    "elasticloadbalancing:DescribeCapacityReservation",
    "elasticloadbalancing:DescribeListenerAttributes",
    "elasticloadbalancing:DescribeListenerCertificates",
    "elasticloadbalancing:DescribeListeners",
    "elasticloadbalancing:DescribeLoadBalancerAttributes",
    "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DescribeRules",
    "elasticloadbalancing:DescribeSSLPolicies",
    "elasticloadbalancing:DescribeTags",
    "elasticloadbalancing:DescribeTargetGroupAttributes",
    "elasticloadbalancing:DescribeTargetGroups",
    "elasticloadbalancing:DescribeTargetHealth",
    "elasticloadbalancing:DescribeTrustStores",
  ]
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "AllowEksAuthToAssumeRoleForPodIdentity"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/eks-cluster-arn"
      values   = [var.cluster_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-namespace"
      values   = [var.namespace]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-service-account"
      values   = [var.service_account]
    }
  }
}

resource "aws_iam_role" "controller" {
  name               = "${local.prefix}-lb-controller"
  assume_role_policy = data.aws_iam_policy_document.trust.json

  tags = { Name = "${local.prefix}-lb-controller" }
}

data "aws_iam_policy_document" "controller" {
  statement {
    sid       = "ReadNetworkAndLoadBalancerState"
    effect    = "Allow"
    actions   = local.read_actions
    resources = ["*"] # the Describe and Get actions above do not support resource-level scoping
  }

  statement {
    sid    = "RegisterPodsInTheOneTargetGroup"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:RegisterTargets",
      "elasticloadbalancing:DeregisterTargets",
    ]
    resources = [var.target_group_arn]
  }
}

resource "aws_iam_role_policy" "controller" {
  name   = "target-group-binding"
  role   = aws_iam_role.controller.id
  policy = data.aws_iam_policy_document.controller.json
}

resource "aws_eks_pod_identity_association" "controller" {
  cluster_name    = var.cluster_name
  namespace       = var.namespace
  service_account = var.service_account
  role_arn        = aws_iam_role.controller.arn
}

output "role_arn" {
  description = "IAM role ARN of the AWS Load Balancer Controller."
  value       = aws_iam_role.controller.arn
}
