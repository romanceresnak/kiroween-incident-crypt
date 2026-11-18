# scenario-lab — Requirements

> EARS format, as in the earlier specs. Citations: `core:` is `agents-core`, `mui:`
> `memory-and-ui`, `pol:` `polish`, `sub:` `submission`, `rpl:` `replay`.

## Scope

The Kiroween rules say "Access must be provided to an Entrant's working Project for judging and
testing", and allow a private site with login credentials. The live system has no public
endpoint (`mui:REQ-UI-05`), and the GitHub Pages replay (`rpl`) only shows saved records. This
spec makes the real system testable by a judge: a public HTTPS address with a login, and a
Scenario Lab where the judge breaks the live cluster on purpose, 14 ways, and watches the agents
investigate it.

In scope: public access (CloudFront, an internal ALB, WAF), the login, the Scenario Lab (14
scenarios, a lab-runner, limits, UI, sandbox approvals), the chart and Terraform that carry them,
the tests, the documents, and the budget limit.

Out of scope: a custom domain or certificate; changing what the agents may do (they stay
read-only); applying approved changes to the cluster; multi-user accounts; persistence of
incident state across restarts (`mui` trade-off, unchanged).

Facts this spec depends on, read on 18 Nov 2026:
- The orchestrator is the only process that serves `/ui` and the API on port 8080; it has no
  Ingress. The watcher posts to it in-cluster without a credential.
- The VPC has two private /20 subnets tagged `kubernetes.io/role/internal-elb`, and nodes use the
  EKS cluster security group.
- The watcher's incident id and cooldown key contain the namespace, workload kind and name,
  container and reason, and no run identifier.
- The classifier accepts `Unhealthy` only as `network` or `deploy_rollout`
  (`SIGNAL_CATEGORIES`).
- The AWS Load Balancer Controller chart is 3.6.3 (app v3.6.3, published 13 Nov 2026). Its
  official IAM policy at that tag has 29 read-only Describe/Get actions for EC2 and ELB, and
  `RegisterTargets` and `DeregisterTargets` on `targetgroup/*/*`.

## A. Public access (PA)

**REQ-PA-01 (Ubiquitous)** Terraform shall create one CloudFront distribution that serves HTTPS
only with the default `*.cloudfront.net` certificate, redirects HTTP to HTTPS, and caches nothing
(managed policy CachingDisabled), forwarding the viewer's headers, cookies and query string to
the origin (managed policy AllViewer).

**REQ-PA-02 (Ubiquitous)** The origin shall be a CloudFront VPC origin that points to an
**internal** Application Load Balancer in the two private subnets. There shall be no public load
balancer, no public IP and no internet-facing listener for the application.

**REQ-PA-03 (Ubiquitous)** The ALB shall have one HTTP listener on port 80 that forwards to a
target group of type `ip` on port 8080, health-checked on `/healthz`.

**REQ-PA-04 (Ubiquitous)** The ALB security group shall accept port 80 only from the security
group CloudFront creates for the VPC origin, and shall send traffic only to the node security
group on port 8080. The node security group shall accept port 8080 from the ALB security group
and from nothing new.

**REQ-PA-05 (Ubiquitous)** An AWS WAF web ACL (scope CLOUDFRONT, created in `us-east-1`) shall be
attached to the distribution with: a rate-based rule of 300 requests per 5 minutes per IP that
blocks, and the AWS managed `AWSManagedRulesCommonRuleSet`. Both shall publish CloudWatch metrics.

**REQ-PA-06 (Ubiquitous)** Terraform shall output the CloudFront URL, the target group ARN and
the private subnet CIDRs the ALB uses.

**REQ-PA-07 (Ubiquitous)** The monthly AWS Budget limit variable shall default to 400 USD.

## B. Load balancer controller (LC)

**REQ-LC-01 (Ubiquitous)** The AWS Load Balancer Controller shall be installed by an Argo CD
Application from the chart `aws-load-balancer-controller`, pinned to an exact version
(3.6.3), in `kube-system`, with `clusterName`, `region` and `vpcId` set so it never calls the
instance metadata service.

**REQ-LC-02 (Ubiquitous)** Its AWS credentials shall come from EKS Pod Identity for the service
account `aws-load-balancer-controller`. The role shall allow only: the 29 read-only EC2 and ELB
Describe/Get actions the controller uses, and `elasticloadbalancing:RegisterTargets` and
`DeregisterTargets` on the one target group. No `Create*`, `Delete*`, `Modify*`, `Set*` or
`iam:*` action, and no `*` resource for a write action.

**REQ-LC-03 (Ubiquitous)** The service-mutating webhook shall be disabled
(`enableServiceMutatorWebhook: false`) so the controller being down cannot block unrelated
Services. Ingress and Service-type load balancers are not used.

**REQ-LC-04 (Ubiquitous)** The agents chart shall bind the `orchestrator` Service to the target
group with a `TargetGroupBinding` (`targetType: ip`, service port `api`), only when a target
group ARN is configured.

**REQ-LC-05 (Ubiquitous)** The agents chart shall allow ingress to the orchestrator on the API
port from the ALB's subnet CIDRs. The default-deny policy shall stay, and nothing else shall
become reachable from those CIDRs.

**REQ-LC-06 (Event-driven)** When the `TargetGroupBinding` CRD is not yet installed on a first
sync, the agents Application shall not fail permanently: it shall skip the dry run for missing
resources and retry.

## C. Login (AU)

**REQ-AU-01 (Ubiquitous)** The orchestrator shall require a login for `/ui` and for every API
route, except the health probes `/healthz` and `/readyz` and the login routes themselves
(`/login`, its assets, `POST /auth/login`).

**REQ-AU-02 (Ubiquitous)** There shall be one account. Its user name and password shall be read
from the Kubernetes Secret `orchestrator-auth` (keys `username` and `password`), created by the
operator. The chart shall never create it, and no credential shall appear in a manifest, a log, a
response or an error.

**REQ-AU-03 (Unwanted behaviour)** If login is required and the Secret is missing or incomplete,
every protected route shall answer 503 "login is not configured". The system shall not fall back
to open access.

**REQ-AU-04 (Ubiquitous)** Both the user name and the password shall be compared in constant
time, and both comparisons shall always run.

**REQ-AU-05 (Event-driven)** When the credentials are correct, the orchestrator shall set a
session cookie that is `HttpOnly`, `Secure` and `SameSite=Strict`, with `Path=/` and a lifetime
of 8 hours, holding a random server-side session id of at least 256 bits. Logout shall delete the
session.

**REQ-AU-06 (Unwanted behaviour)** After 5 failed attempts from one client address within 15
minutes, that address shall be locked out for 15 minutes and receive 429 with `Retry-After`, even
for a correct password. A successful login shall clear the count. Across all addresses, 100
failures within 15 minutes shall pause logins for 5 minutes.

**REQ-AU-07 (Ubiquitous)** The client address shall be the address that the trusted proxy chain
appended (`TRUSTED_PROXY_HOPS`), never the leftmost, client-supplied value.

**REQ-AU-08 (Ubiquitous)** The same-origin check for decisions, login, and Lab actions shall
accept an `Origin` that equals the request's `Host`, the `X-Forwarded-Host` when proxy hops are
trusted, or a configured public host, and shall still require `application/json`.

**REQ-AU-09 (Ubiquitous)** The watcher shall authenticate with a bearer token from the same
Secret (key `internal-token`). The token shall be accepted only for `POST /incidents`, shall be
compared in constant time, and shall never create a session.

**REQ-AU-10 (Unwanted behaviour)** An unauthenticated browser navigation to `/ui` shall redirect
to `/login`; an unauthenticated API call shall answer 401 with no data.

**REQ-AU-11 (Ubiquitous)** A signed-in user shall be able to log out from the UI.

**REQ-AU-12 (Ubiquitous)** The login page shall obey the same Content-Security-Policy as the UI
(same-origin scripts and styles only) and shall set text only with `textContent`.

**REQ-AU-13 (Ubiquitous)** When the account is the judge, decisions (approve, reject) and
`POST /incidents` through the session shall be limited to lab incidents (namespace `lab-01` to
`lab-14`). The watcher's bearer token is not so limited.

## D. Scenarios (SC)

**REQ-SC-01 (Ubiquitous)** There shall be exactly 14 scenarios, each with a fixed id, a title,
the agent that should answer, a plain description of what breaks, and its own namespace
`lab-01` to `lab-14`:

| ns | id | agent | what breaks | expected signal |
|---|---|---|---|---|
| lab-01 | `oom-killed` | cpu-memory | a container allocates past its memory limit | OOMKilled |
| lab-02 | `cpu-throttling` | cpu-memory | a CPU-bound server under a tiny CPU limit misses its probes | Unhealthy |
| lab-03 | `memory-leak` | cpu-memory | memory grows steadily until the limit kills it | OOMKilled |
| lab-04 | `netpol-blocks-client` | network | a NetworkPolicy admits the wrong client label | Unhealthy |
| lab-05 | `service-selector-mismatch` | network | a Service selects no pods, so it has no endpoints | Unhealthy |
| lab-06 | `wrong-target-port` | network | a Service targets a port nothing listens on | Unhealthy |
| lab-07 | `dns-missing-service` | network | a client calls a Service that does not exist | Unhealthy |
| lab-08 | `bad-image-tag` | deploy-rollout | a rollout uses an image tag that does not exist | ErrImagePull, ImagePullBackOff |
| lab-09 | `crashloop-missing-config-key` | deploy-rollout | the app exits because a ConfigMap lacks the key it reads | CrashLoopBackOff |
| lab-10 | `missing-secret` | deploy-rollout | a container takes a variable from a Secret that does not exist | CreateContainerConfigError |
| lab-11 | `rollout-stuck-readiness` | deploy-rollout | a new revision never passes its readiness probe | Unhealthy |
| lab-12 | `pvc-missing-storageclass` | storage | a claim names a StorageClass that does not exist | ProvisioningFailed |
| lab-13 | `rwx-on-ebs` | storage | a claim asks for ReadWriteMany from block storage | ProvisioningFailed |
| lab-14 | `missing-configmap-mount` | storage | a pod mounts a ConfigMap that does not exist | FailedMount |

**REQ-SC-02 (Ubiquitous)** Each scenario's manifests shall be fixed in the repository, written
with the target namespace and a per-run suffix substituted, never taken from a request. A request
carries only a scenario id.

**REQ-SC-03 (Ubiquitous)** A scenario shall contain only these kinds: Deployment, Service,
ConfigMap, PersistentVolumeClaim, NetworkPolicy. No Secret, no RBAC, no Namespace, no Job, no
DaemonSet, no hostPath, no privileged or host-network setting, and images only from
`public.ecr.aws/docker/library/`.

**REQ-SC-04 (Ubiquitous)** Every workload shall set CPU and memory requests and limits, run as a
non-root user with a read-only root filesystem and no capabilities, and fit the namespace quota.

**REQ-SC-05 (Ubiquitous)** A scenario shall be an ordered list of phases. A phase applies
manifests; it may wait for a Deployment to become ready first. `bad-image-tag` and
`rollout-stuck-readiness` use two phases: a working revision, then a broken one.

**REQ-SC-06 (Ubiquitous)** Every Deployment and PersistentVolumeClaim name shall end with the
run suffix, so a second run of the same scenario is a new workload and is not mistaken by the
watcher for the first (the incident id and the cooldown key contain the workload name).

**REQ-SC-07 (Ubiquitous)** Every object shall carry the labels `kiroween.io/lab: "true"`,
`kiroween.io/lab-scenario` and `kiroween.io/lab-run`, which cleanup selects on.

**REQ-SC-08 (Unwanted behaviour)** None of the scenarios shall depend on the other namespaces,
the agents, or the internet beyond pulling its image.

## E. Lab runner (RN)

**REQ-RN-01 (Ubiquitous)** A separate Deployment, `lab-runner`, shall be the only component that
creates or deletes lab objects. It shall run the orchestrator image with a different entrypoint
(`-m kiroween_orchestrator.lab`), under its own service account `lab-runner-sa`, with no AWS role.

**REQ-RN-02 (Ubiquitous)** Its Kubernetes permission shall be a ClusterRole bound by one
RoleBinding in each of `lab-01` to `lab-14` and nowhere else, limited to create, get, list,
patch, delete and deletecollection on Deployments, Pods, Services, ConfigMaps,
PersistentVolumeClaims and NetworkPolicies, plus `get` on the 14 Namespace objects by name. It
shall have no access to Secrets, no wildcard, no cluster-wide write, and no `exec`, `attach`,
`portforward` or `proxy`.

**REQ-RN-03 (Ubiquitous)** The orchestrator and the six agents shall gain no write permission.

**REQ-RN-04 (Ubiquitous)** The runner's API (`GET /status`, `POST /run`, `POST /reset`) shall
accept only a scenario id from the catalog. It shall be reachable only from the orchestrator pod
(NetworkPolicy), and the orchestrator shall be the only caller.

**REQ-RN-05 (Event-driven)** When a run is accepted, the runner shall check that the namespace
exists and carries `kiroween.io/lab: "true"`, then apply the scenario's phases in the
background, and answer at once with the run id.

**REQ-RN-06 (Ubiquitous)** The runner's Kubernetes write calls shall be limited to `create_*` and
`patch_*` of the five kinds and `delete_collection_*` or `delete_*` of the same kinds. A test
shall walk the source and fail on any other write call, any Secret, Namespace, RBAC or exec call.

**REQ-RN-07 (Event-driven)** When the runner starts, it shall clean all 14 namespaces and report
idle.

**REQ-RN-08 (Ubiquitous)** Pod specs shall be admitted under the Pod Security `baseline` level:
the lab namespaces shall carry that label, and each shall have a ResourceQuota and a LimitRange.

## F. Limits (LM)

**REQ-LM-01 (Unwanted behaviour)** While a run is active or being cleaned up, a new run shall be
refused with 409 and the current scenario named.

**REQ-LM-02 (Unwanted behaviour)** After 10 accepted runs in the last hour, a new run shall be
refused with 429 and the time when the next is allowed. Refused runs do not count.

**REQ-LM-03 (Event-driven)** 15 minutes after a run starts, the runner shall delete everything
the run created and return to idle, without any request.

**REQ-LM-04 (Event-driven)** When "Reset" is pressed, the runner shall delete everything the
current run created at once and return to idle. Reset with nothing running shall succeed.

**REQ-LM-05 (Ubiquitous)** Cleanup shall delete by label, shall wait (up to 90 s) until the
namespace holds no lab objects, and shall report `cleaning` until then. A run shall not start in a
namespace that still holds lab objects.

**REQ-LM-06 (Ubiquitous)** The limits shall be configuration (`maxRunsPerHour`,
`autoCleanupSeconds`), with the values above as defaults.

## G. API (AP)

**REQ-AP-01 (Ubiquitous)** The orchestrator shall expose `GET /lab/scenarios`, `GET /lab/status`,
`POST /lab/run` and `POST /lab/reset`, all behind the login, the two POSTs also behind the
same-origin check.

**REQ-AP-02 (Ubiquitous)** `GET /lab/status` shall return the runner's state, the active scenario
and namespace, when the run started and when it expires, the runs used in the last hour and the
limit, and the incident the run produced: its id, status and category.

**REQ-AP-03 (Ubiquitous)** The incident for a run shall be the oldest incident in the run's
namespace created since the run started that is not correlated to another; correlated signals
shall be counted.

**REQ-AP-04 (Unwanted behaviour)** If the runner is not configured or does not answer, the Lab
API shall answer 503 and say so, never an unhandled error.

**REQ-AP-05 (Ubiquitous)** `POST /lab/run` shall accept exactly `{"scenario": "<id>"}`; unknown
fields and unknown ids shall be refused with 422 or 404.

## H. Detection and routing (WT)

**REQ-WT-01 (Ubiquitous)** The watcher shall not suppress a lab incident because of an incident
in another scenario. A test shall show that the same workload name and reason in `lab-01` and
`lab-02` are both submitted, and that a second run of one scenario (a new workload name) is
submitted again inside the cooldown.

**REQ-WT-02 (Ubiquitous)** The lab namespaces shall not be in the watcher's excluded namespaces.

**REQ-WT-03 (Ubiquitous)** `Unhealthy` shall also be accepted as `cpu_memory`, because a probe
that times out under CPU starvation is a resource problem. The other signal rules shall not
change.

## I. Sandbox approvals (SB)

**REQ-SB-01 (Ubiquitous)** An approved lab incident (namespace `lab-01` to `lab-14`) shall open
its pull request against the branch `lab-remediations` (configurable), never against `main`. If
the configured lab branch is `main`, `master` or the main base branch, the orchestrator shall
refuse to start the lab client.

**REQ-SB-02 (Event-driven)** When `lab-remediations` does not exist, the client shall create it
from the main base branch's head, then open the pull request.

**REQ-SB-03 (Unwanted behaviour)** A lab incident shall never use the main-branch client. With no
GitHub token, a lab approval shall be a dry run.

**REQ-SB-04 (Ubiquitous)** An approved lab incident shall not write a learned postmortem and
shall not start a Knowledge Base ingestion. Its approval shall record that learning was skipped
because it is a lab incident.

**REQ-SB-05 (Ubiquitous)** Non-lab incidents shall behave exactly as before.

## J. UI (UI)

**REQ-UI-01 (Ubiquitous)** A page `/ui/lab.html` shall show one card per scenario with its title,
the crew member that should answer (name and picture), what breaks, and a Run button.

**REQ-UI-02 (Ubiquitous)** The page shall show the live status: idle, running or cleaning, the
scenario, a countdown to the automatic cleanup, runs used this hour, and, when the run produces
one, a link to the incident in the incident feed, with its status and category.

**REQ-UI-03 (Event-driven)** A Reset button shall call `POST /lab/reset`. Run buttons shall be
disabled while a run is active, with the reason shown.

**REQ-UI-04 (Unwanted behaviour)** A 409 or 429 shall be shown as a plain sentence, not a code.
The page shall set all text with `textContent`.

**REQ-UI-05 (Ubiquitous)** The main page shall link to the Lab and offer log out. Both shall be
hidden in the replay build.

**REQ-UI-06 (Ubiquitous)** The Lab shall say that the crew only proposes, that Approve opens a pull
request in a sandbox branch, and that nothing is applied to the cluster.

## K. Terraform and delivery (TF)

**REQ-TF-01 (Ubiquitous)** Every change shall be Terraform in `infra/`, with no hardcoded
account or region, validated, formatted, and planned before it is applied.

**REQ-TF-02 (Ubiquitous)** A plan shall be applied only if it holds creates and in-place updates,
no destroy and no replace. The plan summary shall be reported.

**REQ-TF-03 (Ubiquitous)** The Terraform shall not change the existing cluster, node group,
access entries or Pod Identity roles.

## L. Documents (DC)

**REQ-DC-01** `docs/DEVPOST.md` "working app" shall give the CloudFront URL, say that the login
credentials are in the judges-only instructions field, give testing instructions, and keep the
Pages replay as the public demo link.

**REQ-DC-02** `README.md` shall have a "Scenario Lab" section: what it is, how a judge uses it,
the 14 scenarios, the limits, the sandbox branch, and the security model of the public access.

**REQ-DC-03** The "What's next" section shall no longer list login as future work.

**REQ-DC-04** No credential shall appear in any document. The documents shall say what is not
verified.

## M. Tests (TS)

**REQ-TS-01** Normal tests only, no mutation checks, for: the scenario manifests (shape, kinds,
images, limits, names, labels, one fixture per scenario), the runner (RBAC in the rendered chart,
the write-call guard, limits, cleanup, phases), the login (every rule above), the Lab API, the
watcher, the sandbox approvals, the UI (DOM), the chart and Terraform (format, validate), and the
documents.

**REQ-TS-02** The scenarios shall also be run on the real cluster before the work is reported
done, with the signals and incidents they produced recorded, and any scenario that did not
behave as described in REQ-SC-01 fixed or listed as not working.

## Decisions

- **The runner is separate; the orchestrator stays read-only.** The component that serves the
  login and holds the GitHub token never holds a Kubernetes write permission. A compromised
  orchestrator cannot create workloads; a compromised runner can only touch 14 namespaces.
- **One shared image.** The runner is the orchestrator image with another entrypoint, as the
  watcher is. No new image, repository or tag plumbing, at the price of shipping the runner code
  in the orchestrator image.
- **App-level login, not CloudFront signing.** It works with the default certificate and no
  domain, it is testable, and it protects the port-forward path too.
- **The judge can decide only lab incidents.** A judge who approves a real incident, or one they
  invented, would open a pull request against `main` of the author's repository. The session is
  limited to lab incidents; the sandbox branch is where their pull requests go.
- **Fresh names per run.** Reruns are real new incidents, without changing the watcher's
  deduplication, which is correct for real clusters.
- **Source ranges, not pod identity, for the ALB.** A NetworkPolicy can only name the ALB by its
  subnet CIDRs, which every pod also uses, so the login is the control and the policy only keeps
  other namespaces' ports closed.
- **No mutation checks**, at the author's request. Tests are ordinary ones.

## Not verified when this spec was written

- That the VPC origin's security group is named `CloudFront-VPCOrigins-Service-SG` in this VPC
  (the design looks it up; if it is absent the apply fails and the plan is revisited).
- That the least-privilege controller policy is enough for a TargetGroupBinding. It is the
  official policy's read-only part plus target registration; a missing action would show in the
  controller's log.
- That `AWSManagedRulesCommonRuleSet` does not block this application's JSON requests.
- How the classifier routes each scenario's real signal. REQ-TS-02 measures it.
