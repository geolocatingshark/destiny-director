// Shared request plumbing for the anchor web editors. Framework-free vanilla JS, served
// statically from /static/shared.js and reused by every editor page (the rotation editor
// today; a weekly-reset form later). No build step. Loaded (deferred) before each page's
// own script, so `api` is defined by the time that script runs.

// Same-origin JSON POST. Auth is the session cookie, which a same-origin fetch sends
// automatically, so nothing is embedded in the page. Returns the raw Response so callers
// can branch on res.ok and read res.text() themselves — the editors surface the server's
// plain-text preview/error body verbatim.
async function api(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
}
window.api = api;
