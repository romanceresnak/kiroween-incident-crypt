// The Scenario Lab page (scenario-lab REQ-UI-01 to UI-04, REQ-AU-11).
// Everything from the API is data: it is only ever set with textContent or as an attribute,
// never parsed as HTML. The page asks only the orchestrator, never the runner.
"use strict";

const POLL_MS = 3000;
const ART = {
  "cpu-memory": "art/vampire.svg", "network": "art/spider.svg",
  "deploy-rollout": "art/zombie.svg", "storage": "art/ghost.svg",
};
const CREW_BY_CATEGORY = {
  cpu_memory: "Vampire", network: "Spider", deploy_rollout: "Zombie", storage: "Ghost",
};
const STATE_WORDS = { idle: "Ready", running: "Running", cleaning: "Cleaning up" };
// Shown on a card the catalog marks experimental. The scenario still runs.
const EXPERIMENTAL_NOTE = "The crew does not yet answer this one reliably.";
const STATUS_WORDS = {
  accepted: "Detected", processing: "Being investigated", awaiting_approval: "Waiting for you",
  completed: "Finished", approved: "Approved", rejected: "Rejected", failed: "Failed",
  no_specialist: "No specialist", correlated: "Part of another incident",
};

let scenarios = [];
let status = null;
let sending = false;

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

function say(text) {
  document.getElementById("lab-message").textContent = text || "";
}

function setLive(ok, text) {
  const live = document.getElementById("live");
  live.className = "live " + (ok ? "ok" : "down");
  live.textContent = text;
}

// A request to the orchestrator. A lost session sends the visitor to sign in again.
async function api(method, path, body) {
  // Every POST is JSON, even an empty one: the server refuses anything else (same-origin check).
  const post = method === "POST";
  const response = await fetch(path, {
    method,
    headers: { "Accept": "application/json", ...(post ? { "Content-Type": "application/json" } : {}) },
    body: post ? JSON.stringify(body || {}) : undefined,
    credentials: "same-origin",
  });
  if (response.status === 401) {
    window.location.assign("/login?next=" + encodeURIComponent("/ui/lab.html"));
    throw new Error("signed out");
  }
  let data = {};
  try { data = await response.json(); } catch (err) { /* an empty or odd body */ }
  return { ok: response.ok, status: response.status, data, retryAfter: response.headers.get("Retry-After") };
}

// Plain sentences, never a status code on its own (REQ-UI-04).
function explain(result) {
  const { status: code, data, retryAfter } = result;
  if (code === 409) {
    return "Another scenario is already running. Wait for it to end, or press Reset.";
  }
  if (code === 429) {
    const seconds = Number(retryAfter || data.retry_after);
    const minutes = Number.isFinite(seconds) && seconds > 0 ? Math.max(1, Math.ceil(seconds / 60)) : null;
    return "All the runs for this hour are used."
      + (minutes ? " The next one opens in about " + minutes + " minute(s)." : " Try again later.");
  }
  if (code === 503) return "The Lab is not available right now. Try again in a minute.";
  if (code === 404) return "That scenario does not exist.";
  if (code === 403 || code === 415) return "The request was refused. Reload the page and try again.";
  return "Something went wrong (code " + code + "). Try again.";
}

function minutesAndSeconds(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}

// Why the Run buttons are off, or null when they are on.
function why() {
  if (!status) return "Waiting for the Lab to answer.";
  if (status.state === "running") return "A scenario is running. Run opens again when it ends or after Reset.";
  if (status.state === "cleaning") return "The last scenario is being cleaned up.";
  if (status.next_allowed_in_s > 0) {
    return "All " + status.max_runs + " runs for this hour are used. The next opens in about "
      + Math.max(1, Math.ceil(status.next_allowed_in_s / 60)) + " minute(s).";
  }
  if (sending) return "Starting.";
  return null;
}

function renderScenarios() {
  const list = document.getElementById("scenarios");
  document.getElementById("scenarios-empty").hidden = scenarios.length > 0;
  list.replaceChildren(...scenarios.map((s) => el("li", {
    class: s.experimental === true ? "lab-card lab-card-experimental" : "lab-card",
    "data-scenario": s.id },
    el("div", { class: "lab-card-head" },
      el("img", { src: ART[s.agent] || ART.network, alt: "", width: 48, height: 48 }),
      el("div", {},
        el("h3", { text: s.title }),
        el("div", { class: "muted lab-crew", text: s.crew + " (" + s.agent + ") should answer" }),
        s.experimental === true ? el("span", { class: "badge experimental", text: "Experimental" }) : null)),
    el("p", { text: s.breaks }),
    s.experimental === true ? el("p", { class: "lab-experimental", text: EXPERIMENTAL_NOTE }) : null,
    el("p", { class: "muted lab-signals", text: "The watcher should report: " + s.signals.join(", ") }),
    el("button", { type: "button", class: "approve lab-run", "data-scenario": s.id,
                   onclick: () => run(s) }, "Run"))));
  updateButtons();
}

function updateButtons() {
  const reason = why();
  for (const button of document.querySelectorAll(".lab-run")) {
    button.disabled = reason !== null;
    if (reason) button.setAttribute("title", reason); else button.removeAttribute("title");
  }
  document.getElementById("reset").disabled = !status || status.state === "idle" || sending;
}

function renderStatus() {
  const body = document.getElementById("status-body");
  if (!status) { body.textContent = "Loading…"; return; }
  const title = (scenarios.find((s) => s.id === status.scenario) || {}).title || status.scenario;
  const rows = [
    el("p", {}, el("span", { class: "badge " + (status.state === "idle" ? "completed" : "processing"),
                             text: STATE_WORDS[status.state] || status.state }),
      title ? el("strong", { text: " " + title }) : null,
      status.namespace ? el("span", { class: "muted", text: " in " + status.namespace }) : null),
  ];
  if (status.state === "running" && status.expires_at) {
    rows.push(el("p", { class: "muted", id: "countdown",
                        text: "Cleans up by itself in " + minutesAndSeconds(Date.parse(status.expires_at) - Date.now()) }));
  }
  if (status.note) rows.push(el("p", { class: "muted", text: status.note }));
  const incident = status.incident;
  if (incident) {
    const crew = CREW_BY_CATEGORY[incident.category];
    rows.push(el("p", {},
      el("a", { href: "index.html#" + encodeURIComponent(incident.incident_id), class: "lab-incident-link",
                text: "Open the incident" }),
      el("span", { class: "muted", text: " · " + (STATUS_WORDS[incident.status] || incident.status)
        + (crew ? " · " + crew : "") + " · " + incident.reason
        + (incident.correlated ? " · " + incident.correlated + " related signal(s)" : "") })));
  } else if (status.state === "running") {
    rows.push(el("p", { class: "muted", text: "Waiting for the watcher to notice. This takes up to a minute or two." }));
  }
  rows.push(el("p", { class: "muted", text: "Runs this hour: " + status.runs_used + " of " + status.max_runs }));
  const reason = why();
  if (reason && status.state === "idle") rows.push(el("p", { class: "muted", text: reason }));
  body.replaceChildren(...rows);
}

async function refresh() {
  try {
    const result = await api("GET", "/lab/status");
    if (!result.ok) { setLive(false, "lab unavailable"); say(explain(result)); return; }
    status = result.data;
    setLive(true, "lab · " + new Date().toLocaleTimeString());
  } catch (err) {
    if (err.message !== "signed out") setLive(false, "orchestrator unreachable");
    return;
  }
  renderStatus();
  updateButtons();
}

async function run(scenario) {
  if (why() !== null) return;
  sending = true; say(""); updateButtons();
  try {
    const result = await api("POST", "/lab/run", { scenario: scenario.id });
    if (!result.ok) say(explain(result));
  } catch (err) {
    if (err.message !== "signed out") say("Could not reach the Lab. Check your connection.");
  } finally {
    sending = false;
  }
  await refresh();
}

async function reset() {
  sending = true; say(""); updateButtons();
  try {
    const result = await api("POST", "/lab/reset");
    if (!result.ok) say(explain(result));
  } catch (err) {
    if (err.message !== "signed out") say("Could not reach the Lab. Check your connection.");
  } finally {
    sending = false;
  }
  await refresh();
}

async function logout() {
  try { await api("POST", "/auth/logout"); } catch (err) { /* leave anyway */ }
  window.location.assign("/login");
}

async function start() {
  document.getElementById("reset").addEventListener("click", reset);
  document.getElementById("logout").addEventListener("click", logout);
  try {
    const result = await api("GET", "/lab/scenarios");
    scenarios = result.ok && Array.isArray(result.data) ? result.data : [];
  } catch (err) { scenarios = []; }
  if (!scenarios.length) document.getElementById("scenarios-empty").textContent = "The scenarios could not be loaded.";
  renderScenarios();
  await refresh();
  setInterval(refresh, POLL_MS);
  setInterval(() => {
    const counter = document.getElementById("countdown");
    if (counter && status && status.expires_at) {
      counter.textContent = "Cleans up by itself in " + minutesAndSeconds(Date.parse(status.expires_at) - Date.now());
    }
  }, 1000);
}

start();
