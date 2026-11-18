// DOM tests for the Scenario Lab page (scenario-lab REQ-UI-01 to UI-06, REQ-AU-11).
//
// The real lab.html and lab.js run in jsdom; only fetch is faked, and every call is logged.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM, VirtualConsole } from "jsdom";

const UI = new URL("../../agents/orchestrator/src/kiroween_orchestrator/ui/", import.meta.url);
const HTML = readFileSync(new URL("lab.html", UI), "utf8");
const SCRIPT = readFileSync(new URL("lab.js", UI), "utf8");

const CARDS = [
  ["oom-killed", "lab-01", "A container eats all its memory", "cpu-memory", "Vampire", "cpu_memory", ["OOMKilled"]],
  ["netpol-blocks-client", "lab-04", "A firewall rule blocks the client", "network", "Spider", "network", ["Unhealthy"]],
  ["bad-image-tag", "lab-08", "A rollout with an image that does not exist", "deploy-rollout", "Zombie", "deploy_rollout", ["ErrImagePull", "ImagePullBackOff"]],
  ["pvc-missing-storageclass", "lab-12", "A disk request for a storage type that does not exist", "storage", "Ghost", "storage", ["ProvisioningFailed"]],
].map(([id, namespace, title, agent, crew, category, signals]) => ({
  id, namespace, title, agent, crew, category, signals, breaks: `Plain words about ${id}.`,
}));

const IDLE = { state: "idle", scenario: null, namespace: null, run: null, started_at: null,
               expires_at: null, phase: "", note: "", runs_used: 2, max_runs: 10,
               cleanup_seconds: 900, next_allowed_in_s: 0, incident: null };

const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check, what, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (check()) return;
    await tick();
  }
  assert.fail(`timed out waiting for ${what}`);
}

// `handle(method, path, body)` returns {status, body, headers} or undefined for the default.
async function open({ status = IDLE, scenarios = CARDS, handle } = {}) {
  const calls = [];
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => {
    if (/navigation/i.test(error.message)) navigations.push(error.message);
  });
  const state = { status };
  const dom = new JSDOM(HTML, { url: "https://crypt.example.cloudfront.net/ui/lab.html",
                                runScripts: "outside-only", pretendToBeVisual: true, virtualConsole });
  const { window } = dom;
  window.fetch = async (url, init = {}) => {
    const call = { url: String(url), method: init.method ?? "GET",
                   body: init.body ? JSON.parse(init.body) : null, headers: init.headers ?? {} };
    calls.push(call);
    let answer = handle && handle(call, state);
    if (!answer) {
      if (call.url === "/lab/scenarios") answer = { status: 200, body: scenarios };
      else if (call.url === "/lab/status") answer = { status: 200, body: state.status };
      else if (call.url === "/lab/run") answer = { status: 202, body: { run: "abc123" } };
      else if (call.url === "/lab/reset") answer = { status: 200, body: IDLE };
      else if (call.url === "/auth/logout") answer = { status: 200, body: { ok: true } };
      else answer = { status: 404, body: {} };
    }
    const headers = new Map(Object.entries(answer.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
    return { ok: answer.status < 400, status: answer.status, json: async () => answer.body,
             headers: { get: (name) => headers.get(name.toLowerCase()) ?? null } };
  };
  window.eval(SCRIPT);
  await until(() => window.document.querySelectorAll(".lab-card").length === scenarios.length
    && window.document.getElementById("status-body").textContent !== "Loading…", "the page");
  const document = window.document;
  return {
    window, document, calls, navigations, state, close: () => window.close(),
    runButton: (id) => document.querySelector(`.lab-run[data-scenario="${id}"]`),
    text: () => document.getElementById("status-body").textContent,
    message: () => document.getElementById("lab-message").textContent,
    reset: () => document.getElementById("reset"),
  };
}

const future = (ms) => new Date(Date.now() + ms).toISOString();
const running = (extra = {}) => ({
  ...IDLE, state: "running", scenario: "netpol-blocks-client", namespace: "lab-04", run: "abc123",
  started_at: new Date(Date.now() - 20000).toISOString(), expires_at: future(14 * 60000 + 30000),
  note: "applied; the crew is watching", ...extra });

// ---------------------------------------------------------------------------- the cards


test("one card per scenario shows the title, the crew member, what breaks and the signals", async () => {
  const ui = await open();
  try {
    const cards = [...ui.document.querySelectorAll(".lab-card")];
    assert.equal(cards.length, 4);
    CARDS.forEach((scenario, index) => {
      const card = cards[index];
      assert.equal(card.querySelector("h3").textContent, scenario.title);
      assert.equal(card.querySelector(".lab-crew").textContent,
                   `${scenario.crew} (${scenario.agent}) should answer`);
      assert.ok(card.textContent.includes(scenario.breaks));
      assert.ok(card.querySelector(".lab-signals").textContent.includes(scenario.signals.join(", ")));
      assert.match(card.querySelector("img").getAttribute("src"),
                   /^art\/(vampire|spider|zombie|ghost)\.svg$/);
      const button = card.querySelector("button.lab-run");
      assert.equal(button.textContent, "Run");
      assert.equal(button.dataset.scenario, scenario.id);
    });
    assert.equal(cards[0].querySelector("img").getAttribute("src"), "art/vampire.svg");
    assert.equal(cards[1].querySelector("img").getAttribute("src"), "art/spider.svg");
    assert.equal(cards[2].querySelector("img").getAttribute("src"), "art/zombie.svg");
    assert.equal(cards[3].querySelector("img").getAttribute("src"), "art/ghost.svg");
    assert.equal(ui.document.getElementById("scenarios-empty").hidden, true);
  } finally { ui.close(); }
});

test("the page says what the lab does and does not do", async () => {
  const ui = await open();
  try {
    const text = ui.document.querySelector(".lab-intro").textContent.replace(/\s+/g, " ");
    assert.match(text, /sandbox branch/);
    assert.match(text, /Nothing is applied to the cluster, and nothing is saved as memory/);
    assert.match(text, /If the crew proposes a fix/);
    assert.match(text, /those end as a diagnosis, with nothing to approve/);
    assert.match(text, /names the crew member who should answer/);
    assert.match(text, /the incident shows who actually did/);
    assert.match(text, /One scenario at a time/);
    assert.match(text, /15 minutes/);
  } finally { ui.close(); }
});

test("an idle lab shows Ready, the runs used, and enabled Run buttons", async () => {
  const ui = await open();
  try {
    assert.match(ui.text(), /Ready/);
    assert.match(ui.text(), /Runs this hour: 2 of 10/);
    for (const button of ui.document.querySelectorAll(".lab-run")) assert.equal(button.disabled, false);
    assert.equal(ui.reset().disabled, true, "nothing to reset");
    assert.equal(ui.document.getElementById("live").className, "live ok");
  } finally { ui.close(); }
});

// -------------------------------------------------------------------------------- Run


test("Run sends only the scenario id, as JSON, and then shows the new status", async () => {
  const ui = await open({
    handle: (call, state) => {
      if (call.url === "/lab/run") {
        state.status = running();
        return { status: 202, body: { run: "abc123" } };
      }
    },
  });
  try {
    ui.runButton("netpol-blocks-client").click();
    await until(() => /Running/.test(ui.text()), "the running state");
    const run = ui.calls.find((c) => c.url === "/lab/run");
    assert.deepEqual(run.body, { scenario: "netpol-blocks-client" });
    assert.equal(run.method, "POST");
    assert.equal(run.headers["Content-Type"], "application/json");
    assert.equal(ui.calls.filter((c) => c.url === "/lab/run").length, 1);
    assert.match(ui.text(), /A firewall rule blocks the client/);
    assert.match(ui.text(), /in lab-04/);
    for (const button of ui.document.querySelectorAll(".lab-run")) assert.equal(button.disabled, true);
    assert.equal(ui.reset().disabled, false);
    assert.equal(ui.message(), "");
  } finally { ui.close(); }
});

test("a click on a disabled Run button sends nothing", async () => {
  const ui = await open({ status: running() });
  try {
    await until(() => /Running/.test(ui.text()), "the running state");
    const before = ui.calls.length;
    ui.runButton("oom-killed").click();
    ui.runButton("oom-killed").dispatchEvent(new ui.window.MouseEvent("click", { bubbles: true }));
    await tick(50);
    assert.equal(ui.calls.filter((c) => c.url === "/lab/run").length, 0);
    assert.ok(ui.calls.length >= before);
  } finally { ui.close(); }
});

test("the buttons say why they are off", async () => {
  const cases = [
    [running(), /A scenario is running/],
    [{ ...IDLE, state: "cleaning", scenario: "oom-killed" }, /being cleaned up/],
    [{ ...IDLE, runs_used: 10, next_allowed_in_s: 840 }, /All 10 runs for this hour are used.*14 minute/],
  ];
  for (const [status, pattern] of cases) {
    const ui = await open({ status });
    try {
      for (const button of ui.document.querySelectorAll(".lab-run")) {
        assert.equal(button.disabled, true);
        assert.match(button.getAttribute("title"), pattern);
      }
    } finally { ui.close(); }
  }
});

test("a refusal is shown as a plain sentence and never as a bare code", async () => {
  const cases = [
    [{ status: 409, body: { detail: "a scenario is already running: oom-killed" } },
     /Another scenario is already running\. Wait for it to end, or press Reset\./],
    [{ status: 429, body: { detail: "hourly" }, headers: { "Retry-After": "840" } },
     /All the runs for this hour are used\. The next one opens in about 14 minute\(s\)\./],
    [{ status: 503, body: { detail: "x" } }, /The Lab is not available right now/],
    [{ status: 404, body: { detail: "x" } }, /That scenario does not exist/],
    [{ status: 403, body: { detail: "x" } }, /The request was refused\. Reload the page/],
    [{ status: 500, body: {} }, /Something went wrong \(code 500\)/],
  ];
  for (const [answer, pattern] of cases) {
    const ui = await open({ handle: (call) => (call.url === "/lab/run" ? answer : undefined) });
    try {
      ui.runButton("oom-killed").click();
      await until(() => ui.message() !== "", "the message");
      assert.match(ui.message(), pattern);
      if (answer.status !== 500) assert.doesNotMatch(ui.message(), /\b(409|429|503|404|403)\b/);
      assert.equal(ui.document.getElementById("lab-message").getAttribute("role"), "alert");
    } finally { ui.close(); }
  }
});

test("when the orchestrator cannot be reached the page says so", async () => {
  const ui = await open({
    handle: (call) => {
      if (call.url === "/lab/run") throw new Error("network down");
    },
  });
  try {
    ui.runButton("oom-killed").click();
    await until(() => ui.message() !== "", "the message");
    assert.match(ui.message(), /Could not reach the Lab/);
    assert.equal(ui.runButton("oom-killed").disabled, false, "the visitor can try again");
  } finally { ui.close(); }
});

// ------------------------------------------------------------------- status and incident


test("a running scenario shows a countdown to the automatic cleanup", async () => {
  const ui = await open({ status: running() });
  try {
    await until(() => /Cleans up by itself in 14:\d\d/.test(ui.text()), "the countdown");
    assert.match(ui.document.getElementById("countdown").textContent, /^Cleans up by itself in 14:[0-5]\d$/);
  } finally { ui.close(); }
});

test("until an incident exists the page says the watcher is still looking", async () => {
  const ui = await open({ status: running() });
  try {
    assert.match(ui.text(), /Waiting for the watcher to notice/);
    assert.equal(ui.document.querySelector(".lab-incident-link"), null);
  } finally { ui.close(); }
});

test("the incident the run produced is linked, with its status and the crew member", async () => {
  const incident = { incident_id: "9a8b7c6d5e4f3a2b", status: "awaiting_approval",
                     category: "network", reason: "Unhealthy", workload: "Deployment/client-abc123",
                     correlated: 2 };
  const ui = await open({ status: running({ incident }) });
  try {
    const link = ui.document.querySelector(".lab-incident-link");
    assert.equal(link.getAttribute("href"), "index.html#9a8b7c6d5e4f3a2b");
    assert.equal(link.textContent, "Open the incident");
    assert.match(ui.text(), /Waiting for you · Spider · Unhealthy · 2 related signal\(s\)/);
    assert.doesNotMatch(ui.text(), /Waiting for the watcher/);
  } finally { ui.close(); }
});

test("an incident id is encoded into the link and text from the API is never parsed as HTML", async () => {
  const hostile = "<img src=x onerror=window.pwned=1>";
  const incident = { incident_id: 'x"><script>window.pwned=2</script>', status: "processing",
                     category: "network", reason: hostile, workload: "Deployment/x", correlated: 0 };
  const cards = [{ ...CARDS[0], title: hostile, breaks: hostile, crew: hostile }];
  const ui = await open({ status: running({ incident, note: hostile }), scenarios: cards });
  try {
    assert.equal(ui.window.pwned, undefined);
    assert.equal(ui.document.querySelector("img[onerror]"), null);
    assert.equal(ui.document.querySelector("script[src]") ? 1 : 0, 1, "only lab.js");
    assert.equal(ui.document.querySelectorAll("script").length, 1);
    const href = ui.document.querySelector(".lab-incident-link").getAttribute("href");
    assert.ok(href.startsWith("index.html#") && !href.includes("<") && !href.includes('"'), href);
    assert.ok(ui.document.querySelector(".lab-card h3").textContent === hostile);
    assert.ok(ui.text().includes(hostile));
  } finally { ui.close(); }
});

test("an unknown status or state is shown as it is, not as undefined", async () => {
  const incident = { incident_id: "i1", status: "something_new", category: null, reason: "Unhealthy",
                     workload: "Deployment/x", correlated: 0 };
  const ui = await open({ status: running({ incident, state: "running" }) });
  try {
    assert.match(ui.text(), /something_new/);
    assert.doesNotMatch(ui.text(), /\b(undefined|null|NaN)\b/);
  } finally { ui.close(); }
});

// -------------------------------------------------------------------------------- Reset


test("Reset asks the runner to clean up now and shows the idle lab again", async () => {
  const ui = await open({
    status: running(),
    handle: (call, state) => {
      if (call.url === "/lab/reset") { state.status = IDLE; return { status: 200, body: IDLE }; }
    },
  });
  try {
    await until(() => !ui.reset().disabled, "reset to be enabled");
    ui.reset().click();
    await until(() => /Ready/.test(ui.text()), "the idle state");
    const reset = ui.calls.find((c) => c.url === "/lab/reset");
    assert.equal(reset.method, "POST");
    assert.equal(reset.headers["Content-Type"], "application/json");
    assert.equal(ui.runButton("oom-killed").disabled, false);
  } finally { ui.close(); }
});

test("a failed Reset is explained", async () => {
  const ui = await open({
    status: running(),
    handle: (call) => (call.url === "/lab/reset" ? { status: 503, body: {} } : undefined),
  });
  try {
    ui.reset().click();
    await until(() => ui.message() !== "", "the message");
    assert.match(ui.message(), /The Lab is not available right now/);
  } finally { ui.close(); }
});

// ------------------------------------------------------------ sign-out and failures


test("a lost session sends the visitor to sign in instead of showing an error", async () => {
  const ui = await open({
    handle: (call, state) => (call.url === "/lab/status" && state.expired
      ? { status: 401, body: { detail: "login required" } } : undefined),
  });
  try {
    ui.state.expired = true;
    await until(() => ui.navigations.length > 0, "a navigation", 5000);
    assert.match(ui.navigations[0], /navigation/i);
  } finally { ui.close(); }
});

test("Log out ends the session and leaves the page", async () => {
  const ui = await open();
  try {
    ui.document.getElementById("logout").click();
    await until(() => ui.calls.some((c) => c.url === "/auth/logout"), "the logout request");
    const logout = ui.calls.find((c) => c.url === "/auth/logout");
    assert.equal(logout.method, "POST");
    assert.equal(logout.headers["Content-Type"], "application/json");
    await until(() => ui.navigations.length > 0, "the redirect");
  } finally { ui.close(); }
});

test("the page asks only the orchestrator's lab and auth routes, and never the runner", async () => {
  const ui = await open({
    handle: (call, state) => {
      if (call.url === "/lab/run") { state.status = running(); return { status: 202, body: {} }; }
    },
  });
  try {
    ui.runButton("oom-killed").click();
    await until(() => /Running/.test(ui.text()), "running");
    ui.reset().click();
    await tick(60);
    for (const call of ui.calls) {
      assert.match(call.url, /^\/(lab\/(scenarios|status|run|reset)|auth\/logout)$/, call.url);
      assert.doesNotMatch(call.url, /lab-runner|:8080|http/);
    }
  } finally { ui.close(); }
});

test("a lab with no scenarios says so", async () => {
  const ui = await open({ scenarios: [], handle: (call) => (call.url === "/lab/scenarios"
    ? { status: 503, body: {} } : undefined) }).catch((error) => error);
  // With no cards the open() helper waits for a card count of zero, which is immediate.
  assert.ok(ui.document, String(ui));
  try {
    assert.match(ui.document.getElementById("scenarios-empty").textContent,
                 /could not be loaded/);
    assert.equal(ui.document.querySelectorAll(".lab-card").length, 0);
  } finally { ui.close(); }
});

test("the lab page and script contain no inline script, handler or HTML injection", () => {
  assert.doesNotMatch(HTML, /<script(?![^>]*\bsrc=)[^>]*>/);
  assert.doesNotMatch(HTML, /\son\w+\s*=/);
  assert.doesNotMatch(HTML, /style=/);
  assert.doesNotMatch(SCRIPT, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/);
  assert.match(SCRIPT, /textContent/);
  for (const src of HTML.matchAll(/(?:src|href)="([^"]+)"/g)) {
    assert.doesNotMatch(src[1], /^(https?:|\/\/)/, src[1]);
  }
});
