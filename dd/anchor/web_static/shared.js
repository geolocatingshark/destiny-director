// Shared client for the anchor web editors. Framework-free vanilla JS, served statically
// from /static/shared.js and loaded (deferred) before each page's own script, so the
// globals below are defined by the time that script runs. No build step.
//
// Three exports:
//   api(path, body)        — same-origin JSON POST (auth is the session cookie).
//   initPostPreview(opts)  — the standalone live previewer (#previewBox) — reusable on its
//                            own by any page that has a post to preview.
//   initPostForm(opts)     — the full hybrid-post form lifecycle (status/problems, the
//                            create/edit(±publish)/delete/autopost buttons and their
//                            visibility), which drives a previewer internally.
// The two hybrid-post forms (trials, weekly_reset) share the SAME element ids and the same
// server contract (POST /{prefix}/{preview,create,edit,delete,auto}), differing only by
// their route prefix, a couple of delete-confirm strings, and their per-form widgets +
// readForm() payload shape — so everything except those stays here.

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

// Internal DOM helpers (each page's own script keeps its own copies for widget building).
const _byId = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Standalone live previewer
// ---------------------------------------------------------------------------
// A self-contained preview of what the Discord post will look like, decoupled from any
// particular form: give it a route prefix and a readForm() that returns the post payload,
// and it POSTs to /<prefix>/preview and renders the server's reply into a box. Returned as
// a small handle so a caller (a form, or a future standalone editor/command manager) can
// trigger it: `render()` now, or `schedule()` debounced after a burst of edits.
function initPostPreview({
  routePrefix,
  readForm,
  box = _byId("previewBox"),
  accentColor = null,
  debounceMs = 400,
} = {}) {
  // Mirror the post's CV2 accent colour as the preview's left bar (see #previewBox CSS).
  // Only --post-accent (preview bar + set-card selection) tracks the post; --accent (page
  // chrome) stays fixed.
  if (accentColor) {
    document.documentElement.style.setProperty("--post-accent", accentColor);
  }

  async function render() {
    try {
      const res = await api(`/${routePrefix}/preview`, readForm());
      const body = await res.text();
      // On ok the server returns SAFE HTML (render_post_html: escaped leaves, whitelisted
      // tags, http(s)-validated URLs) — innerHTML renders emoji/markdown. On failure the
      // body is an untrusted error string, so use textContent to keep it escaped.
      if (res.ok) {
        box.innerHTML = body;
      } else {
        box.textContent = "Preview failed:\n" + body;
      }
    } catch (e) {
      box.textContent = "Preview error: " + e;
    }
  }

  let timer;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(render, debounceMs);
  }

  return { render, schedule };
}
window.initPostPreview = initPostPreview;

// ---------------------------------------------------------------------------
// Hybrid-post form lifecycle
// ---------------------------------------------------------------------------
// Wires the status line, problem list, the create/edit(±publish)/delete buttons and their
// visibility, the autopost toggle, and the live preview against the shared element ids and
// the /<routePrefix>/{preview,create,edit,delete,auto} server contract. The caller supplies
// only what genuinely differs between producers: the route prefix, a readForm() returning
// the post payload, the two delete-confirm / post-delete strings (labels), the bootstrap
// object, the previewer handle, and an onEdit run on every form edit.
//
// The caller owns the previewer (initPostPreview) and onEdit because its widgets are built —
// and wire their onChange to onEdit — BEFORE this runs (e.g. weekly_reset's Tom Selects,
// whose changes don't bubble a DOM "input"; onEdit is also where it re-runs its
// Iron-Banner⇒Trials sync). onEdit must schedule the preview itself (preview.schedule()).
function initPostForm({
  routePrefix,
  readForm,
  preview,
  onEdit,
  boot = window.__BOOTSTRAP__,
  labels = {},
} = {}) {
  const statusEl = _byId("status");
  const problemsEl = _byId("problems");

  function setStatus(msg, ok) {
    statusEl.textContent = msg;
    statusEl.className = ok ? "ok" : "err";
  }
  function showProblems(problems) {
    problemsEl.replaceChildren(
      ...problems.map((p) => Object.assign(document.createElement("li"), { textContent: p })),
    );
    problemsEl.classList.toggle("hidden", !problems.length);
  }

  // Native inputs bubble "input" to onEdit (which schedules the preview); widget onChange is
  // wired to the same onEdit by the caller.
  _byId("form").addEventListener("submit", (e) => e.preventDefault());
  _byId("form").addEventListener("input", onEdit);
  _byId("refreshBtn").addEventListener("click", preview.render);

  // --- action-button visibility ----------------------------------------
  // `postThisPeriod` = a post exists for the CURRENT period (Trials may skip a weekend, so
  // this is often false); `crossposted` = it's been published to followers. Both seed from
  // the GET bootstrap and update after every create/edit/delete. The two Create buttons
  // show only when there's no post this period; once one exists they hide and Edit/Delete
  // take over. "Edit & publish" is the way to publish a post created unpublished, so it
  // hides once crossposted.
  let postThisPeriod = !!boot.post_this_period;
  let crossposted = !!boot.crossposted;
  function updateButtons() {
    _byId("createBtn").hidden = postThisPeriod;
    _byId("createPublishBtn").hidden = postThisPeriod;
    _byId("editBtn").hidden = !postThisPeriod;
    _byId("deleteBtn").hidden = !postThisPeriod;
    _byId("editPublishBtn").hidden = !postThisPeriod || crossposted;
  }
  updateButtons();

  // --- create / edit (± publish) ---------------------------------------
  // One helper backs all four post buttons: it POSTs the form to /create or /edit with a
  // `publish` flag. The unpublished path is lenient (advisory `warnings`); the publish path
  // blocks on `problems`. On success it re-syncs the button state from the response.
  async function postAction(path, publish, okMsg) {
    const res = await api(`/${routePrefix}/${path}`, { ...readForm(), publish });
    const data = await res.json();
    if (data.problems) {
      showProblems(data.problems);
      setStatus("Not done — see problems above.", false);
      return false;
    }
    if (!res.ok || !data.ok) {
      showProblems(data.error ? [data.error] : ["Request failed — try again."]);
      setStatus("Not done — see problems above.", false);
      return false;
    }
    showProblems(data.warnings || []); // advisory only — the post still went through
    postThisPeriod = !!data.post_this_period;
    crossposted = !!data.crossposted;
    updateButtons();
    const warned = (data.warnings || []).length;
    setStatus(
      data.note || (warned ? `${okMsg} — ${warned} warning(s) below.` : okMsg),
      true,
    );
    return true;
  }

  // A button handler wrapping postAction with a confirm (publish only) + status framing.
  function wirePost(id, path, publish, framing, okMsg, confirmMsg) {
    _byId(id).addEventListener("click", async () => {
      if (confirmMsg && !confirm(confirmMsg)) return;
      setStatus(framing, true);
      try {
        await postAction(path, publish, okMsg);
      } catch (e) {
        setStatus(framing.replace(/…$/, "") + " error: " + e, false);
      }
    });
  }
  wirePost("createBtn", "create", false, "Creating post…", "Post created (uncrossposted) ✓");
  wirePost(
    "createPublishBtn", "create", true, "Creating & publishing…", "Published ✓",
    "Create the post AND publish it to every follower?",
  );
  wirePost("editBtn", "edit", false, "Editing post…", "Post edited ✓");
  wirePost(
    "editPublishBtn", "edit", true, "Editing & publishing…", "Published ✓",
    "Edit the post AND publish it to every follower?",
  );

  // --- delete post ------------------------------------------------------
  _byId("deleteBtn").addEventListener("click", async () => {
    if (!postThisPeriod) return;
    const msg = crossposted ? labels.deletePublished : labels.deleteDraft;
    if (!confirm(msg)) return;
    setStatus("Deleting…", true);
    try {
      const res = await api(`/${routePrefix}/delete`, {});
      const data = await res.json();
      if (!res.ok || !data.ok) {
        return setStatus("Delete failed" + (data.error ? ": " + data.error : "."), false);
      }
      postThisPeriod = false;
      crossposted = false;
      updateButtons();
      setStatus(labels.deleted, true);
    } catch (e) {
      setStatus("Delete error: " + e, false);
    }
  });

  // --- autopost toggle --------------------------------------------------
  const autopost = _byId("autopost");
  autopost.checked = !!boot.autopost_enabled;
  autopost.addEventListener("change", async () => {
    try {
      const res = await api(`/${routePrefix}/auto`, { enabled: autopost.checked });
      const data = await res.json();
      autopost.checked = !!data.enabled;
      setStatus("Autopost " + (data.enabled ? "enabled" : "disabled") + ".", true);
    } catch (e) {
      setStatus("Autopost toggle error: " + e, false);
    }
  });

  // Initial preview.
  preview.render();
}
window.initPostForm = initPostForm;
