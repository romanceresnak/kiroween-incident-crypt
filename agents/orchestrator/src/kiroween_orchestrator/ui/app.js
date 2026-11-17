// Kiroween Incident Crypt UI (memory-and-ui REQ-UI).
// Everything from the API is cluster-controlled text: it is only ever set with
// textContent or as an attribute, never parsed as HTML (REQ-UI-04).
"use strict";

const POLL_MS = 3000;
const ART = {
  "orchestrator": "art/necromancer.svg",
  "cpu-memory": "art/vampire.svg", "cpu_memory": "art/vampire.svg",
  "network": "art/spider.svg",
  "deploy-rollout": "art/zombie.svg", "deploy_rollout": "art/zombie.svg",
  "storage": "art/ghost.svg",
  "incident-memory": "art/archivist.svg",
};
const CREW_NAME = {
  "orchestrator": "Necromancer", "cpu-memory": "Vampire", "network": "Spider",
  "deploy-rollout": "Zombie", "storage": "Ghost", "incident-memory": "Archivist",
};
const SPECIALIST_AGENT = {
  cpu_memory: "cpu-memory", network: "network", deploy_rollout: "deploy-rollout",
  storage: "storage",
};

let selected = null;

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function artFor(record) {
  const category = record.classification && record.classification.category;
  return ART[category] || ART.orchestrator;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString();
}

function badge(status) {
  return el("span", { class: "badge " + status, text: status.replace(/_/g, " ") });
}

async function getJSON(path) {
  const response = await fetch(path, { headers: { "Accept": "application/json" } });
  if (!response.ok) throw new Error(path + " returned " + response.status);
  return response.json();
}

function setLive(ok, text) {
  const live = document.getElementById("live");
  live.className = "live " + (ok ? "ok" : "down");
  live.textContent = text;
}

// --- feed -------------------------------------------------------------------------------

function renderFeed(records) {
  const list = document.getElementById("feed");
  document.getElementById("feed-empty").hidden = records.length > 0;
  list.replaceChildren(...records.map((r) => {
    const i = r.incident;
    const item = el("button", {
      class: "feed-item", type: "button",
      "aria-current": i.incident_id === selected ? "true" : "false",
      onclick: () => select(i.incident_id),
    },
      el("img", { src: artFor(r), alt: "" }),
      el("div", {},
        el("div", { class: "title" }, i.reason, badge(r.status)),
        el("div", { class: "sub", text: `${i.namespace}/${i.workload.name} · ${when(r.created_at)}` })));
    return el("li", {}, item);
  }));
  markBusy(records);
}

function markBusy(records) {
  const busy = new Set();
  for (const r of records) {
    if (r.status !== "processing" && r.status !== "accepted") continue;
    busy.add("orchestrator").add("incident-memory");
    const category = r.classification && r.classification.category;
    if (SPECIALIST_AGENT[category]) busy.add(SPECIALIST_AGENT[category]);
  }
  for (const fig of document.querySelectorAll(".crew figure")) {
    fig.classList.toggle("busy", busy.has(fig.dataset.agent));
  }
}

// --- detail -----------------------------------------------------------------------------

function section(title, ...content) {
  return [el("h3", { text: title }), ...content];
}

function kv(pairs) {
  return el("dl", { class: "kv" },
    pairs.filter(([, v]) => v !== undefined && v !== null && v !== "")
      .flatMap(([k, v]) => [el("dt", { text: k }), el("dd", {}, v)]));
}

function evidenceTable(evidence) {
  if (!evidence || !evidence.length) return el("p", { class: "muted", text: "No evidence recorded." });
  return el("table", {},
    el("thead", {}, el("tr", {}, el("th", { text: "Tool" }), el("th", { text: "Field" }),
                      el("th", { text: "Value" }))),
    el("tbody", {}, evidence.map((e) => el("tr", {},
      el("td", {}, el("code", { text: e.tool })), el("td", { text: e.field }),
      el("td", {}, el("code", { text: e.value }))))));
}

function proposalView(inv) {
  if (!inv) return el("p", { class: "muted", text: "No investigation yet." });
  const out = [kv([["Outcome", inv.outcome], ["Root cause", inv.diagnosis.root_cause],
                   ["Confidence", inv.diagnosis.confidence]])];
  out.push(...section("Evidence", evidenceTable(inv.diagnosis.evidence)));
  const p = inv.proposal;
  if (!p) {
    out.push(...section("Proposal", el("p", { class: "muted",
      text: "No proposal: the subject is healthy now or nothing is wrong." })));
  } else {
    const target = p.target ? `${p.target.kind}/${p.target.name} in ${p.target.namespace}` : "";
    out.push(...section("Proposal",
      kv([["Action", p.action], ["Target", target], ["Container", p.target && p.target.container],
          ["Rationale", p.rationale], ["Risk", p.risk], ["Delivery", p.delivery]]),
      p.field ? el("div", { class: "change" }, el("code", { text: p.field + ": " }),
        el("span", { class: "from", text: String(p.current) }), " → ",
        el("span", { class: "to", text: String(p.proposed) })) : null));
  }
  if (inv.validation_notes && inv.validation_notes.length) {
    out.push(...section("Validation notes",
      el("ul", {}, inv.validation_notes.map((n) => el("li", { text: n })))));
  }
  return out;
}

function similarView(items) {
  if (!items || !items.length) {
    return el("p", { class: "muted", text: "The Archivist found no similar past incidents." });
  }
  return el("ol", { class: "similar" }, items.map((s) => el("li", {},
    el("strong", { text: s.title }), " ", el("span", { class: "score", text: s.score.toFixed(2) }),
    s.category ? el("span", { class: "badge", text: s.category }) : null,
    el("div", { text: "Fix that worked: " + (s.fix || "(not recorded)") }))));
}

function relatedView(record) {
  const out = [];
  if (record.related_to) {
    out.push(el("p", {}, "Attached to ",
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); select(record.related_to); },
                text: record.related_to }), " (same root cause)."));
  }
  if (record.related_incidents && record.related_incidents.length) {
    out.push(el("ul", {}, record.related_incidents.map((r) => el("li", {},
      `${r.reason} on ${r.workload.kind}/${r.workload.name} (${r.match.replace(/_/g, " ")})`))));
  }
  return out.length ? out : [el("p", { class: "muted", text: "None." })];
}

function costView(usage) {
  const rows = Object.entries(usage || {});
  if (!rows.length) return el("p", { class: "muted", text: "No model calls recorded." });
  let total = 0;
  const body = rows.map(([agent, u]) => {
    if (typeof u.cost_usd === "number") total += u.cost_usd;
    return el("tr", {},
      el("td", {}, el("img", { src: ART[agent] || ART.orchestrator, alt: "", width: 24, height: 24 }),
         " ", CREW_NAME[agent] || agent),
      el("td", { text: u.input_tokens }), el("td", { text: u.output_tokens }),
      el("td", { text: typeof u.cost_usd === "number" ? "$" + u.cost_usd.toFixed(4) : "n/a" }));
  });
  return el("table", {},
    el("thead", {}, el("tr", {}, ["Agent", "Tokens in", "Tokens out", "Cost"].map(
      (h) => el("th", { text: h })))),
    el("tbody", {}, body,
      el("tr", {}, el("th", { text: "Total" }), el("td"), el("td"),
         el("th", { text: "$" + total.toFixed(4) }))));
}

function safeLink(url) {
  return typeof url === "string" && url.startsWith("https://github.com/") ? url : null;
}

function approvalView(record) {
  const a = record.approval;
  if (a) {
    const pr = a.pull_request;
    const out = [kv([["Decision", a.decision], ["By", a.by], ["At", when(a.at)], ["Comment", a.comment]])];
    if (pr) {
      const link = safeLink(pr.url);
      out.push(kv([["Delivery", pr.mode === "github" ? "GitHub pull request" : "dry run"],
                   ["Pull request", link ? el("a", { href: link, target: "_blank",
                     rel: "noopener noreferrer", text: link }) : null],
                   ["Title", pr.title], ["Branch", pr.branch], ["File", pr.path],
                   ["Error", pr.error ? el("span", { class: "error", text: pr.error }) : null]]));
      if (pr.content) out.push(el("pre", { text: pr.content }));
    }
    return out;
  }
  if (record.status !== "awaiting_approval") return [];
  const name = el("input", { id: "approver", name: "by", maxlength: 64, autocomplete: "name",
                             value: localStorage.getItem("kiroween.approver") || "" });
  const comment = el("textarea", { id: "comment", name: "comment", maxlength: 500, rows: 2 });
  const status = el("p", { class: "error", role: "alert" });
  const send = (decision) => async (event) => {
    const by = name.value.trim();
    if (!/^[A-Za-z0-9 ._@-]{1,64}$/.test(by)) {
      status.textContent = "Enter your name (letters, digits, space, . _ @ -).";
      return;
    }
    localStorage.setItem("kiroween.approver", by);
    for (const b of event.target.parentElement.querySelectorAll("button")) b.disabled = true;
    try {
      const response = await fetch(`/incidents/${encodeURIComponent(record.incident.incident_id)}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ by, comment: comment.value }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(typeof detail.detail === "string" ? detail.detail : "HTTP " + response.status);
      }
      await refreshDetail();
    } catch (err) {
      status.textContent = "Not recorded: " + err.message;
      for (const b of event.target.parentElement.querySelectorAll("button")) b.disabled = false;
    }
  };
  return [el("div", { class: "decision" },
    el("label", { for: "approver", text: "Your name" }), name,
    el("label", { for: "comment", text: "Comment (optional)" }), comment,
    el("div", { class: "buttons" },
      el("button", { class: "approve", type: "button", onclick: send("approve"),
                     text: "Approve: open the PR" }),
      el("button", { class: "reject", type: "button", onclick: send("reject"), text: "Reject" })),
    status)];
}

function renderDetail(record) {
  const i = record.incident;
  const c = record.classification;
  const article = document.getElementById("detail");
  article.replaceChildren(
    el("div", { class: "detail-head" },
      el("img", { src: artFor(record), alt: "" }),
      el("div", {},
        el("h2", {}, i.reason, badge(record.status)),
        el("div", { class: "muted", text: `${i.workload.kind} ${i.namespace}/${i.workload.name}` }))),
    ...section("Incident", kv([["ID", el("code", { text: i.incident_id })], ["Pod", i.pod],
      ["Container", i.container], ["Observed", when(i.observed_at)], ["Message", i.message],
      ["Error", record.error ? el("span", { class: "error", text: record.error }) : null]])),
    ...section("Classification", c ? kv([["Category", c.category], ["Confidence", c.confidence],
      ["Rationale", c.rationale]]) : el("p", { class: "muted", text: "Not classified yet." })),
    ...section("Investigation", proposalView(record.investigation)),
    ...section("Similar past incidents", similarView(record.similar_incidents)),
    ...section("Related incidents", relatedView(record)),
    ...section("Cost per agent", costView(record.usage_by_agent)),
    ...section("Decision", approvalView(record)));
  document.getElementById("detail-empty").hidden = true;
  article.hidden = false;
}

// --- polling ----------------------------------------------------------------------------

async function refreshFeed() {
  try {
    renderFeed(await getJSON("/incidents?limit=50"));
    setLive(true, "live · " + new Date().toLocaleTimeString());
  } catch (err) {
    setLive(false, "orchestrator unreachable");
  }
}

async function refreshDetail() {
  if (!selected) return;
  // Do not redraw while the operator is typing a decision.
  const active = document.activeElement;
  if (active && (active.id === "approver" || active.id === "comment")) return;
  try {
    renderDetail(await getJSON("/incidents/" + encodeURIComponent(selected)));
  } catch (err) {
    setLive(false, "could not load " + selected);
  }
}

function select(id) {
  selected = id;
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  refreshDetail();
  refreshFeed();
}

const fromHash = decodeURIComponent(location.hash.slice(1));
if (fromHash) select(fromHash);
refreshFeed();
setInterval(refreshFeed, POLL_MS);
setInterval(refreshDetail, POLL_MS);
