// DOM tests for the sign-in page and the main page's navigation (scenario-lab REQ-AU-11,
// REQ-AU-12, REQ-UI-05). The real files run in jsdom; only fetch is faked.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM, VirtualConsole } from "jsdom";

const ORCH = new URL("../../agents/orchestrator/src/kiroween_orchestrator/", import.meta.url);
const LOGIN_HTML = readFileSync(new URL("ui_login/login.html", ORCH), "utf8");
const LOGIN_JS = readFileSync(new URL("ui_login/login.js", ORCH), "utf8");
const INDEX_HTML = readFileSync(new URL("ui/index.html", ORCH), "utf8");
const APP_JS = readFileSync(new URL("ui/app.js", ORCH), "utf8");

const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check, what, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (check()) return;
    await tick();
  }
  assert.fail(`timed out waiting for ${what}`);
}

function openLogin({ answer, search = "" } = {}) {
  const calls = [];
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => {
    if (/navigation/i.test(error.message)) navigations.push(error.message);
  });
  const dom = new JSDOM(LOGIN_HTML, { url: "https://crypt.example.cloudfront.net/login" + search,
                                      runScripts: "outside-only", virtualConsole });
  const { window } = dom;
  window.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method, headers: init.headers,
                 body: init.body ? JSON.parse(init.body) : null, credentials: init.credentials });
    if (answer instanceof Error) throw answer;
    const reply = answer ?? { status: 200, body: { ok: true } };
    return { ok: reply.status < 400, status: reply.status,
             headers: { get: (n) => (n.toLowerCase() === "retry-after" ? reply.retryAfter ?? null : null) },
             json: async () => reply.body ?? {} };
  };
  // The redirect rule is a function in the script; expose it to the test without editing it.
  window.eval(LOGIN_JS + "\nwindow.__safeNext = safeNext; window.__explain = explain;");
  const document = window.document;
  const fill = (user, password) => {
    document.getElementById("username").value = user;
    document.getElementById("password").value = password;
  };
  const submit = () => document.getElementById("login")
    .dispatchEvent(new window.Event("submit", { cancelable: true, bubbles: true }));
  return { window, document, calls, navigations, fill, submit,
           message: () => document.getElementById("message").textContent, close: () => window.close() };
}

// ------------------------------------------------------------------------- the form


test("the form has labelled fields for the right autofill and a visible error area", () => {
  const ui = openLogin();
  try {
    const user = ui.document.getElementById("username");
    const password = ui.document.getElementById("password");
    assert.equal(ui.document.querySelector('label[for="username"]').textContent, "User name");
    assert.equal(ui.document.querySelector('label[for="password"]').textContent, "Password");
    assert.equal(user.getAttribute("autocomplete"), "username");
    assert.equal(password.getAttribute("autocomplete"), "current-password");
    assert.equal(password.type, "password");
    assert.equal(ui.document.getElementById("message").getAttribute("role"), "alert");
    assert.equal(ui.document.getElementById("submit").textContent, "Sign in");
    assert.match(ui.document.body.textContent, /nothing is applied without a person/);
  } finally { ui.close(); }
});

test("empty fields are refused without sending anything", async () => {
  const ui = openLogin();
  try {
    ui.fill("", "");
    ui.submit();
    await tick(30);
    assert.equal(ui.message(), "Enter your user name and password.");
    assert.equal(ui.calls.length, 0);
    ui.fill("judge", "");
    ui.submit();
    await tick(30);
    assert.equal(ui.calls.length, 0);
  } finally { ui.close(); }
});

test("a correct sign-in posts the credentials once as JSON and leaves the page", async () => {
  const ui = openLogin();
  try {
    ui.fill("judge", "correct horse");
    ui.submit();
    await until(() => ui.navigations.length > 0, "the redirect");
    assert.equal(ui.calls.length, 1);
    const [call] = ui.calls;
    assert.equal(call.url, "/auth/login");
    assert.equal(call.method, "POST");
    assert.equal(call.headers["Content-Type"], "application/json");
    assert.equal(call.credentials, "same-origin");
    assert.deepEqual(call.body, { username: "judge", password: "correct horse" });
  } finally { ui.close(); }
});

test("a wrong password is explained, the password is cleared and the form can be used again", async () => {
  const ui = openLogin({ answer: { status: 401, body: { detail: "wrong user name or password" } } });
  try {
    ui.fill("judge", "wrong");
    ui.submit();
    await until(() => ui.message() !== "", "the message");
    assert.equal(ui.message(), "Wrong user name or password.");
    assert.equal(ui.document.getElementById("password").value, "");
    assert.equal(ui.document.getElementById("username").value, "judge");
    assert.equal(ui.document.getElementById("submit").disabled, false);
    assert.equal(ui.navigations.length, 0);
  } finally { ui.close(); }
});

test("a lockout tells the visitor how long to wait, in minutes", async () => {
  const ui = openLogin({ answer: { status: 429, retryAfter: "840", body: {} } });
  try {
    ui.fill("judge", "x");
    ui.submit();
    await until(() => ui.message() !== "", "the message");
    assert.equal(ui.message(), "Too many attempts. Try again in 14 minute(s).");
  } finally { ui.close(); }
});

test("every other failure is a plain sentence", async () => {
  const cases = [
    [{ status: 503, body: {} }, "Sign-in is not set up on this server yet."],
    [{ status: 403, body: {} }, "The request was refused. Reload the page and try again."],
    [{ status: 415, body: {} }, "The request was refused. Reload the page and try again."],
    [{ status: 500, body: {} }, "Sign-in failed (500). Try again."],
    [{ status: 429, body: {} }, "Too many attempts. Try again in a while."],
    [new Error("offline"), "Could not reach the server. Check your connection and try again."],
  ];
  for (const [answer, expected] of cases) {
    const ui = openLogin({ answer });
    try {
      ui.fill("judge", "x");
      ui.submit();
      await until(() => ui.message() !== "", "the message");
      assert.equal(ui.message(), expected);
    } finally { ui.close(); }
  }
});

test("the password is never shown, stored or kept after a failure", async () => {
  const ui = openLogin({ answer: { status: 401, body: {} } });
  try {
    ui.fill("judge", "s3cret-value");
    ui.submit();
    await until(() => ui.message() !== "", "the message");
    assert.ok(!ui.document.body.textContent.includes("s3cret-value"));
    assert.equal(ui.window.localStorage.length, 0);
    assert.equal(ui.window.sessionStorage.length, 0);
    assert.equal(ui.document.getElementById("password").value, "");
  } finally { ui.close(); }
});

// -------------------------------------------------------------- where it sends you


test("sign-in returns only to a page under /ui on this site", () => {
  const ui = openLogin();
  try {
    const next = ui.window.__safeNext;
    const good = ["?next=/ui/", "?next=/ui", "?next=/ui/lab.html", "?next=%2Fui%2Flab.html",
                  "?next=/ui/index.html"];
    for (const search of good) {
      const expected = decodeURIComponent(search.slice(6));
      assert.equal(next(search), expected, search);
    }
    const bad = ["", "?next=", "?next=/", "?next=/login", "?next=//evil.example.com",
                 "?next=/ui//evil.example.com", "?next=https://evil.example.com/ui/",
                 "?next=javascript:alert(1)", "?next=/ui/../auth/logout", "?next=\\\\evil",
                 "?next=/ui/<script>", "?next=/uievil", "?next=/ui/%0d%0aSet-Cookie:x",
                 "?next=data:text/html,x", "?other=/ui/lab.html"];
    for (const search of bad) assert.equal(next(search), "/ui/", search);
  } finally { ui.close(); }
});

test("the explain table has no code-only messages for the common failures", () => {
  const ui = openLogin();
  try {
    for (const status of [401, 403, 415, 429, 503]) {
      assert.doesNotMatch(ui.window.__explain(status, null), /^\d+$/);
      assert.ok(ui.window.__explain(status, null).length > 15);
    }
  } finally { ui.close(); }
});

test("the sign-in page has no inline script, no handler attribute and sets text safely", () => {
  assert.doesNotMatch(LOGIN_HTML, /<script(?![^>]*\bsrc=)[^>]*>/);
  assert.doesNotMatch(LOGIN_HTML, /\son\w+\s*=/);
  assert.doesNotMatch(LOGIN_JS, /innerHTML|outerHTML|document\.write|eval\(/);
  assert.match(LOGIN_JS, /textContent/);
});

// ------------------------------------------------------------ the main page's nav


async function openIndex() {
  const calls = [];
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => {
    if (/navigation/i.test(error.message)) navigations.push(error.message);
  });
  const dom = new JSDOM(INDEX_HTML, { url: "https://crypt.example.cloudfront.net/ui/",
                                      runScripts: "outside-only", pretendToBeVisual: true,
                                      virtualConsole });
  dom.window.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET", headers: init.headers ?? {},
                 body: init.body ?? null });
    return { ok: true, status: 200, json: async () => (String(url).startsWith("/incidents") ? [] : {}) };
  };
  dom.window.eval(APP_JS);
  await tick(30);
  return { window: dom.window, document: dom.window.document, calls, navigations,
           close: () => dom.window.close() };
}

test("the main page links to the lab and offers to log out", async () => {
  const ui = await openIndex();
  try {
    const nav = ui.document.getElementById("topnav");
    const links = [...nav.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]);
    assert.deepEqual(links, [["Incident feed", "index.html"], ["Scenario Lab", "lab.html"]]);
    assert.equal(nav.querySelector("a[aria-current]").textContent, "Incident feed");
    assert.equal(ui.document.getElementById("logout").textContent, "Log out");
  } finally { ui.close(); }
});

test("Log out on the main page posts JSON to /auth/logout and goes to the sign-in page", async () => {
  const ui = await openIndex();
  try {
    ui.document.getElementById("logout").click();
    await until(() => ui.calls.some((c) => c.url === "/auth/logout"), "the logout request");
    const call = ui.calls.find((c) => c.url === "/auth/logout");
    assert.equal(call.method, "POST");
    assert.equal(call.headers["Content-Type"], "application/json");
    assert.equal(call.body, "{}");
    await until(() => ui.navigations.length > 0, "the redirect");
  } finally { ui.close(); }
});

test("a lab incident's approval says it was not saved as memory", async () => {
  const ui = await openIndex();
  try {
    const learningText = ui.window.eval(
      APP_JS.slice(APP_JS.indexOf("function learningText"),
                   APP_JS.indexOf("function relatedView")) + "; learningText");
    assert.match(learningText({ status: "disabled", error: "lab incident" }),
                 /Scenario Lab incident, and lab incidents are never saved as memory/);
    assert.equal(learningText({ status: "disabled", error: null }),
                 "Not learned: learning is not configured.");
    assert.match(learningText({ status: "error", error: "Boom" }), /Not learned: Boom/);
  } finally { ui.close(); }
});
