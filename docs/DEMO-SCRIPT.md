# Demo video script

Target: **2:50, never over 3:00** (the rules: under three minutes, judges need not watch
more; public on YouTube; shows the project working on the device it was built for).

The story: break the cluster, watch the crew investigate, read the evidence and the similar
past incidents, approve a real pull request, see the approved incident written back as
memory, and look at what it cost.

## Before you record (off camera)

The video is a real run. These steps make it work, and the rehearsal gate keeps the memory
shot honest.

1. **Session and cluster.**
   ```bash
   export PATH=/usr/local/bin:/opt/homebrew/bin:$PATH
   aws sts get-caller-identity
   aws eks update-kubeconfig --name kiroween-prod --region eu-west-1
   kubectl config current-context
   kubectl -n agents get pods
   ```
   All agent pods should be `Running`. The context must name `kiroween-prod`; the chaos
   script refuses to run otherwise.
2. **Port-forwards**, each in its own terminal tab:
   ```bash
   kubectl -n agents port-forward svc/orchestrator 8080:8080
   kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
   ```
   Open <http://localhost:8080/ui/> (it asks for the login from Secret `orchestrator-auth`) and <http://localhost:3000/d/kiroween-agents>. Log in to
   Grafana now, with the password from Secret `grafana-admin`, so it never appears on screen.
3. **Start from a clean scenario.** The watcher reports one incident per workload per 10
   minutes (cooldown 600 s), and an approved incident counts as open for the 5-minute
   correlation window. So:
   ```bash
   chaos/netpol/netpol.sh down
   ```
   and wait at least 10 minutes after the last incident on `chaos-netpol` before the take.
4. **Rehearsal gate for the memory shot.** Run the scenario once off camera
   (`chaos/netpol/netpol.sh up`, wait for `awaiting_approval`) and read what the Archivist
   found:
   ```bash
   curl -s 'localhost:8080/incidents?limit=1' | python3 -c 'import json,sys; r=json.load(sys.stdin)[0]; print(r["status"]); [print(s["origin"], round(s["score"], 3), s["title"]) for s in r["similar_incidents"]]'
   ```
   If a line starts with `learned`, shot 5 may say the learned postmortem was found. If none
   does, use the alternate line in shot 5.
   Observed on 18 Nov (saved in `docs/demo-results/netpol-second-run-real.json`): run 2
   returned `learned 0.846 LRN-2e4fd60547e332f6` first, then `seeded 0.708` and
   `seeded 0.683`; run 1 had returned only the two seeded ones. It is one observation, so
   rehearse anyway.
   Then `chaos/netpol/netpol.sh down`, and wait 10 minutes again.
5. **Check the learned object exists** (shot 7):
   ```bash
   aws s3 ls s3://kiroween-prod-postmortems-255834079310/learned/ --region eu-west-1
   ```
6. **Screen.** 1920x1080, browser zoom so the UI text is readable, no notifications, no
   other tabs. The account ID appears in the bucket name; blur it or accept it. No music: the
   rules prohibit copyrighted music and third-party marks you lack permission for. No logos
   or title cards beyond your own.

## Shot list

Timings are cumulative and add up to 170 seconds. `Cut` marks a place you may edit out waiting
time; say so on screen ("time-lapse").

| Start | Seconds | Shot | On screen | Command |
|---|---:|---|---|---|
| 0:00 | 12 | 1. The problem | The UI header and the six crew cards, idle: "Incidents rise; the crew investigates; only the living approve." | none |
| 0:12 | 13 | 2. Break it | Terminal. The context line shows `kiroween-prod`. | `chaos/netpol/netpol.sh up` |
| 0:25 | 15 | 3. The crew wakes | UI. The live indicator, a new row in "Incident feed", the Necromancer then the Spider animating. Caption: "time-lapse, about a minute". Cut. | none |
| 0:40 | 30 | 4. Evidence and proposal | Click the incident. Show Classification (category `network`), the Evidence table (tool, field, value), then Proposal: one field, the old value, the new value, "requires approval". | none |
| 1:10 | 20 | 5. Memory | "Similar past incidents": titles, scores, and the `seeded` or `learned` badge. The Archivist card lights up. | none |
| 1:30 | 25 | 6. Approve | Decision section: type a name, click "Approve: open the PR". Then the pull request page on GitHub with the patch file. | none |
| 1:55 | 20 | 7. It remembers | Terminal: the object written under `learned/`, then the ingestion job. | see below |
| 2:15 | 25 | 8. What it costs | Grafana "Kiroween Incident Crypt": Bedrock cost, cost per agent, cost per incident, tokens. | none |
| 2:40 | 10 | 9. Close | Repository page: the README title, the `.kiro` folder, the MIT licence. | none |

Shot 7 commands:

```bash
aws s3 ls s3://kiroween-prod-postmortems-255834079310/learned/ --region eu-west-1
aws bedrock-agent list-ingestion-jobs --knowledge-base-id VIBAT27SDH --data-source-id UCADW10RQV --region eu-west-1 --max-results 1 --query 'ingestionJobSummaries[0].[status,startedAt]' --output text
```

After the take, run `chaos/netpol/netpol.sh down`.

## Narration

Spoken pace is about 2.5 words a second. A test keeps each shot under 2.8.

**Shot 1 (12 s).** At three in the morning, Kubernetes breaks. This is the Incident Crypt: six
read-only AI agents that find the fault, remember similar ones, and ask a human before
anything changes.

**Shot 2 (13 s).** I break a real EKS cluster on purpose: a network policy that blocks a
client. It is the same chaos script anyone can run.

**Shot 3 (15 s).** A watcher sees the failing probe. The Necromancer classifies it as a
network problem and wakes the Spider. The animation shows which agent is working. This cut
skips about a minute.

**Shot 4 (30 s).** The Spider reads the cluster, read-only, and lists its evidence. The policy
only allows pods labelled netpol-trusted, but the client is labelled netpol-client. It
proposes one change: that label, from trusted to client. A proposal always needs approval.
The agents cannot write to the cluster.

**Shot 5 (20 s).** The Archivist searches past postmortems and shows similar incidents, each
marked seeded or learned. *If the rehearsal found a learned hit:* the learned one comes from
the last time a person approved a fix here. *Alternate, if it did not:* these are the
seeded postmortems; approved incidents are written back, as the next shot shows.

**Shot 6 (25 s).** I enter my name and approve. The Necromancer opens a real pull request on
GitHub with a patch for the policy. Git history is the audit trail. Merging records the
change; applying it is not automated yet.

**Shot 7 (20 s).** The approved incident is written back as a postmortem, and the knowledge
base is re-indexed, so the next incident can find it. Here is the object in S3 and the
ingestion job.

**Shot 8 (25 s).** Every model call is counted by agent and by incident. Grafana shows the
cost per agent, per incident, and the tokens. One real investigation, a Grafana out-of-memory,
cost about eleven cents.

**Shot 9 (10 s).** Built with Kiro: nine specs, five hooks, three steering files. The code
is on GitHub under the MIT licence.

## What not to claim

- Do not say the pull request fixed the cluster. It records the approved change; nothing
  applies `gitops/remediations/` yet.
- Do not say the crew found the learned postmortem unless the rehearsal gate showed it.
- Do not quote a cost for the netpol incident from memory. Read it off the dashboard, or use
  the Grafana figure above, which is from
  [`docs/demo-results/grafana-oom-real.json`](demo-results/grafana-oom-real.json).
- Do not say the learned hit proves the fix works. A learned postmortem records an approved
  change, not a confirmed one, and the same scenario was used both times.

## After recording

Upload to YouTube as **Public**, with the repository URL in the description. Put the video URL
into the Devpost form and into [`docs/DEVPOST.md`](DEVPOST.md).

> **TODO(user):** the YouTube URL.
