# scenario-lab — Design

## 1. Request path

```
judge's browser ──HTTPS──> CloudFront (default cert, WAF: rate 300/5min/IP + CommonRuleSet)
                              │  VPC origin (private, HTTP :80)
                              ▼
                       internal ALB (private subnets, SG: only the VPC-origin SG on :80)
                              │  target group, type ip, :8080, health /healthz
                              ▼
        TargetGroupBinding ──> orchestrator pod (login, /ui, API, Lab API)
                                   │ http, NetworkPolicy: orchestrator -> lab-runner only
                                   ▼
                              lab-runner pod ──(RoleBinding in lab-01..lab-14)──> Kubernetes API
                                                                                   │
        watcher ──(bearer token)──> orchestrator /incidents <── pod/event streams ──┘
```

The orchestrator never writes to Kubernetes. The runner never sees a user, a cookie or AWS.

## 2. Terraform

```
infra/modules/public_access/    (aws + aws.us_east_1)
  alb.tf         aws_security_group alb, aws_lb internal, aws_lb_target_group (ip, 8080, /healthz),
                 aws_lb_listener :80 forward
  origin.tf      aws_cloudfront_vpc_origin (http-only, :80) -> ALB
                 data aws_security_group "CloudFront-VPCOrigins-Service-SG" (depends_on origin)
                 aws_vpc_security_group_ingress_rule alb <- origin SG :80
                 aws_vpc_security_group_egress_rule  alb -> node SG :8080
                 aws_vpc_security_group_ingress_rule node SG <- alb SG :8080
  cdn.tf         aws_cloudfront_distribution (CachingDisabled, AllViewer, redirect-to-https,
                 default cert, PriceClass_100) with web_acl_id
  waf.tf         aws_wafv2_web_acl (us-east-1): rate rule + AWSManagedRulesCommonRuleSet
infra/modules/lb_controller/    Pod Identity role (read-only Describe/Get + Register/Deregister
                                on the one TG) + association kube-system/aws-load-balancer-controller
modules/vpc, modules/eks        + outputs: vpc_cidr/private cidrs, cluster security group id
root                            + provider "aws" alias us_east_1, module calls, outputs
                                + budget_limit_usd default 400
```

The ALB sits in the private subnets. Its listener is HTTP because the CloudFront-to-VPC-origin
hop is private and the default certificate cannot be installed on an ALB without a domain.
Viewers only ever speak HTTPS to CloudFront.

The managed security group for the VPC origin is looked up by name in the VPC after the origin
exists. If it is absent, the apply stops there (a stop condition), not later.

The controller policy is the read-only half of the official policy (29 actions) plus two target
actions on `arn:aws:elasticloadbalancing:<region>:<account>:targetgroup/<name>/*`.

## 3. GitOps

- `gitops/apps/aws-load-balancer-controller.yaml`: chart 3.6.3 from `https://aws.github.io/eks-charts`,
  namespace `kube-system`, values `clusterName`, `region`, `vpcId`, `replicaCount: 1`,
  `enableServiceMutatorWebhook: false`, `createIngressClassResource: false`, a service account
  named `aws-load-balancer-controller` (the Pod Identity association's name), small resources.
- `gitops/apps/agents.yaml`: `SkipDryRunOnMissingResource=true` so the first sync tolerates the
  missing CRD.
- Chart additions, each a template gated by a value:
  `publicAccess.yaml` (TargetGroupBinding, ALB ingress NetworkPolicy), `lab.yaml` (14 namespaces,
  quota, limit range, RBAC, runner Deployment/Service/ServiceAccount, NetworkPolicies).
- Orchestrator env: `AUTH_REQUIRED`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `AUTH_INTERNAL_TOKEN` (all
  from Secret `orchestrator-auth`, `optional: true`, so a missing Secret fails closed in code, not
  at pod start), `TRUSTED_PROXY_HOPS`, `PUBLIC_HOSTS`, `LAB_RUNNER_URL`, `LAB_GITHUB_BRANCH`.
- Watcher env: `ORCHESTRATOR_TOKEN` from the same Secret's `internal-token`.

## 4. Login (`kiroween_orchestrator/auth.py`)

```
AuthConfig(required, username, password, internal_token, session_ttl, trusted_hops, public_hosts,
           max_failures=5, window=900, lock=900, global_max=100, global_pause=300)
from_env(env): required = AUTH_REQUIRED != "false"      # fail closed by default
configured    = username and password

middleware (installed by create(service, auth)):
  path in {/healthz, /readyz, /login, /login-assets/*, POST /auth/login}  -> pass
  not required                                                              -> pass
  required and not configured                                               -> 503
  Authorization: Bearer <internal> and POST /incidents                       -> pass (token only)
  valid session cookie                                                       -> pass (+ role rules)
  GET/HEAD for /ui*                                                          -> 302 /login?next=
  otherwise                                                                  -> 401 {"detail": "login required"}
```

- Sessions: `secrets.token_urlsafe(32)` in an in-memory dict with expiry, pruned on use, capped.
  A restart logs everyone out; the cost is accepted.
- Comparison: `hmac.compare_digest(sha256(a), sha256(b))` for both fields, both always evaluated.
- Lockout: per-address deque of failure times and a locked-until time; a global deque.
  `client_ip(request)`: split `X-Forwarded-For`, take the entry `hops` from the right when `hops`
  is set and enough entries exist, else the socket peer.
- Cookie: `kiroween_session`, `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=28800`.
- Judge scope (REQ-AU-13): a session may decide, and may `POST /incidents`, only for a namespace
  matching `^lab-(0[1-9]|1[0-4])$`. The bearer token has no such limit.
- `check_same_origin(request, auth)`: JSON content type; `Origin` netloc in `{Host, X-Forwarded-Host
  (hops > 0), public_hosts}`.
- Login page: `ui_login/{login.html,login.js,login.css}` mounted at `/login-assets`; `GET /login`
  returns `login.html`. Same CSP as `/ui`.

## 5. Scenario catalog (`kiroween_orchestrator/lab/`)

```
lab/scenarios.py   SCENARIOS: tuple[Scenario, ...]  (14), Scenario(id, namespace, title, agent,
                   summary, expected_signals, phases)  Phase(manifests: str, wait_ready: str|None)
lab/render.py      render(scenario, run) -> list[phase of list[dict]]: substitutes __NS__ and __RUN__,
                   adds labels, parses YAML, validates (kinds, images, limits, names, no host*)
lab/catalog.py     public(): the fields the UI shows, no manifests
```

All manifests are YAML text in `scenarios.py`, one constant per scenario, in the style of
`chaos/*/manifests.yaml`. Workloads reuse `public.ecr.aws/docker/library/busybox:1.36` and
`python:3.12-slim`. Details by scenario:

| id | what the manifests do |
|---|---|
| oom-killed | python allocates 8 MiB every 0.5 s, memory limit 64Mi |
| cpu-throttling | python HTTP server plus a busy loop, CPU limit 20m, readiness `httpGet` timeout 1 s |
| memory-leak | python grows a list 1 MiB a second, memory limit 48Mi, logs its size |
| netpol-blocks-client | server, Service, NetworkPolicy admitting `app: trusted`, client labelled `app: client` with an `exec wget` readiness probe |
| service-selector-mismatch | server labelled `app: web-v2`, Service selecting `app: web`, client probe |
| wrong-target-port | server on 8080, Service `targetPort: 9090`, client probe |
| dns-missing-service | client probes `http://payments-api:8080/`, which does not exist |
| bad-image-tag | phase 1 working Deployment (wait ready), phase 2 patches the image to a missing tag |
| crashloop-missing-config-key | ConfigMap with key `application.conf`, app requires `/config/app.conf` and exits 1 |
| missing-secret | `secretKeyRef` to `db-credentials`, which is never created |
| rollout-stuck-readiness | phase 1 healthy, phase 2 changes the readiness path to `/ready` (404), `maxUnavailable: 0` |
| pvc-missing-storageclass | claim with `storageClassName: does-not-exist`, pod mounting it |
| rwx-on-ebs | claim `ReadWriteMany` on `gp2`, pod mounting it |
| missing-configmap-mount | pod mounts ConfigMap `app-settings`, which is never created |

Deployment and PVC names are `<base>-__RUN__`; Services, ConfigMaps and NetworkPolicies keep their
names, because other objects refer to them by name and cleanup removes them between runs.

## 6. The runner (`kiroween_orchestrator/lab/`)

```
lab/limits.py      RunLimits(max_per_hour, cleanup_seconds, clock); sliding window of accepted runs
lab/runner.py      LabRunner(kube, clock, sleep, new_run_id): state machine; no Kubernetes import
lab/kube.py        KubernetesWriter: the only module with write calls (see the guard)
lab/server.py      FastAPI app: /healthz /readyz /status /run /reset; entrypoint of -m ...lab
lab/client.py      LabClient (httpx) used by the orchestrator; never imports runner or kube
```

State: `idle | running | cleaning`, plus `scenario`, `namespace`, `run`, `started_at`,
`expires_at`, `phase`, `note`. Transitions:

```
run(id): lock; not idle -> 409; limit reached -> 429; namespace check; state=running;
         start thread: for each phase: apply, optionally wait ready (<= 90 s, then continue)
tick (every 5 s thread): running and now >= expires_at -> cleanup
reset(): cleanup now
cleanup(): state=cleaning; delete by label; wait until empty (<= 90 s); state=idle
start: cleanup all 14, state=idle
```

`apply` creates, and on 409 patches (a strategic merge of the whole Deployment, which is how
phase 2 changes the image and the probe). `KubernetesWriter` uses the typed client: for each of
the five kinds a `create_namespaced_*`, for Deployments `patch_namespaced_deployment`, and
`delete_collection_namespaced_*` by label selector. Deleting Deployments uses background
propagation; pods are deleted too, in case a controller lags.

`wait_ready(deployment)` polls `read_namespaced_deployment` for `ready_replicas >= replicas`; it is
the only read besides `list_*` for emptiness and `read_namespace` for the label.

## 7. Orchestrator Lab API (`api.py`, `lab/routes.py`)

```
GET  /lab/scenarios   catalog.public()
GET  /lab/status      client.status() + incident lookup
POST /lab/run         {scenario}  -> client.run(id)   (202 {run, ...}; 409/429 passed through)
POST /lab/reset       client.reset()
```

Incident lookup: records in `service.store` with `incident.namespace == status.namespace` and
`created_at >= started_at - 2 s`; primary = oldest with `related_to is None`;
`correlated = count of the rest`.

## 8. Detection, routing, approvals

- Watcher: no code change for REQ-WT-01; tests prove it. `agents.watcher.excludedNamespaces` is
  unchanged and has no lab namespace. `SIGNAL_CATEGORIES["Unhealthy"]` gains `cpu_memory`.
- `Approver(github, lab_github=None)`: `is_lab(incident)` by namespace regex; a lab incident uses
  `lab_github` only, with `render_pr` unchanged. `GitHubClient(..., create_base_from="main")` for
  the lab client creates `lab-remediations` from `main` if `GET git/ref/heads/<base>` is 404.
  `build_lab_client(env)` refuses a base in `{main, master, GITHUB_BASE_BRANCH}`.
- `IncidentService.decide`: learning is skipped for a lab incident, with
  `Learning(status="disabled", error="lab incident")`... rendered by the UI as "Not learned: lab
  incident".

## 9. UI

`ui/lab.html`, `ui/lab.js`, `ui/lab.css` (or additions to `style.css`): a grid of cards from
`/lab/scenarios`, a status panel from `/lab/status` every 3 s. `index.html` gets a nav with a Lab
link and a Log out button (`app.js` wires logout; hidden by `replay.css`). All text by
`textContent`; incident links are `index.html#<id>`.

## 10. Tests

| File | Covers |
|---|---|
| `agents/orchestrator/tests/test_lab_scenarios.py` | 14 scenarios: ids, namespaces, agents, kinds, images, limits, labels, names, phases, per-scenario fixture |
| `.../test_lab_runner.py` | state machine, limits, cleanup, phases, startup, reset, fakes |
| `.../test_lab_kube_guard.py` | AST: only allowed write calls, no Secret/Namespace/RBAC/exec |
| `.../test_auth.py` | every REQ-AU rule |
| `.../test_lab_api.py` | Lab API, incident lookup, errors |
| `.../test_lab_approvals.py` | sandbox branch, no main, no learning, lab client guard |
| `.../test_watcher_lab.py` | cross-scenario and rerun not suppressed; `Unhealthy` routing |
| `gitops/tests/test_lab_chart.py` | rendered RBAC, namespaces, quota, NetworkPolicies, TGB |
| `tools/ui-tests/lab.test.mjs`, `login.test.mjs` | DOM |
| `tools/tests/test_scenario_lab_docs.py` | documents, budget, Terraform text checks |

## Trade-offs

- **In-memory sessions, limits and runner state.** A restart logs out the judge and forgets the
  hourly count; the runner cleans on start, so it is safe, not exact.
- **A scenario that the classifier routes elsewhere is shown as it happened.** The card says
  which agent should answer; the incident shows what did.
- **Shipping the runner in the orchestrator image** puts write-capable code in the image the
  orchestrator uses. It cannot run there: the orchestrator's service account holds no permission.
