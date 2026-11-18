// DOM tests for the public replay (replay REQ-RM, REQ-TL, REQ-DD, REQ-TS-01).
//
// The page under test is the one tools/replay/build.py assembles from the real index.html,
// app.js and the saved incident records, run in jsdom. Only fetch is faked, and it serves the
// built files and logs every call, so "the replay asks for nothing but files under data/" is
// something the tests check, not something they assume.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const SITE = mkdtempSync(join(tmpdir(), "replay-site-"));
execFileSync(process.env.PYTHON ?? "python3",
             [join(ROOT, "tools", "replay", "build.py"), "--out", SITE]);

const HTML = readFileSync(join(SITE, "index.html"), "utf8");
const SCRIPTS = [...HTML.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);
const PAGE = "https://romanceresnak.github.io/kiroween-incident-crypt/";

const FILES = {
  second: "netpol-second-run-real.json", first: "netpol-first-run-real.json",
  badimage: "badimage-real.json", pvc: "pvc-real.json", grafana: "grafana-oom-real.json",
};
const rec = (key) => JSON.parse(readFileSync(
  join(ROOT, "docs", "demo-results", FILES[key]), "utf8"));
const REC = Object.fromEntries(Object.keys(FILES).map((key) => [key, rec(key)]));
const ID = Object.fromEntries(Object.entries(REC).map(([key, r]) => [key, r.incident.incident_id]));
const ORDER = ["second", "first", "badimage", "pvc", "grafana"];

const STATUS_FOR = { detected: "accepted", classified: "processing", investigating: "processing",
                     awaiting_approval: "awaiting approval", approved: "approved" };
const SPECIALIST = { cpu_memory: "cpu-memory", network: "network",
                     deploy_rollout: "deploy-rollout", storage: "storage" };

const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check, what, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (check()) return;
    await tick();
  }
  assert.fail(`timed out waiting for ${what}`);
}

// Opens the built page. `serve` may answer a call itself: {status, body}.
async function open({ config = { autoplay: false }, serve, waitForDetail = true } = {}) {
  const calls = [];
  const stages = [];
  const dom = new JSDOM(HTML, { url: PAGE, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = async (url, init = {}) => {
    const call = { url: String(url), method: init.method ?? "GET", body: init.body ?? null };
    calls.push(call);
    const custom = serve && serve(call);
    if (custom) {
      return { ok: custom.status < 400, status: custom.status, json: async () => custom.body };
    }
    try {
      const body = JSON.parse(readFileSync(join(SITE, call.url), "utf8"));
      return { ok: true, status: 200, json: async () => body };
    } catch {
      return { ok: false, status: 404, json: async () => ({}) };
    }
  };
  window.KIROWEEN_REPLAY_CONFIG = config;
  for (const src of SCRIPTS) window.eval(readFileSync(join(SITE, src), "utf8"));
  const replay = window.KIROWEEN_REPLAY;
  const draw = replay.onChange;
  replay.onChange = () => { stages.push(replay.stage()); draw(); };
  await replay.ready;
  if (waitForDetail) {
    await until(() => !window.document.getElementById("detail").hidden, "the detail to render");
  }
  return { window, document: window.document, calls, stages, replay,
           close: () => window.close() };
}

const detail = (ui) => ui.document.getElementById("detail");
const detailText = (ui) => detail(ui).textContent;
const badgeText = (ui) => detail(ui).querySelector(".detail-head .badge")?.textContent;
const busy = (ui) => new Set([...ui.document.querySelectorAll(".crew figure.busy")]
  .map((f) => f.dataset.agent));
const heading = (ui, title) => [...detail(ui).querySelectorAll("h3")]
  .find((h) => h.textContent === title);

async function showStage(ui, index, stage) {
  ui.replay.goto(index);
  await until(() => badgeText(ui) === STATUS_FOR[stage], `${stage} to render`);
  await until(() => ui.replay.stage() === stage, `${stage} stage`);
  await tick(20);
}

async function choose(ui, key) {
  const items = [...ui.document.querySelectorAll(".feed-item")];
  items[ORDER.indexOf(key)].click();
  await until(() => detailText(ui).includes(ID[key]), `${key} to be shown`);
  await tick(20);
}

// Objects built inside the jsdom window have that window's Array and Object, which strict
// deepEqual refuses to match; compare their JSON instead.
const same = (actual, expected, message) =>
  assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, message);

function assertNoLeaks(ui) {
  const text = ui.document.body.textContent;
  assert.ok(!text.includes("[object "), `found "[object " in: ${text.slice(0, 200)}`);
  assert.doesNotMatch(text, /\b(null|undefined|NaN)\b/);
}

// ---------------------------------------------------------------------------- the page

test("the banner is exact, first on the page, and cannot be dismissed", async () => {
  const ui = await open();
  try {
    const banner = ui.document.querySelector(".replay-banner");
    assert.equal(banner.textContent,
      "Replay of real runs on Amazon EKS -- the live system has no public endpoint by design");
    assert.equal(ui.document.body.firstElementChild, banner);
    assert.equal(banner.querySelectorAll("button, a, input").length, 0);
    assert.equal(ui.document.title, "Kiroween Incident Crypt (replay)");
  } finally { ui.close(); }
});

test("the header says replay, never live", async () => {
  const ui = await open();
  try {
    await until(() => ui.document.getElementById("live").textContent.length > 0, "the indicator");
    const live = ui.document.getElementById("live");
    assert.equal(live.textContent, "replay of saved records");
    assert.equal(live.className, "live ok");
    assert.doesNotMatch(live.textContent, /\blive\b/i);
  } finally { ui.close(); }
});

test("the page asks for nothing but GET of files under data/, and never for the live API", async () => {
  const ui = await open({ config: { autoplay: true, stageMs: { detected: 10, classified: 10,
    investigating: 10, awaiting_approval: 10 } } });
  try {
    await until(() => ui.replay.stage() === "approved", "the replay to finish");
    // Try every control on the page, including the disabled decision.
    await choose(ui, "pvc");
    ui.replay.skip();
    await tick(30);
    for (const button of ui.document.querySelectorAll("button")) button.click();
    await tick(50);
    assert.ok(ui.calls.length >= 6, "the manifest and the five records");
    for (const call of ui.calls) {
      assert.equal(call.method, "GET", JSON.stringify(call));
      assert.equal(call.body, null);
      assert.match(call.url, /^data\/[A-Za-z0-9._-]+\.json$/, call.url);
    }
    assert.ok(!ui.calls.some((c) => c.url.includes("incidents")));
    assert.equal(ui.window.localStorage.length, 0);
    assert.equal(ui.document.getElementById("approver"), null);
  } finally { ui.close(); }
});

test("the feed lists the five real records in order, with their real timestamps", async () => {
  const ui = await open();
  try {
    await until(() => ui.document.querySelectorAll(".feed-item").length === 5, "the feed");
    const items = [...ui.document.querySelectorAll(".feed-item")];
    assert.equal(items.length, 5);
    items.forEach((item, index) => {
      const record = REC[ORDER[index]];
      const sub = item.querySelector(".sub").textContent;
      assert.ok(sub.startsWith(`${record.incident.namespace}/${record.incident.workload.name} · `),
                sub);
      assert.match(sub, /2026/);
      assert.ok(item.textContent.includes(record.incident.reason), item.textContent);
      assert.equal(item.querySelector("img").getAttribute("src").startsWith("art/"), true);
    });
    // Only the playing incident is at an early stage; the others show where they ended.
    const badges = items.map((i) => i.querySelector(".badge").textContent);
    assert.deepEqual(badges, ["accepted", "approved", "awaiting approval", "awaiting approval",
                              "awaiting approval"]);
    assertNoLeaks(ui);
  } finally { ui.close(); }
});

test("the first incident plays when the page opens, and a hash can pick another", async () => {
  const ui = await open();
  try {
    assert.equal(ui.stages[0], "detected");
    assert.ok(detailText(ui).includes(ID.second));
    assert.equal(ui.window.location.hash, "#" + ID.second);
  } finally { ui.close(); }
});

// -------------------------------------------------------------------------- the stages

test("each stage shows only what the record had by then", async () => {
  for (const key of ORDER) {
    const record = REC[key];
    const ui = await open();
    try {
      const view = (stage) => ui.replay.viewAt(record, stage);
      const stages = record.approval ? ui.replay.STAGES : ui.replay.STAGES.slice(0, 4);
      for (const stage of stages) {
        const v = view(stage);
        same(v.incident, record.incident, `${key}/${stage}`);
        assert.equal(v.status, stage === "approved" ? record.status : STATUS_FOR[stage]
          .replace("awaiting approval", "awaiting_approval"), `${key}/${stage}`);
      }
      assert.equal(view("detected").classification, null);
      assert.equal(view("detected").investigation, null);
      same(view("detected").similar_incidents, []);
      same(view("classified").classification, record.classification);
      assert.equal(view("classified").investigation, null);
      same(view("classified").similar_incidents, []);
      same(view("investigating").similar_incidents, record.similar_incidents);
      assert.equal(view("investigating").investigation, null);
      same(view("investigating").usage_by_agent, {});
      same(view("awaiting_approval").investigation, record.investigation);
      same(view("awaiting_approval").usage_by_agent, record.usage_by_agent);
      for (const stage of ["detected", "classified", "investigating", "awaiting_approval"]) {
        assert.equal(view(stage).approval, null, `${key}/${stage} must not show the approval`);
      }
      if (record.approval) same(view("approved"), record);
    } finally { ui.close(); }
  }
});

test("the page shows each stage in order, and the crew cards that did the work", async () => {
  const ui = await open();
  try {
    await showStage(ui, 0, "detected");
    assert.match(detailText(ui), /Not classified yet\./);
    assert.equal(heading(ui, "Evidence"), undefined);
    assert.deepEqual([...busy(ui)], ["orchestrator"]);

    await showStage(ui, 1, "classified");
    assert.match(detailText(ui), /network/);
    assert.match(detailText(ui), /No investigation yet\./);
    assert.deepEqual([...busy(ui)], ["orchestrator"]);

    await showStage(ui, 2, "investigating");
    assert.match(detailText(ui), /LRN-2e4fd60547e332f6/);
    assert.match(detailText(ui), /No investigation yet\./);
    assert.deepEqual([...busy(ui)].sort(), ["incident-memory", "network"]);

    await showStage(ui, 3, "awaiting_approval");
    const rows = heading(ui, "Evidence").nextElementSibling.querySelectorAll("tbody tr");
    assert.equal(rows.length, REC.second.investigation.diagnosis.evidence.length);
    assert.match(detailText(ui), /netpol-trusted/);
    assert.match(detailText(ui), /Cost per agent/);
    assert.deepEqual([...busy(ui)], []);

    await showStage(ui, 4, "approved");
    assert.deepEqual([...busy(ui)], []);
    assertNoLeaks(ui);
  } finally { ui.close(); }
});

test("each specialist is animated for its own incident while it investigates", async () => {
  const ui = await open();
  try {
    for (const key of ORDER) {
      await choose(ui, key);
      const specialist = SPECIALIST[REC[key].classification.category];
      await showStage(ui, 2, "investigating");
      assert.deepEqual([...busy(ui)].sort(), ["incident-memory", specialist].sort(), key);
    }
  } finally { ui.close(); }
});

test("the stage list marks where the replay is, and only approved records have a fifth stage", async () => {
  const ui = await open();
  try {
    const bar = () => ui.document.getElementById("replay-bar");
    assert.equal(bar().querySelectorAll(".replay-stages li").length, 5);   // second run: approved
    await showStage(ui, 2, "investigating");
    const now = bar().querySelectorAll('.replay-stages li[aria-current="step"]');
    assert.equal(now.length, 1);
    assert.equal(now[0].dataset.stage, "investigating");
    assert.equal(bar().querySelectorAll(".replay-stages li.done").length, 2);
    await choose(ui, "pvc");
    assert.equal(bar().querySelectorAll(".replay-stages li").length, 4);   // never approved
  } finally { ui.close(); }
});

test("the recorded duration and the illustrative-timing note are on the page", async () => {
  const ui = await open();
  try {
    for (const key of ORDER) {
      await choose(ui, key);
      const record = REC[key];
      const text = ui.document.getElementById("replay-bar").textContent;
      const from = Date.parse(record.created_at);
      const to = Date.parse(record.approval ? record.approval.at : record.updated_at);
      const seconds = Math.round((to - from) / 1000);
      const expected = record.approval
        ? `Recorded: ${seconds} s from submission to approval`
        : `Recorded: ${seconds} s from detection to awaiting approval.`;
      assert.ok(text.includes(expected), `${key}: ${text}`);
      assert.match(text, /Stage boundaries are illustrative/);
    }
  } finally { ui.close(); }
});

test("with timers the stages play in order and end on the last one", async () => {
  const ui = await open({ config: { autoplay: true, stageMs: { detected: 15, classified: 15,
    investigating: 15, awaiting_approval: 15 } } });
  try {
    await until(() => ui.replay.stage() === "approved", "the replay to reach the end");
    const order = ui.stages.filter((s, i) => s !== ui.stages[i - 1]);
    assert.deepEqual(order, ["detected", "classified", "investigating", "awaiting_approval",
                             "approved"]);
    await tick(60);
    assert.equal(ui.replay.stage(), "approved", "it stays on the last stage");
    assert.deepEqual([...busy(ui)], []);
  } finally { ui.close(); }
});

test("Restart replays from the start, Skip goes to the end, and another incident restarts", async () => {
  const ui = await open();
  try {
    ui.document.querySelector(".replay-skip").click();
    await until(() => ui.replay.stage() === "approved", "skip");
    ui.document.querySelector(".replay-restart").click();
    await until(() => ui.replay.stage() === "detected", "restart");
    await showStage(ui, 3, "awaiting_approval");
    await choose(ui, "badimage");
    assert.equal(ui.replay.stage(), "detected");
    assert.ok(detailText(ui).includes(ID.badimage));
    assert.equal(ui.window.location.hash, "#" + ID.badimage);
  } finally { ui.close(); }
});

// ------------------------------------------------------------------------ the decision

test("Approve and Reject are present, disabled, and send nothing", async () => {
  const ui = await open();
  try {
    await showStage(ui, 3, "awaiting_approval");
    const approve = detail(ui).querySelector("button.approve");
    const reject = detail(ui).querySelector("button.reject");
    assert.equal(approve.textContent, "Approve: open the PR");
    assert.equal(reject.textContent, "Reject");
    assert.equal(approve.disabled, true);
    assert.equal(reject.disabled, true);
    const before = ui.calls.length;
    approve.click(); reject.click();
    approve.dispatchEvent(new ui.window.MouseEvent("click", { bubbles: true }));
    await tick(50);
    assert.equal(ui.calls.length, before);
    assert.ok(!ui.calls.some((c) => c.method !== "GET"));
    assert.equal(ui.window.localStorage.length, 0);
    assert.equal(detail(ui).querySelectorAll("input, textarea").length, 0);
  } finally { ui.close(); }
});

test("the note links to the real pull request when there is one", async () => {
  const ui = await open();
  try {
    for (const [key, number] of [["second", 3], ["first", 2]]) {
      await choose(ui, key);
      await showStage(ui, 3, "awaiting_approval");
      const box = detail(ui).querySelector(".replay-decision");
      assert.match(box.textContent, /disabled in this replay/);
      assert.match(box.textContent, /Roman Ceresnak approved this incident/);
      const link = box.querySelector("a");
      assert.equal(link.getAttribute("href"),
        `https://github.com/romanceresnak/kiroween-incident-crypt/pull/${number}`);
      assert.equal(link.getAttribute("target"), "_blank");
      assert.equal(link.getAttribute("rel"), "noopener noreferrer");
    }
  } finally { ui.close(); }
});

test("the note says an incident was left unapproved when no pull request exists", async () => {
  const ui = await open();
  try {
    for (const key of ["badimage", "pvc", "grafana"]) {
      await choose(ui, key);
      await showStage(ui, 3, "awaiting_approval");
      const box = detail(ui).querySelector(".replay-decision");
      assert.match(box.textContent, /left unapproved, so no pull request exists/);
      assert.equal(box.querySelector("a"), null, key);
      assert.equal(box.querySelector("button.approve").disabled, true);
    }
  } finally { ui.close(); }
});

test("the approved stage shows the recorded decision, pull request and learned postmortem", async () => {
  const ui = await open();
  try {
    await showStage(ui, 4, "approved");
    const text = detailText(ui);
    const approval = REC.second.approval;
    assert.match(text, /Roman Ceresnak/);
    assert.match(text, new RegExp(approval.learning.key.replace(/[.]/g, "\\.")));
    assert.match(text, new RegExp(approval.learning.ingestion_job_id));
    assert.match(text, /GitHub pull request/);
    const link = detail(ui).querySelector('a[href$="/pull/3"]');
    assert.equal(link.getAttribute("href"), approval.pull_request.url);
    assert.ok(detail(ui).querySelector("pre").textContent.includes("op: replace"));
    assert.equal(detail(ui).querySelector("button.approve"), null);
    assertNoLeaks(ui);
  } finally { ui.close(); }
});

test("the learned postmortem of run 1 is the first similar incident of run 2", async () => {
  const ui = await open();
  try {
    await showStage(ui, 2, "investigating");
    const first = detail(ui).querySelector("ol.similar li");
    assert.match(first.textContent, /LRN-2e4fd60547e332f6/);
    assert.equal(first.querySelector(".badge.source-learned").textContent, "source: learned");
    assert.equal(first.querySelector(".score").textContent, "0.85");
    const others = [...detail(ui).querySelectorAll("ol.similar li")].slice(1);
    assert.deepEqual(others.map((li) => li.querySelector(".badge.source-seeded").textContent),
                     ["source: seeded", "source: seeded"]);
    await choose(ui, "first");
    await showStage(ui, 2, "investigating");
    assert.equal(detail(ui).querySelectorAll(".badge.source-learned").length, 0);
  } finally { ui.close(); }
});

// ------------------------------------------------------------------------------ safety

test("text from the records is never parsed as HTML, and only github.com is linked", async () => {
  const hostile = "<img src=x onerror=window.pwned=1><script>window.pwned=2</script>";
  const ui = await open({
    serve: (call) => {
      if (call.url === "data/manifest.json") {
        const manifest = JSON.parse(readFileSync(join(SITE, "data", "manifest.json"), "utf8"));
        manifest.incidents[0].story = hostile;
        return { status: 200, body: manifest };
      }
      if (call.url === "data/netpol-second-run-real.json") {
        const r = JSON.parse(JSON.stringify(REC.second));
        r.incident.reason = hostile;
        r.incident.message = hostile;
        r.classification.rationale = hostile;
        r.investigation.diagnosis.root_cause = hostile;
        r.approval.by = hostile;
        r.approval.pull_request.url = "javascript:window.pwned=3";
        return { status: 200, body: r };
      }
      return null;
    },
  });
  try {
    for (const [index, stage] of [[1, "classified"], [3, "awaiting_approval"], [4, "approved"]]) {
      await showStage(ui, index, stage);
      assert.equal(ui.document.querySelector("img[onerror]"), null);
      assert.equal(ui.document.querySelector("[onerror], [onclick]"), null,
                   "no handler attributes from data");
      assert.equal(ui.document.querySelector('a[href^="javascript"]'), null);
      assert.equal(ui.window.pwned, undefined);
      assert.ok(ui.document.body.textContent.includes(hostile));
      const bar = ui.document.getElementById("replay-bar");
      assert.ok(bar.textContent.includes(hostile), "the story is shown as text");
      assert.equal(bar.querySelector("img, script"), null);
    }
    assert.ok(![...ui.document.querySelectorAll("script")].some((s) => s.textContent.includes("pwned")));
  } finally { ui.close(); }
});

test("a failed load is said so and never falls back to the live API", async () => {
  const ui = await open({
    waitForDetail: false,
    serve: (call) => (call.url === "data/manifest.json" ? { status: 500, body: {} } : null),
  });
  try {
    await until(() => ui.document.getElementById("live").textContent
      .includes("replay data could not be loaded"), "the failure to be shown");
    assert.equal(ui.document.getElementById("live").className, "live down");
    assert.equal(ui.document.querySelectorAll(".feed-item").length, 0);
    assert.equal(detail(ui).hidden, true);
    await tick(100);
    assert.ok(ui.calls.every((c) => c.url === "data/manifest.json" && c.method === "GET"),
              JSON.stringify(ui.calls));
  } finally { ui.close(); }
});

test("a record that does not match the manifest is refused", async () => {
  const ui = await open({
    waitForDetail: false,
    serve: (call) => {
      if (call.url === "data/pvc-real.json") {
        const r = JSON.parse(JSON.stringify(REC.pvc));
        r.incident.incident_id = "someone-else";
        return { status: 200, body: r };
      }
      return null;
    },
  });
  try {
    await until(() => ui.document.getElementById("live").textContent
      .includes("replay data could not be loaded"), "the mismatch to be refused");
  } finally { ui.close(); }
});

test("a manifest cannot point outside data/", async () => {
  const ui = await open({
    waitForDetail: false,
    serve: (call) => (call.url === "data/manifest.json"
      ? { status: 200, body: { incidents: [{ id: "x", file: "../app.js", story: "s" }] } } : null),
  });
  try {
    await until(() => ui.document.getElementById("live").textContent
      .includes("replay data could not be loaded"), "the bad name to be refused");
    assert.ok(!ui.calls.some((c) => c.url.includes("..")));
  } finally { ui.close(); }
});

// ---------------------------------------------------------------------- the live UI

test("without the replay the real UI still asks the live API, exactly as before", async () => {
  const uiDir = new URL("../../agents/orchestrator/src/kiroween_orchestrator/ui/", import.meta.url);
  const dom = new JSDOM(readFileSync(new URL("index.html", uiDir), "utf8"),
    { url: "http://localhost:8080/ui/", runScripts: "outside-only", pretendToBeVisual: true });
  const calls = [];
  dom.window.fetch = async (url) => {
    calls.push(String(url));
    return { ok: true, status: 200, json: async () => [] };
  };
  dom.window.eval(readFileSync(new URL("app.js", uiDir), "utf8"));
  await until(() => calls.length > 0, "the first poll");
  assert.equal(calls[0], "/incidents?limit=50");
  await until(() => dom.window.document.getElementById("live").textContent.startsWith("live ·"),
              "the live indicator");
  assert.equal(dom.window.document.querySelector(".replay-banner"), null);
  dom.window.close();
});
