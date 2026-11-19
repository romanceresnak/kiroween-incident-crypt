// Sign-in page (scenario-lab REQ-AU-12). Text is only ever set with textContent, and the
// only place it redirects to is a path under /ui on this site.
"use strict";

function safeNext(search) {
  const next = new URLSearchParams(search).get("next") || "";
  // /ui, /ui/ or /ui/<segments>: each segment starts with a letter, digit, _, ~ or -, and a dot
  // may only sit between name characters (lab.html). So no "." or ".." segment, no empty
  // segment (//), no backslash, no % escape, and no scheme or host.
  const segment = "[A-Za-z0-9_~-]+(?:\\.[A-Za-z0-9_~-]+)*";
  if (new RegExp("^/ui(?:/|(?:/" + segment + ")+/?)?$").test(next)) return next;
  return "/ui/";
}

function explain(status, retryAfter) {
  if (status === 401) return "The crypt does not recognise you.";
  if (status === 429) {
    const seconds = Number(retryAfter);
    const wait = Number.isFinite(seconds) && seconds > 0
      ? Math.max(1, Math.ceil(seconds / 60)) + " minute(s)" : "a while";
    return "Too many knocks. The crypt stays sealed for " + wait + ".";
  }
  if (status === 503) return "The crypt is not open yet. Sign-in is not set up on this server.";
  if (status === 415 || status === 403) return "The gate refused the request. Reload the page and try again.";
  return "The gate jammed (" + status + "). Try again.";
}

async function signIn(event) {
  event.preventDefault();
  const message = document.getElementById("message");
  const button = document.getElementById("submit");
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  message.textContent = "";
  if (!username || !password) {
    message.textContent = "Speak your user name and password to enter.";
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ username, password }),
      credentials: "same-origin",
    });
    if (response.ok) {
      window.location.assign(safeNext(window.location.search));
      return;
    }
    message.textContent = explain(response.status, response.headers.get("Retry-After"));
  } catch (err) {
    message.textContent = "The crypt cannot be reached. Check your connection and try again.";
  }
  document.getElementById("password").value = "";
  button.disabled = false;
}

document.getElementById("login").addEventListener("submit", signIn);
