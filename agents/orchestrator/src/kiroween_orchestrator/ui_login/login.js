// Sign-in page (scenario-lab REQ-AU-12). Text is only ever set with textContent, and the
// only place it redirects to is a path under /ui on this site.
"use strict";

function safeNext(search) {
  const next = new URLSearchParams(search).get("next") || "";
  // A relative path under /ui. Not //host, not a scheme, not a backslash trick.
  if (/^\/ui(\/[A-Za-z0-9._~\-\/%]*)?$/.test(next) && !next.includes("//")) return next;
  return "/ui/";
}

function explain(status, retryAfter) {
  if (status === 401) return "Wrong user name or password.";
  if (status === 429) {
    const seconds = Number(retryAfter);
    const wait = Number.isFinite(seconds) && seconds > 0
      ? Math.max(1, Math.ceil(seconds / 60)) + " minute(s)" : "a while";
    return "Too many attempts. Try again in " + wait + ".";
  }
  if (status === 503) return "Sign-in is not set up on this server yet.";
  if (status === 415 || status === 403) return "The request was refused. Reload the page and try again.";
  return "Sign-in failed (" + status + "). Try again.";
}

async function signIn(event) {
  event.preventDefault();
  const message = document.getElementById("message");
  const button = document.getElementById("submit");
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  message.textContent = "";
  if (!username || !password) {
    message.textContent = "Enter your user name and password.";
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
    message.textContent = "Could not reach the server. Check your connection and try again.";
  }
  document.getElementById("password").value = "";
  button.disabled = false;
}

document.getElementById("login").addEventListener("submit", signIn);
