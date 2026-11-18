// DOM tests for the orchestrator's /ui (memory-and-ui REQ-UI-02, UI-04).
//
// The real index.html and app.js run in jsdom; only fetch is faked. On 17 Nov the live UI
// printed "[object HTMLDListElement],[object HTMLHeadingElement],..." for the Investigation,
// Related incidents and Decision sections and the Approve/Reject buttons were missing:
// arrays of nodes were handed to replaceChildren, which turns them into text.
//
// UI_APP_JS points the tests at another copy of app.js (used to prove they fail on the bug).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const UI = new URL("../../agents/orchestrator/src/kiroween_orchestrator/ui/", import.meta.url);
const HTML = readFileSync(new URL("index.html", UI), "utf8");
const APP = readFileSync(process.env.UI_APP_JS ?? new URL("app.js", UI), "utf8");

const NOW = "2026-11-17T15:00:00Z";

function record(overrides = {}) {
  return {
    incident: {
      incident_id: "inc-1", source: "watcher", reason: "ImagePullBackOff",
      namespace: "chaos-badimage", workload: { kind: "Deployment", name: "badimage-demo" },
      pod: "badimage-demo-b2-y", container: "web", observed_at: NOW,
      message: "ImagePullBackOff: Back-off pulling image", depends_on: [], similar_incidents: [],
    },
    status: "awaiting_approval",
    classification: { category: "deploy_rollout", confidence: "high",
                      rationale: "The new revision cannot pull its image." },
    investigation: {
      outcome: "action_required",
      diagnosis: {
        root_cause: "Revision 2 uses an image tag that does not exist.", confidence: "high",
        evidence: [
          { tool: "get_rollout_history", field: "revision 1 image",
            value: "public.ecr.aws/docker/library/python:3.12-slim" },
          { tool: "get_pod", field: "waiting.reason", value: "ImagePullBackOff" }],
      },
      proposal: {
        action: "set_field",
        target: { namespace: "chaos-badimage", kind: "Deployment", name: "badimage-demo",
                  container: null },
        field: "spec.template.spec.containers[name=web].image",
        current: "public.ecr.aws/docker/library/python:0.0.0-does-not-exist",
        proposed: "public.ecr.aws/docker/library/python:3.12-slim",
        rationale: "Roll back to the image revision 1 ran.", risk: "Reverts revision 2.",
        requires_approval: true,
        delivery: "Apply as a pull request against /gitops after a human approves it.",
      },
      usage: { input_tokens: 1200, output_tokens: 300, cost_usd: 0.0089 },
      validation_notes: ["checked against the rollout history"],
    },
    delegation: "tool",
    usage_by_agent: {
      orchestrator: { input_tokens: 900, output_tokens: 80, cost_usd: 0.0014 },
      "deploy-rollout": { input_tokens: 1200, output_tokens: 300, cost_usd: 0.0089 },
    },
    error: null,
    related_incidents: [
      { incident_id: "inc-2", reason: "ErrImagePull",
        workload: { kind: "Deployment", name: "badimage-demo" },
        pod: "badimage-demo-b2-y", match: "same_workload", observed_at: NOW }],
    related_to: null,
    similar_incidents: [
      { title: "PM-2026-072: search-indexer rollout stuck on a tag that was never pushed",
        category: "deploy_rollout", score: 0.78,
        fix: "Rolled the image back to the last Ready revision.",
        source: "s3://b/postmortems/05.md", excerpt: "..." }],
    approval: null,
    created_at: NOW, updated_at: NOW,
    ...overrides,
  };
}

function tick(ms = 10) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function until(check, what, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (check()) return;
    await tick();
  }
  assert.fail(`timed out waiting for ${what}`);
}

// Opens the UI for `rec`. `server.respond` can override a response; `server.calls` logs fetches.
async function open(rec, { respond } = {}) {
  const calls = [];
  const state = { rec };
  const dom = new JSDOM(HTML, {
    url: `http://localhost:8080/ui/#${rec.incident.incident_id}`,
    runScripts: "outside-only", pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = async (url, init = {}) => {
    const call = { url, method: init.method ?? "GET", headers: init.headers ?? {},
                   body: init.body ? JSON.parse(init.body) : null };
    calls.push(call);
    const custom = respond && respond(call, state);
    const body = custom ?? (url.startsWith("/incidents?") ? [state.rec] : state.rec);
    const status = custom && custom.__status ? custom.__status : 200;
    return { ok: status < 400, status, json: async () => (custom?.__json ?? body) };
  };
  window.eval(APP);
  await until(() => !window.document.getElementById("detail").hidden, "the detail to render");
  return { window, document: window.document, calls, state, close: () => window.close() };
}

const detailOf = (ui) => ui.document.getElementById("detail");
const headings = (ui) => [...detailOf(ui).querySelectorAll("h3")].map((h) => h.textContent);

function assertNoLeaks(ui) {
  const text = ui.document.body.textContent;
  assert.ok(!text.includes("[object "), `found "[object " in: ${text.slice(0, 300)}`);
  assert.doesNotMatch(text, /\b(null|undefined|NaN)\b/);
}

test("a full incident renders every section as elements, with no stringified nodes", async () => {
  const ui = await open(record());
  try {
    assertNoLeaks(ui);
    assert.deepEqual(headings(ui), [
      "Incident", "Classification", "Investigation", "Evidence", "Proposal",
      "Validation notes", "Similar past incidents", "Related incidents", "Cost per agent",
      "Decision"]);
    // Every direct child of the article is an element: nothing was turned into a text node.
    for (const node of detailOf(ui).childNodes) {
      assert.equal(node.nodeType, 1, `stray ${node.nodeName}: ${node.textContent.slice(0, 80)}`);
    }
    const text = detailOf(ui).textContent;
    assert.match(text, /Revision 2 uses an image tag that does not exist/);   // Investigation
    assert.match(text, /get_rollout_history/);                                // evidence table
    assert.match(text, /spec\.template\.spec\.containers\[name=web\]\.image: /);
    assert.match(text, /checked against the rollout history/);               // validation notes
    assert.match(text, /ErrImagePull on Deployment\/badimage-demo \(same workload\)/); // Related
    assert.match(text, /PM-2026-072/);                                        // Similar
    assert.match(text, /\$0\.0103/);                                          // cost total
    assert.ok(detailOf(ui).querySelectorAll("dl").length >= 4);
    assert.equal(detailOf(ui).querySelectorAll("table").length, 2);
  } finally {
    ui.close();
  }
});

test("the Approve and Reject controls exist, with the name and comment fields", async () => {
  const ui = await open(record());
  try {
    const approve = detailOf(ui).querySelector("button.approve");
    const reject = detailOf(ui).querySelector("button.reject");
    assert.ok(approve, "Approve button is missing");
    assert.ok(reject, "Reject button is missing");
    assert.equal(approve.textContent, "Approve: open the PR");
    assert.equal(reject.textContent, "Reject");
    assert.ok(detailOf(ui).querySelector("input#approver"));
    assert.ok(detailOf(ui).querySelector("textarea#comment"));
    // They sit inside the Decision section, after its heading.
    const decision = [...detailOf(ui).children].filter((c) => c.className === "decision");
    assert.equal(decision.length, 1);
    assert.equal(decision[0].previousElementSibling.textContent, "Decision");
  } finally {
    ui.close();
  }
});

test("Approve posts JSON for the incident and the UI shows the decision", async () => {
  const ui = await open(record(), {
    respond: (call, state) => {
      if (call.method !== "POST") return undefined;
      state.rec = record({
        status: "approved",
        approval: { decision: "approved", by: call.body.by, at: NOW, comment: call.body.comment,
                    pull_request: { mode: "dry_run", title: "fix(chaos-badimage): image",
                                    branch: "remediation/inc-1",
                                    path: "gitops/remediations/chaos-badimage/deployment-x.yaml",
                                    content: "apiVersion: apps/v1\n", body: "b", url: null,
                                    error: null } },
      });
      return state.rec;
    },
  });
  try {
    ui.document.getElementById("approver").value = "Roman C.";
    ui.document.getElementById("comment").value = "looks right";
    ui.document.querySelector("button.approve").click();
    await until(() => !detailOf(ui).querySelector("button.approve")
                      && /approved/.test(detailOf(ui).textContent), "the approved view");
    const post = ui.calls.find((c) => c.method === "POST");
    assert.equal(post.url, "/incidents/inc-1/approve");
    assert.equal(post.headers["Content-Type"], "application/json");
    assert.deepEqual(post.body, { by: "Roman C.", comment: "looks right" });
    assertNoLeaks(ui);
    assert.match(detailOf(ui).textContent, /dry run/);
    assert.equal(detailOf(ui).querySelector("pre").textContent, "apiVersion: apps/v1\n");
    assert.equal(detailOf(ui).querySelector("button.reject"), null);
  } finally {
    ui.close();
  }
});

test("a missing name is refused locally and a server refusal re-enables the buttons", async () => {
  const ui = await open(record(), {
    respond: (call) => (call.method === "POST"
      ? { __status: 409, __json: { detail: "incident is approved, not awaiting_approval" } }
      : undefined),
  });
  try {
    ui.document.querySelector("button.approve").click();
    await tick(50);
    assert.equal(ui.calls.filter((c) => c.method === "POST").length, 0);
    assert.match(detailOf(ui).querySelector(".decision .error").textContent, /Enter your name/);

    ui.document.getElementById("approver").value = "roman";
    ui.document.querySelector("button.reject").click();
    await until(() => /Not recorded/.test(detailOf(ui).textContent), "the server error");
    assert.match(detailOf(ui).textContent, /not awaiting_approval/);
    assert.equal(ui.document.querySelector("button.approve").disabled, false);
    assert.equal(ui.calls.at(-1).url, "/incidents/inc-1/reject");
  } finally {
    ui.close();
  }
});

test("an incident that is still being processed renders without leaks or buttons", async () => {
  const ui = await open(record({ status: "processing", classification: null, investigation: null,
                                 related_incidents: [], similar_incidents: [],
                                 usage_by_agent: {} }));
  try {
    assertNoLeaks(ui);
    assert.match(detailOf(ui).textContent, /Not classified yet/);
    assert.match(detailOf(ui).textContent, /No investigation yet/);
    assert.match(detailOf(ui).textContent, /No model calls recorded/);
    assert.equal(detailOf(ui).querySelector("button.approve"), null);
    assert.deepEqual(headings(ui).slice(-1), ["Decision"]);
  } finally {
    ui.close();
  }
});

test("a transient outcome (no proposal) says so and offers no decision", async () => {
  const rec = record({ status: "completed" });
  rec.investigation = { ...rec.investigation, outcome: "transient", proposal: null };
  const ui = await open(rec);
  try {
    assertNoLeaks(ui);
    assert.match(detailOf(ui).textContent, /No proposal: the subject is healthy now/);
    assert.equal(detailOf(ui).querySelector("button.approve"), null);
  } finally {
    ui.close();
  }
});

test("a proposal without a field renders without a stray null", async () => {
  const rec = record();
  rec.investigation.proposal = { ...rec.investigation.proposal, action: "investigate_further",
                                 field: null, current: null, proposed: null, target: null };
  const ui = await open(rec);
  try {
    assertNoLeaks(ui);
    assert.match(detailOf(ui).textContent, /investigate_further/);
  } finally {
    ui.close();
  }
});

test("a correlated incident links to its primary", async () => {
  const ui = await open(record({ status: "correlated", related_to: "inc-0",
                                 related_incidents: [], investigation: null }));
  try {
    assertNoLeaks(ui);
    const link = [...detailOf(ui).querySelectorAll("a")].find((a) => a.textContent === "inc-0");
    assert.ok(link, "link to the primary incident is missing");
    assert.equal(detailOf(ui).querySelector("button.approve"), null);
  } finally {
    ui.close();
  }
});

test("pull request links are only ever GitHub URLs", async () => {
  const approved = (url) => record({
    status: "approved",
    approval: { decision: "approved", by: "r", at: NOW, comment: "",
                pull_request: { mode: "github", title: "t", branch: "b", path: "p",
                                content: "", body: "", url, error: null } },
  });
  const good = await open(approved("https://github.com/o/r/pull/7"));
  try {
    const link = good.document.querySelector("a[href^='https://github.com/']");
    assert.ok(link);
    assert.equal(link.getAttribute("rel"), "noopener noreferrer");
    assert.equal(link.getAttribute("target"), "_blank");
  } finally {
    good.close();
  }
  const bad = await open(approved("javascript:alert(1)"));
  try {
    assert.equal(bad.document.querySelector("a[href^='javascript']"), null);
    assertNoLeaks(bad);
  } finally {
    bad.close();
  }
});

test("cluster text is shown as text, never parsed as HTML", async () => {
  const rec = record();
  rec.incident.message = "<img src=x onerror=alert(1)> <script>alert(2)</script>";
  rec.investigation.diagnosis.evidence[0].value = "<b>bold</b><iframe src=//evil></iframe>";
  rec.similar_incidents[0].title = "<svg onload=alert(3)>";
  const ui = await open(rec);
  try {
    const text = detailOf(ui).textContent;
    assert.ok(text.includes("<img src=x onerror=alert(1)>"));
    assert.ok(text.includes("<svg onload=alert(3)>"));
    assert.equal(detailOf(ui).querySelector("iframe, script, b, svg[onload], img[src='x']"), null);
    assertNoLeaks(ui);
  } finally {
    ui.close();
  }
});

test("the feed lists incidents newest first as buttons and selecting one loads it", async () => {
  const ui = await open(record());
  try {
    await until(() => ui.document.querySelectorAll("#feed .feed-item").length === 1, "the feed");
    const item = ui.document.querySelector("#feed .feed-item");
    assert.equal(item.tagName, "BUTTON");
    assert.match(item.textContent, /ImagePullBackOff/);
    assert.match(item.textContent, /awaiting approval/);
    assert.equal(ui.document.getElementById("feed-empty").hidden, true);
    assertNoLeaks(ui);
  } finally {
    ui.close();
  }
});

// --- polish: learned postmortems (REQ-LRN-08) --------------------------------------------------

const hit = (over) => ({ title: "PM", category: "network", score: 0.7, fix: "relabel the client",
                         source: "s3://b/x.md", excerpt: "", ...over });

test("similar incidents say where they came from, and a learned fix is not called one that worked", async () => {
  const rec = record();
  rec.similar_incidents = [
    hit({ title: "PM-2026-058: seeded one", origin: "seeded", fix: "relabel the client" }),
    hit({ title: "LRN-inc-9: learned one", origin: "learned", score: 0.64,
          fix: "Approved fix (not yet confirmed): set a on NetworkPolicy/p from x to y." })];
  const ui = await open(rec);
  try {
    const items = [...detailOf(ui).querySelectorAll("ol.similar li")];
    assert.equal(items.length, 2);
    const [seeded, learned] = items;
    assert.equal(seeded.querySelector(".badge.source-seeded").textContent, "source: seeded");
    assert.equal(learned.querySelector(".badge.source-learned").textContent, "source: learned");
    assert.equal(seeded.querySelector(".badge.source-learned"), null);
    assert.match(seeded.textContent, /Fix that worked: relabel the client/);
    assert.doesNotMatch(learned.textContent, /Fix that worked/);
    assert.match(learned.textContent, /Approved fix \(not yet confirmed\)/);
    assertNoLeaks(ui);
  } finally {
    ui.close();
  }
});

test("a hit from an older record without an origin is shown as seeded", async () => {
  const rec = record();
  rec.similar_incidents = [hit({ title: "old record" })];
  const ui = await open(rec);
  try {
    assert.ok(detailOf(ui).querySelector(".similar .badge.source-seeded"));
    assert.equal(detailOf(ui).querySelector(".similar .badge.source-learned"), null);
  } finally {
    ui.close();
  }
});

const approvedWith = (learning) => record({
  status: "approved",
  approval: { decision: "approved", by: "romanceresnak", at: NOW, comment: "",
              pull_request: { mode: "dry_run", title: "t", branch: "b", path: "p", content: "",
                              body: "", url: null, error: null },
              learning },
});

const OUTCOMES = [
  [{ status: "written", key: "learned/inc-1.md", ingestion: "started", ingestion_job_id: "JOB-7",
     error: null },
   /Written to learned\/inc-1\.md; indexing started \(job JOB-7\)\. It can be found as a similar incident once indexing finishes\./],
  [{ status: "written", key: "learned/inc-1.md", ingestion: "already_running",
     ingestion_job_id: null, error: null },
   /an indexing job was already running, so it is picked up by the next one/],
  [{ status: "written", key: "learned/inc-1.md", ingestion: "failed", ingestion_job_id: null,
     error: "AccessDeniedException" },
   /Written to learned\/inc-1\.md; indexing failed \(AccessDeniedException\)\./],
  [{ status: "error", key: null, ingestion: null, ingestion_job_id: null, error: "AccessDenied" },
   /Not learned: AccessDenied/],
  [{ status: "disabled", key: null, ingestion: null, ingestion_job_id: null, error: null },
   /Not learned: learning is not configured\./],
];

test("an approved incident shows what happened to its postmortem", async () => {
  for (const [learning, expected] of OUTCOMES) {
    const ui = await open(approvedWith(learning));
    try {
      const rows = [...detailOf(ui).querySelectorAll("dl.kv")]
        .flatMap((dl) => [...dl.querySelectorAll("dt")])
        .filter((dt) => dt.textContent === "Memory");
      assert.equal(rows.length, 1, JSON.stringify(learning));
      assert.match(rows[0].nextElementSibling.textContent, expected);
      assertNoLeaks(ui);
    } finally {
      ui.close();
    }
  }
});

test("a learning error is styled as an error and the others are not", async () => {
  const bad = await open(approvedWith(OUTCOMES[3][0]));
  const good = await open(approvedWith(OUTCOMES[0][0]));
  try {
    assert.ok(bad.document.querySelector("#detail dd .error"));
    assert.equal(good.document.querySelector("#detail dd .error"), null);
  } finally {
    bad.close();
    good.close();
  }
});

test("no Memory line for a rejection or an approval that has no learning outcome", async () => {
  const rejected = record({ status: "rejected", approval: {
    decision: "rejected", by: "r", at: NOW, comment: "no", pull_request: null, learning: null } });
  const none = approvedWith(null);
  for (const rec of [rejected, none]) {
    const ui = await open(rec);
    try {
      const labels = [...detailOf(ui).querySelectorAll("dt")].map((dt) => dt.textContent);
      assert.ok(!labels.includes("Memory"), labels.join(","));
      assertNoLeaks(ui);
    } finally {
      ui.close();
    }
  }
});

test("learning text from the server is shown as text, never as HTML", async () => {
  const ui = await open(approvedWith({
    status: "written", key: "learned/<img src=x onerror=alert(1)>.md", ingestion: "failed",
    ingestion_job_id: null, error: "<script>alert(2)</script>" }));
  try {
    assert.ok(detailOf(ui).textContent.includes("learned/<img src=x onerror=alert(1)>.md"));
    assert.equal(detailOf(ui).querySelector("img[src='x'], script"), null);
  } finally {
    ui.close();
  }
});
