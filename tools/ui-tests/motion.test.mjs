// DOM tests for the crew animations at /ui.
//
// Two things are tested against the real index.html, app.js and style.css:
//  1. app.js adds the "busy" class to an agent's card while it works on an incident and
//     removes it when the agent is idle.
//  2. What the CSS then does with that class. jsdom does not run CSS animations or evaluate
//     prefers-reduced-motion, so a small cascade resolver (specificity, source order, var(),
//     @media) works out which animation each card, thread and glow layer ends up with, in
//     normal and in reduced-motion mode.
//
// UI_APP_JS and UI_STYLE_CSS point the tests at other copies (used to prove they fail).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const UI = new URL("../../agents/orchestrator/src/kiroween_orchestrator/ui/", import.meta.url);
const HTML = readFileSync(new URL("index.html", UI), "utf8");
const APP = readFileSync(process.env.UI_APP_JS ?? new URL("app.js", UI), "utf8");
const CSS = readFileSync(process.env.UI_STYLE_CSS ?? new URL("style.css", UI), "utf8");

const NOW = "2026-11-17T15:00:00Z";

// What each character does while busy (the request): spider on a thread, ghost floating and
// fading, zombie shuffling, the others a plain bob with a glow pulse.
const EXPECTED = {
  orchestrator: { motion: "bob", seconds: 2, glow: "glow" },
  "cpu-memory": { motion: "bob", seconds: 2, glow: "glow" },
  "incident-memory": { motion: "bob", seconds: 2, glow: "glow" },
  network: { motion: "dangle", seconds: 2.4, glow: "none", thread: true },
  storage: { motion: "float", seconds: 2.5, glow: "none" },
  "deploy-rollout": { motion: "shuffle", seconds: 2.2, glow: "none" },
};
const AGENTS = Object.keys(EXPECTED);

// --- the page ------------------------------------------------------------------------------

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

function incident(id, status, category = null, reason = "OOMKilled") {
  return {
    incident: { incident_id: id, source: "watcher", reason, namespace: "shop",
                workload: { kind: "Deployment", name: id }, pod: null, container: null,
                observed_at: NOW, message: "", depends_on: [], similar_incidents: [] },
    status, classification: category ? { category, confidence: "high", rationale: "r" } : null,
    investigation: null, usage_by_agent: {}, related_incidents: [], similar_incidents: [],
    related_to: null, approval: null, created_at: NOW, updated_at: NOW,
  };
}

// Opens /ui with a feed the test controls. Polling is driven by poll(), not by timers.
async function open(records) {
  const state = { records };
  const dom = new JSDOM(HTML, { url: "http://localhost:8080/ui/", runScripts: "outside-only",
                                pretendToBeVisual: true });
  const { window } = dom;
  const document = window.document;
  const timers = [];
  window.setInterval = (fn) => { timers.push(fn); return timers.length; };
  window.fetch = async (url) => ({
    ok: true, status: 200, json: async () => (url.startsWith("/incidents?") ? state.records : null),
  });
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.append(style);
  window.eval(APP);
  await until(() => /^live/.test(document.getElementById("live").textContent), "the first feed");
  return {
    window, document, state, close: () => window.close(),
    figure: (agent) => document.querySelector(`.crew figure[data-agent="${agent}"]`),
    busy: () => [...document.querySelectorAll(".crew figure.busy")]
      .map((f) => f.dataset.agent).sort(),
    async poll() {
      await Promise.all(timers.map((fn) => fn()));
      await tick();
    },
  };
}

// --- 1. the class toggle -------------------------------------------------------------------

test("the six characters are on the page", async () => {
  const ui = await open([]);
  try {
    const found = [...ui.document.querySelectorAll(".crew figure")].map((f) => f.dataset.agent);
    assert.deepEqual(found.sort(), [...AGENTS].sort());
  } finally {
    ui.close();
  }
});

test("nobody is busy when the crypt is quiet or every incident is settled", async () => {
  const settled = ["completed", "awaiting_approval", "approved", "rejected", "failed",
                   "no_specialist", "correlated"];
  const ui = await open(settled.map((s, i) => incident(`i${i}`, s, "network")));
  try {
    assert.deepEqual(ui.busy(), []);
  } finally {
    ui.close();
  }
});

test("an agent gets the busy class while it works and loses it when it is idle", async () => {
  const ui = await open([incident("a", "processing", "network")]);
  try {
    // Classified as network: the orchestrator, incident-memory and the network specialist work.
    assert.deepEqual(ui.busy(), ["incident-memory", "network", "orchestrator"]);
    assert.equal(ui.figure("storage").classList.contains("busy"), false);

    ui.state.records = [incident("a", "completed", "network")];
    await ui.poll();
    assert.deepEqual(ui.busy(), [], "busy must be removed when the incident is done");
  } finally {
    ui.close();
  }
});

test("the class follows the incident through its life", async () => {
  const ui = await open([incident("a", "accepted")]);
  try {
    assert.deepEqual(ui.busy(), ["incident-memory", "orchestrator"]);   // not yet classified
    ui.state.records = [incident("a", "processing", "storage")];
    await ui.poll();
    assert.deepEqual(ui.busy(), ["incident-memory", "orchestrator", "storage"]);
    ui.state.records = [incident("a", "awaiting_approval", "storage")];
    await ui.poll();
    assert.deepEqual(ui.busy(), []);
    ui.state.records = [incident("b", "processing", "deploy_rollout")];
    await ui.poll();
    assert.deepEqual(ui.busy(), ["deploy-rollout", "incident-memory", "orchestrator"]);
  } finally {
    ui.close();
  }
});

test("two incidents in different categories light up both specialists", async () => {
  const ui = await open([incident("a", "processing", "cpu_memory"),
                         incident("b", "processing", "storage"),
                         incident("c", "completed", "network")]);
  try {
    assert.deepEqual(ui.busy(), ["cpu-memory", "incident-memory", "orchestrator", "storage"]);
  } finally {
    ui.close();
  }
});

// --- the cascade resolver ----------------------------------------------------------------------

function specificity(selector) {
  let s = selector;
  const attrs = (s.match(/\[[^\]]*\]/g) ?? []).length;
  s = s.replace(/\[[^\]]*\]/g, "");
  const pseudoElements = (s.match(/::[\w-]+/g) ?? []).length;
  s = s.replace(/::[\w-]+/g, "");
  const pseudoClasses = (s.match(/:[\w-]+/g) ?? []).length;
  s = s.replace(/:[\w-]+/g, "");
  const ids = (s.match(/#[\w-]+/g) ?? []).length;
  s = s.replace(/#[\w-]+/g, "");
  const classes = (s.match(/\.[\w-]+/g) ?? []).length;
  s = s.replace(/\.[\w-]+/g, "");
  const tags = (s.match(/[A-Za-z][\w-]*/g) ?? []).length;
  return ids * 1e6 + (classes + attrs + pseudoClasses) * 1e3 + tags + pseudoElements;
}

function declarations(style) {
  const out = {};
  for (let i = 0; i < style.length; i += 1) {
    const name = style.item ? style.item(i) : style[i];
    out[name] = style.getPropertyValue(name).trim();
  }
  return out;
}

// Style rules in source order; rules inside a media query only when it is the reduced-motion
// query and `reduced` is set. Every other media query (max-width) is treated as not matching.
function styleRules(document, reduced) {
  const out = [];
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.media) {
        if (reduced && /prefers-reduced-motion:\s*reduce/.test(rule.media.mediaText)) {
          walk(rule.cssRules);
        }
      } else if (rule.selectorText !== undefined) {
        out.push(rule);
      }
    }
  };
  walk(document.styleSheets[0].cssRules);
  return out;
}

function cascade(el, pseudo, reduced) {
  const matched = [];
  styleRules(el.ownerDocument, reduced).forEach((rule, order) => {
    for (const selector of rule.selectorText.split(",").map((s) => s.trim())) {
      const parts = selector.match(/^(.*?)(::(?:before|after))?$/);
      const forPseudo = parts[2] ? parts[2].slice(2) : null;
      if (forPseudo === pseudo && el.matches(parts[1])) {
        matched.push({ specificity: specificity(selector), order, style: rule.style });
      }
    }
  });
  matched.sort((a, b) => a.specificity - b.specificity || a.order - b.order);
  return Object.assign({}, ...matched.map((m) => declarations(m.style)));
}

// Effective property values for an element (or its ::before / ::after) with var() resolved.
// A pseudo-element inherits the element's custom properties.
function computed(el, pseudo = null, reduced = false) {
  const own = cascade(el, null, reduced);
  const target = pseudo ? cascade(el, pseudo, reduced) : own;
  const variables = { ...own, ...target };
  const out = {};
  for (const [name, value] of Object.entries(target)) {
    if (name.startsWith("--")) continue;
    out[name] = value.replace(/var\((--[\w-]+)\)/g, (whole, v) => variables[v] ?? whole);
  }
  return out;
}

function animation(el, pseudo = null, reduced = false) {
  const value = computed(el, pseudo, reduced).animation;
  if (!value) return null;
  const t = value.split(/\s+/);
  const seconds = t.find((x) => /^\d*\.?\d+s$/.test(x));
  return {
    name: t[0], seconds: seconds ? parseFloat(seconds) : null,
    timing: t.find((x) => /^(linear|ease|ease-in|ease-out|ease-in-out)$/.test(x)) ?? null,
    infinite: t.includes("infinite"),
  };
}

const runs = (a) => a !== null && a.name !== "none";

function keyframes(document) {
  const out = {};
  for (const rule of document.styleSheets[0].cssRules) {
    if (rule.constructor.name !== "CSSKeyframesRule") continue;
    out[rule.name] = [...rule.cssRules].map((k) => ({
      at: k.keyText.split(",").map((s) => s.trim()), props: declarations(k.style) }));
  }
  return out;
}

test("resolver sanity: specificity and source order decide, var() resolves", async () => {
  assert.ok(specificity(".crew figure.busy") < specificity('.crew figure[data-agent="x"].busy'));
  assert.equal(specificity(".crew figure[data-agent].busy::before"),
               specificity('.crew figure[data-agent="network"].busy::before'));
  assert.ok(specificity("#a") > specificity(".a.b.c.d"));
  const ui = await open([]);
  try {
    const spider = ui.figure("network");
    assert.equal(computed(spider)["--motion"], undefined);   // custom properties are not output
    assert.equal(cascade(spider, null, false)["--motion"], "dangle");
    assert.equal(cascade(ui.figure("storage"), null, false)["--motion"], "float");
  } finally {
    ui.close();
  }
});

// --- 2. what the CSS does -----------------------------------------------------------------------

test("idle cards do not animate: no motion, no thread, an invisible glow", async () => {
  const ui = await open([]);
  try {
    for (const agent of AGENTS) {
      const card = ui.figure(agent);
      assert.equal(runs(animation(card)), false, `${agent} animates while idle`);
      assert.equal(runs(animation(card, "after")), false, `${agent} glow animates while idle`);
      assert.equal(computed(card, "after").opacity, "0", `${agent} glow is visible while idle`);
      assert.equal(computed(card, "before").content, undefined, `${agent} has a thread`);
    }
  } finally {
    ui.close();
  }
});

test("each busy character gets its own motion, 1.5-2.5 s, ease-in-out, infinite", async () => {
  const ui = await open([]);
  try {
    for (const [agent, want] of Object.entries(EXPECTED)) {
      const card = ui.figure(agent);
      card.classList.add("busy");
      const a = animation(card);
      assert.equal(a.name, want.motion, agent);
      assert.equal(a.seconds, want.seconds, agent);
      assert.ok(a.seconds >= 1.5 && a.seconds <= 2.5, `${agent} ${a.seconds}s`);
      assert.equal(a.timing, "ease-in-out", agent);
      assert.equal(a.infinite, true, agent);
    }
    const names = AGENTS.map((agent) => animation(ui.figure(agent)));
    assert.equal(new Set(names.map((a) => a.name)).size, 4, "bob, dangle, float, shuffle");
  } finally {
    ui.close();
  }
});

test("vampire, necromancer and archivist pulse a glow; the others do not", async () => {
  const ui = await open([]);
  try {
    for (const [agent, want] of Object.entries(EXPECTED)) {
      const card = ui.figure(agent);
      card.classList.add("busy");
      const glow = animation(card, "after");
      if (want.glow === "glow") {
        assert.equal(glow.name, "glow", agent);
        assert.ok(glow.seconds >= 1.5 && glow.seconds <= 2.5, agent);
        assert.equal(glow.timing, "ease-in-out");
        assert.equal(glow.infinite, true);
      } else {
        assert.equal(runs(glow), false, `${agent} should not pulse`);
      }
      assert.notEqual(computed(card, "after").opacity, "0", `${agent} glow hidden while busy`);
    }
  } finally {
    ui.close();
  }
});

test("the spider hangs from a thread that exists only while it is busy", async () => {
  const ui = await open([]);
  try {
    const spider = ui.figure("network");
    assert.equal(computed(spider, "before").content, undefined);   // idle: no thread
    spider.classList.add("busy");
    const thread = computed(spider, "before");
    assert.equal(thread.content, '""');
    assert.equal(thread.width, "1px");
    assert.equal(thread.position, "absolute");
    const move = animation(spider);
    const line = animation(spider, "before");
    assert.equal(line.name, "thread");
    assert.deepEqual([line.seconds, line.timing, line.infinite],
                     [move.seconds, move.timing, move.infinite], "thread stays in step");
    for (const agent of AGENTS.filter((a) => a !== "network")) {
      const card = ui.figure(agent);
      card.classList.add("busy");
      assert.equal(computed(card, "before").content, undefined, `${agent} has a thread`);
    }
  } finally {
    ui.close();
  }
});

test("the thread stays anchored: it moves up as the spider drops and stretches to meet it", async () => {
  const ui = await open([]);
  try {
    const frames = keyframes(ui.document);
    const dangle = frames.dangle.find((f) => f.at.includes("50%")).props.transform;
    const drop = parseFloat(dangle.match(/translateY\((-?[\d.]+)px\)/)[1]);
    const thread = frames.thread.find((f) => f.at.includes("50%")).props.transform;
    const lift = parseFloat(thread.match(/translateY\((-?[\d.]+)px\)/)[1]);
    const stretch = parseFloat(thread.match(/scaleY\(([\d.]+)\)/)[1]);
    const spider = ui.figure("network");
    spider.classList.add("busy");
    const rule = computed(spider, "before");
    const length = parseFloat(rule.height);
    assert.equal(parseFloat(rule.top), -length, "the thread sits directly above the card");
    assert.equal(lift, -drop, "the thread cancels the card's movement");
    assert.equal(length * stretch, length + drop, "and grows by exactly the drop");
    assert.equal(rule["transform-origin"], "top center");
  } finally {
    ui.close();
  }
});

test("keyframes use transform and opacity only, and every animation name exists", async () => {
  const ui = await open([]);
  try {
    const frames = keyframes(ui.document);
    assert.deepEqual(Object.keys(frames).sort(),
                     ["bob", "dangle", "float", "glow", "shuffle", "thread"]);
    for (const [name, list] of Object.entries(frames)) {
      for (const frame of list) {
        for (const prop of Object.keys(frame.props)) {
          assert.ok(["transform", "opacity"].includes(prop), `${name}: ${prop}`);
        }
      }
    }
    for (const reduced of [false, true]) {
      for (const agent of AGENTS) {
        const card = ui.figure(agent);
        card.classList.add("busy");
        for (const pseudo of [null, "before", "after"]) {
          const a = animation(card, pseudo, reduced);
          if (runs(a)) assert.ok(frames[a.name], `${agent} ${pseudo} uses missing ${a.name}`);
        }
      }
    }
  } finally {
    ui.close();
  }
});

test("the ghost fades slightly, the zombie shuffles sideways, the rest bob vertically", async () => {
  const ui = await open([]);
  try {
    const frames = keyframes(ui.document);
    const opacities = frames.float.map((f) => parseFloat(f.props.opacity));
    assert.ok(Math.min(...opacities) >= 0.6 && Math.min(...opacities) < 1, "slight fade");
    assert.equal(Math.max(...opacities), 1);
    const xs = frames.shuffle.map(
      (f) => parseFloat(f.props.transform.match(/translate\(\s*(-?[\d.]+)/)[1]));
    assert.ok(xs.some((x) => x < 0) && xs.some((x) => x > 0), "side to side");
    assert.ok(Math.max(...xs.map(Math.abs)) <= 4, "a small shuffle");
    for (const name of ["bob", "float", "dangle"]) {
      for (const f of frames[name]) assert.doesNotMatch(f.props.transform, /translateX|translate\(/);
    }
    for (const f of frames.glow) assert.equal(f.props.transform, undefined);
  } finally {
    ui.close();
  }
});

// --- 3. reduced motion --------------------------------------------------------------------------

test("prefers-reduced-motion: busy cards stay put, lose the thread, and keep the glow", async () => {
  const ui = await open([]);
  try {
    const frames = keyframes(ui.document);
    for (const agent of AGENTS) {
      const card = ui.figure(agent);
      card.classList.add("busy");
      // Normal motion first, so this test cannot pass on a page that never animates.
      assert.equal(runs(animation(card, null, false)), true, `${agent} normal`);

      assert.equal(runs(animation(card, null, true)), false, `${agent} still moves`);
      assert.equal(computed(card, "before", true).display, "none", `${agent} thread shown`);
      assert.equal(runs(animation(card, "before", true)), false, `${agent} thread moves`);

      const glow = animation(card, "after", true);
      assert.equal(glow.name, "glow", `${agent} lost its glow`);
      assert.equal(glow.infinite, true);
      assert.notEqual(computed(card, "after", true).opacity, "0");
      assert.deepEqual(Object.keys(frames[glow.name].flatMap((f) => Object.keys(f.props))
        .reduce((o, k) => ({ ...o, [k]: 1 }), {})), ["opacity"], "glow changes opacity only");
    }
  } finally {
    ui.close();
  }
});

test("reduced motion changes nothing for idle cards and the border still marks busy ones", async () => {
  const ui = await open([]);
  try {
    for (const agent of AGENTS) {
      const card = ui.figure(agent);
      assert.equal(runs(animation(card, null, true)), false);
      assert.equal(computed(card, "after", true).opacity, "0");
      card.classList.add("busy");
      assert.equal(computed(card, null, true)["border-color"], "var(--pumpkin)");
    }
  } finally {
    ui.close();
  }
});

test("end to end: the spider dangles while an incident is classified as network", async () => {
  const ui = await open([incident("a", "processing", "network")]);
  try {
    const spider = ui.figure("network");
    assert.equal(spider.classList.contains("busy"), true);
    assert.equal(animation(spider).name, "dangle");
    assert.equal(runs(animation(spider, null, true)), false);
    ui.state.records = [incident("a", "completed", "network")];
    await ui.poll();
    assert.equal(spider.classList.contains("busy"), false);
    assert.equal(runs(animation(spider)), false);
  } finally {
    ui.close();
  }
});
