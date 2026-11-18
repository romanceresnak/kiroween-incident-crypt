# I let AI agents diagnose my Kubernetes cluster, then made them ask permission

*This post is part of my entry to the Kiroween 2026 hackathon (category: Frankenstein). The
project, Kiroween Incident Crypt, was built with Kiro. The code is MIT licensed:
<https://github.com/romanceresnak/kiroween-incident-crypt>.*

At some point every cluster has a bad night: a pod is killed for memory, an image will not
pull, a network policy cuts off a service. The fix is often small. Finding it is the slow
part, and I wanted to see how much of that an AI agent could do while keeping a human in
charge of every change.

So I built a crew of six agents for Amazon EKS, with spooky names because it is a Halloween
hackathon. A watcher notices a broken workload. The Necromancer classifies it and hands it to
a specialist: the Vampire (memory and CPU), the Spider (networking), the Zombie (deploys and
images) or the Ghost (storage). A sixth agent, the Archivist, looks up similar past incidents.
The specialist reads the cluster, lists its evidence and proposes one change. Then it stops
and waits for a person.

## One incident, start to finish

I broke a cluster on purpose with a script: a NetworkPolicy that only lets pods labelled
`netpol-trusted` reach a server, while the client is labelled `netpol-client`. Its readiness
probe times out.

The Spider's answer was six pieces of evidence, each with the tool, the field and the value:
the policy allows `netpol-trusted`, the client pod carries `netpol-client`, the probe fails
with `wget: download timed out`, and the server's endpoints are healthy. Its proposal was one
change:

```
spec.ingress[0].from[0].podSelector.matchLabels.app:  netpol-trusted -> netpol-client
```

From the incident being submitted to my click on Approve took 40 seconds, including the
investigation and the time I spent reading it. Approve opens a real pull request, because the
agents have no way to change the cluster themselves. This one is
[pull request #2](https://github.com/romanceresnak/kiroween-incident-crypt/pull/2).

## It remembers

When I approve, the incident is also written back as a postmortem and the knowledge base is
re-indexed. Fourteen minutes later I ran the same scenario again. The first time, the Archivist
had returned two seeded postmortems (scores 0.708 and 0.683). The second time, the postmortem
the crew had written after my approval came back first, at 0.846, ahead of both. The second
run opened [pull request #3](https://github.com/romanceresnak/kiroween-incident-crypt/pull/3)
and wrote a second postmortem.

That is one observation of the same scenario twice. It shows that the loop works. It does not
show that the crew would find a useful postmortem for a different failure. A learned
postmortem also records an approved change, not a confirmed fix, and it says so in its own
text.

## The guardrails are the product

- The agents have read-only Kubernetes permissions: `get`, `list`, `watch`, no secrets. A test
  walks the source and fails on any call that would write.
- Every proposal says `requires_approval: true`, and code, not the model, decides whether a
  proposed change is allowed.
- Each agent has its own AWS role through EKS Pod Identity, and default-deny network policies
  keep the specialists reachable only from the orchestrator.
- The UI has no login. It is only reachable through `kubectl port-forward`, so I did not expose
  it. That is a trade-off, and the README says so.

One limit matters. **Merging the pull request does not change the cluster yet.** Nothing in
Argo CD applies `gitops/remediations/`, and the demo workloads are not managed by it. The pull
request is an audit trail of an approved change. I would rather say that here than have you
find it in the repo.

## What an incident costs

Every Bedrock call is counted by agent and by incident and shown in Grafana. Five real
incidents so far: a Grafana out-of-memory that the crew diagnosed on the same cluster ($0.1104),
the two network runs ($0.0790 and $0.1052), a bad image tag ($0.0609) and a missing
StorageClass ($0.0508). The prices are the ones I typed into the chart,
and I have not checked them against the AWS price list.

## How Kiro fit in

Kiro wrote and ran most of this under the specs. My part was reviewing its plans, approving
the AWS changes it stopped to ask about, and pushing.

Everything started as a spec: requirements with numbered IDs, a design, and tasks with one
commit each. There are eight specs, 84 tasks and more than 320 requirement IDs, and the tests
and commit messages cite the IDs. Three steering files hold the rules that should never be
renegotiated: agents are read-only, fixes are pull requests, AWS access uses Pod Identity.

The specs also kept decisions I would have forgotten. One records that having the orchestrator
call the specialists over HTTP costs a network hop, and that I accept it because running a
specialist inside the orchestrator would force the orchestrator to hold the specialist's
Kubernetes permissions. Another notes that `incident_id` as a metric label has unbounded
cardinality, fine for a demo and a problem at volume. I also gave Kiro stop conditions in the
spec: stop for an AWS permission I had not approved, a Terraform plan that replaces or destroys
something, a step that fails twice, or an ambiguous requirement. Everything else it did on its
own, one commit per task.

The part I trust most is how many mistakes surfaced when something was actually run:

- CI failed with an AWS trust error. GitHub issues immutable OIDC subjects for newer
  repositories, so a trust policy written with the repository name never matched.
- A CI step died with exit code 254 because GitHub runs `bash -e`, so a bare command
  substitution aborted before the error handler. The fix came with tests that run the real
  step under GitHub's exact shell.
- The UI printed `[object HTMLDListElement],[object HTMLHeadingElement],...` and had no
  Approve button, because a DOM call stringifies an array. Eleven jsdom tests now run the real
  page script, and they all failed against the old one.
- Retrieval returned one postmortem three times, because the service returns chunks. It now
  returns one hit per document.

The crew also diagnosed a real out-of-memory in Grafana on my own cluster. It found the
256Mi limit with 229 MiB in use right after a restart, and it proposed 512Mi for the Grafana
container. The pod's two sidecars were also being killed, and it mentioned them without
proposing a change for them.

The habit I would keep is one requirement and one test for every rule that must hold: the
read-only permissions, the approval flag, the network policies. Kiro made that cheap, because
the requirement, the code and the test live in the same task.

What I did not use: custom agents, property-based testing and Kiro powers. I added five Kiro
hooks last, one per area of the repository (ruff and pytest, Terraform checks, Helm lint,
shellcheck, UI tests). They have been tested against fake tools but never run inside a Kiro
app, so I cannot tell you how they behave there.

## What is not finished

- Approved changes are recorded, not applied.
- The `badimage` and `pvc` scenarios were re-run after the watcher changes and stopped at
  `awaiting_approval`. I did not approve them, so I do not know that their proposals work.
- The similarity threshold of 0.5 is a first guess.
- Incident state is in memory, so a restart of the orchestrator loses it.

The repository has the specs, the steering files, the hooks and the saved records of the real
incidents: <https://github.com/romanceresnak/kiroween-incident-crypt>. A read-only replay of the
real UI, built from those records, is at
<https://romanceresnak.github.io/kiroween-incident-crypt/>. It is a replay: the live system has no public
endpoint, because its UI can open pull requests and has no login.
