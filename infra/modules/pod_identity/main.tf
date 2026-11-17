data "aws_caller_identity" "current" {}

locals {
  prefix     = "${var.project}-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  agents     = toset(var.agent_names)

  # Agents that also query the Knowledge Base. Filtering agent_names (known at
  # plan time) keeps for_each plannable.
  kb_agents = toset([for a in var.agent_names : a if contains(var.knowledge_base_agents, a)])

  service_accounts = { for a in var.agent_names : a => "${a}-sa" }
}

# ---------------------------------------------------------------------------
# Trust policy (REQ-IAM-02): EKS Pod Identity may assume the role only for the
# agent's own service account and namespace in this cluster. The conditions use
# the session tags Pod Identity adds on every assume.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "trust" {
  for_each = local.agents

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
      values   = [local.service_accounts[each.key]]
    }
  }
}

resource "aws_iam_role" "agent" {
  for_each = local.agents

  name               = "${local.prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.trust[each.key].json

  tags = { Name = "${local.prefix}-${each.key}" }
}

# ---------------------------------------------------------------------------
# Permissions (REQ-IAM-03): model invocation through the EU cross-region
# inference profile. Converse is authorised by InvokeModel, so no separate action.
# Cluster read access is Kubernetes RBAC in the Helm chart, not IAM.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "bedrock" {
  statement {
    sid    = "InvokeThroughEuInferenceProfile"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/eu.*",
      "arn:aws:bedrock:eu-*::foundation-model/*",
    ]
  }
}

resource "aws_iam_role_policy" "bedrock" {
  for_each = local.agents

  name   = "bedrock-invoke"
  role   = aws_iam_role.agent[each.key].id
  policy = data.aws_iam_policy_document.bedrock.json
}

# REQ-IAM-04: retrieval agents may query the Knowledge Base.
data "aws_iam_policy_document" "kb_retrieve" {
  statement {
    sid       = "RetrieveFromKnowledgeBase"
    effect    = "Allow"
    actions   = ["bedrock:Retrieve"]
    resources = [var.knowledge_base_arn]
  }
}

resource "aws_iam_role_policy" "kb_retrieve" {
  for_each = local.kb_agents

  name   = "knowledge-base-retrieve"
  role   = aws_iam_role.agent[each.key].id
  policy = data.aws_iam_policy_document.kb_retrieve.json
}

# ---------------------------------------------------------------------------
# Pod Identity associations (REQ-IAM-05)
# ---------------------------------------------------------------------------

resource "aws_eks_pod_identity_association" "agent" {
  for_each = local.agents

  cluster_name    = var.cluster_name
  namespace       = var.namespace
  service_account = local.service_accounts[each.key]
  role_arn        = aws_iam_role.agent[each.key].arn
}
