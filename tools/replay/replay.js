// Replay engine for the Kiroween Incident Crypt UI (replay REQ-RM, REQ-TL, REQ-DD).
//
// Loaded before app.js. It defines window.KIROWEEN_REPLAY, which makes app.js read saved
// incident records instead of the live API. It makes no request except GET of files under
// data/, and it only ever sets text with textContent, never as HTML.
"use strict";

(function () {
  const config = Object.assign({ autoplay: true }, window.KIROWEEN_REPLAY_CONFIG || {});
  // How long each stage plays. The last stage has no timer.
  const stageMs = Object.assign(
    { detected: 1500, classified: 2000, investigating: 5000, awaiting_approval: 6000 },
    config.stageMs || {});
  const MANIFEST = config.manifestUrl || "data/manifest.json";
  const FILE_NAME = /^[A-Za-z0-9._-]+\.json$/;

  const STAGES = ["detected", "classified", "investigating", "awaiting_approval", "approved"];
  const STAGE_LABEL = {
    detected: "Detected", classified: "Classified", investigating: "Investigating",
    awaiting_approval: "Awaiting approval", approved: "Approved (as recorded)",
  };
  const SPECIALIST_AGENT = {
    cpu_memory: "cpu-memory", network: "network", deploy_rollout: "deploy-rollout",
    storage: "storage",
  };
  const ILLUSTRATIVE = "Stage boundaries are illustrative: the saved records keep only the "
    + "creation and last-update times, not a timeline.";

  const records = new Map();
  const stories = new Map();
  let order = [];
  let loadError = null;
  let play = { id: null, index: 0, timer: null };

  // --- stages ----------------------------------------------------------------------------

  function stagesFor(record) {
    return record.approval ? STAGES : STAGES.slice(0, 4);
  }

  // What the incident looked like at a stage. A pure function of the saved record.
  function viewAt(record, stage) {
    const view = Object.assign({}, record);
    if (stage === "approved") return view;
    view.approval = null;
    view.error = null;
    if (stage === "awaiting_approval") {
      view.status = "awaiting_approval";
      if (record.approval) view.updated_at = record.created_at;
      return view;
    }
    view.updated_at = record.created_at;
    view.investigation = null;
    view.usage_by_agent = {};
    if (stage === "detected") {
      view.status = "accepted";
      view.classification = null;
      view.similar_incidents = [];
    } else if (stage === "classified") {
      view.status = "processing";
      view.similar_incidents = [];
    } else {
      view.status = "processing";
    }
    return view;
  }

  // The crew cards that did the work at a stage (REQ-TL-03).
  function busyFor(stage, record) {
    if (stage === "detected" || stage === "classified") return ["orchestrator"];
    if (stage === "investigating") {
      const agents = ["incident-memory"];
      const specialist = SPECIALIST_AGENT[record.classification && record.classification.category];
      if (specialist) agents.push(specialist);
      return agents;
    }
    return [];
  }

  function lastStage(record) {
    const stages = stagesFor(record);
    return stages[stages.length - 1];
  }

  function currentStage() {
    const record = records.get(play.id);
    return record ? stagesFor(record)[play.index] : null;
  }

  function viewOf(id) {
    const record = records.get(id);
    return viewAt(record, id === play.id ? currentStage() : lastStage(record));
  }

  // --- loading ---------------------------------------------------------------------------

  async function getFile(url) {
    const response = await window.fetch(url, { headers: { "Accept": "application/json" } });
    if (!response.ok) throw new Error(url + " returned " + response.status);
    return response.json();
  }

  async function load() {
    try {
      const manifest = await getFile(MANIFEST);
      if (!manifest || !Array.isArray(manifest.incidents) || !manifest.incidents.length) {
        throw new Error(MANIFEST + " lists no incidents");
      }
      for (const entry of manifest.incidents) {
        if (!FILE_NAME.test(String(entry.file))) throw new Error("bad file name in the manifest");
        const record = await getFile("data/" + entry.file);
        if (!record || !record.incident || record.incident.incident_id !== entry.id) {
          throw new Error(entry.file + " does not match the manifest");
        }
        records.set(entry.id, record);
        stories.set(entry.id, String(entry.story || ""));
        order.push(entry.id);
      }
    } catch (err) {
      loadError = "replay data could not be loaded: " + err.message;
      order = [];
    }
  }

  const ready = load();

  async function getJSON(path) {
    await ready;
    if (loadError) throw new Error(loadError);
    if (path.startsWith("/incidents?")) return order.map(viewOf);
    const match = /^\/incidents\/([^/?#]+)$/.exec(path);
    if (match) {
      const id = decodeURIComponent(match[1]);
      if (!records.has(id)) throw new Error(path + " returned 404");
      return viewOf(id);
    }
    throw new Error("the replay has no " + path);
  }

  // --- the playhead ----------------------------------------------------------------------

  function clearTimer() {
    if (play.timer !== null) clearTimeout(play.timer);
    play.timer = null;
  }

  function schedule() {
    const record = records.get(play.id);
    if (!record || !config.autoplay) return;
    const stages = stagesFor(record);
    if (play.index >= stages.length - 1) return;
    const ms = stageMs[stages[play.index]];
    if (typeof ms !== "number") return;
    play.timer = setTimeout(() => {
      play.timer = null;
      play.index += 1;
      changed();
      schedule();
    }, ms);
  }

  function start(id) {
    if (!records.has(id)) return;
    clearTimer();
    play = { id, index: 0, timer: null };
    changed();
    schedule();
  }

  function restart() {
    if (play.id) start(play.id);
  }

  function goto(index) {
    const record = records.get(play.id);
    if (!record) return;
    clearTimer();
    play.index = Math.max(0, Math.min(index, stagesFor(record).length - 1));
    changed();
    schedule();
  }

  function skip() {
    const record = records.get(play.id);
    if (record) goto(stagesFor(record).length - 1);
  }

  function changed() {
    renderBar();
    if (typeof api.onChange === "function") api.onChange();
  }

  // --- what the page says ----------------------------------------------------------------

  function seconds(from, to) {
    const value = (Date.parse(to) - Date.parse(from)) / 1000;
    return Number.isFinite(value) ? Math.round(value) : null;
  }

  function recordedText(record) {
    if (record.approval) {
      return "Recorded: " + seconds(record.created_at, record.approval.at)
        + " s from submission to approval, including the investigation and a person reading it.";
    }
    return "Recorded: " + seconds(record.created_at, record.updated_at)
      + " s from detection to awaiting approval.";
  }

  // The note under the disabled buttons (REQ-DD-02). It is about the real run, so it reads the
  // saved record: the view of an earlier stage hides the approval on purpose.
  function decisionNote(view) {
    const record = records.get(view.incident.incident_id) || view;
    const approval = record.approval;
    const pr = approval && approval.pull_request;
    const url = pr && typeof pr.url === "string" ? pr.url : null;
    if (url) {
      return {
        text: "Approve and Reject are disabled in this replay. On the real cluster "
          + (approval.by || "a person") + " approved this incident and the orchestrator opened "
          + "this pull request:",
        link: url,
      };
    }
    return {
      text: "Approve and Reject are disabled in this replay. On the real cluster this incident "
        + "was investigated and left unapproved, so no pull request exists.",
      link: null,
    };
  }

  function node(tag, props, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") element.textContent = String(value);
      else if (key === "class") element.className = value;
      else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
      else element.setAttribute(key, String(value));
    }
    for (const child of children) if (child) element.append(child);
    return element;
  }

  function renderBar() {
    const bar = document.getElementById("replay-bar");
    if (!bar) return;
    const record = records.get(play.id);
    if (!record) {
      bar.replaceChildren(node("p", { class: "muted", text: "Choose an incident from the feed." }));
      return;
    }
    const stages = stagesFor(record);
    const list = node("ol", { class: "replay-stages" }, ...stages.map((stage, index) => node("li", {
      class: index < play.index ? "done" : (index === play.index ? "now" : "todo"),
      "aria-current": index === play.index ? "step" : null,
      "data-stage": stage,
      text: STAGE_LABEL[stage],
    })));
    bar.replaceChildren(
      node("p", { class: "replay-story", text: stories.get(play.id) }),
      list,
      node("p", { class: "muted replay-recorded", text: recordedText(record) }),
      node("p", { class: "muted replay-note", text: ILLUSTRATIVE }),
      node("div", { class: "replay-controls" },
        node("button", { type: "button", class: "replay-restart", text: "Restart",
                         onclick: restart }),
        node("button", { type: "button", class: "replay-skip", text: "Skip to the end",
                         onclick: skip })));
  }

  const api = {
    enabled: true,
    ready,
    STAGES,
    onChange: null,
    getJSON,
    viewAt,
    start,
    restart,
    skip,
    goto,
    stage: currentStage,
    has: (id) => records.has(id),
    firstId: () => order[0] || null,
    busyAgents: () => {
      const record = records.get(play.id);
      return new Set(record ? busyFor(currentStage(), record) : []);
    },
    decisionNote,
  };
  window.KIROWEEN_REPLAY = api;
  renderBar();
})();
