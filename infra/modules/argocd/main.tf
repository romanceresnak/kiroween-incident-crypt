locals {
  repository = "https://argoproj.github.io/argo-helm"
}

# Bootstrap only (REQ-ACD-01): Terraform installs Argo CD and the root
# Application. Everything else in the cluster is deployed by Argo CD from
# /gitops (REQ-ACD-04). The admin password is left to Argo CD's own
# argocd-initial-admin-secret (REQ-ACD-05).
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = local.repository
  chart            = "argo-cd"
  version          = var.argocd_chart_version
  namespace        = var.namespace
  create_namespace = true
}

# REQ-ACD-03: root app-of-apps Application, installed with the argocd-apps chart
# so the Application CRD only has to exist at apply time, after the argo-cd
# release has registered it. (kubernetes_manifest would fail at plan time on a
# fresh cluster.)
resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  repository = local.repository
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version
  namespace  = var.namespace

  values = [yamlencode({
    # argocd-apps 2.x: `applications` is a map keyed by Application name.
    applications = {
      "app-of-apps" = {
        namespace = var.namespace
        project   = "default"
        source = {
          repoURL        = var.gitops_repo_url
          targetRevision = var.gitops_target_revision
          path           = var.gitops_repo_path
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = var.namespace
        }
        syncPolicy = {
          automated = {
            prune    = true
            selfHeal = true
          }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd]
}
