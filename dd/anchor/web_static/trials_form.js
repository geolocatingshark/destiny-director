// Trials of Osiris form script. A self-contained form for editing the trials draft,
// served statically from /static/trials_form.js (no build step). The page
// (trials_form.html) is served by dd.anchor.extensions.trials, which substitutes
// {draft, options, autopost_enabled, default_image_url, accent_color, post_this_period,
// crossposted} into a small inline <script> as window.__BOOTSTRAP__ before this runs.
// This reads that global, edits the draft client-side and POSTs it (via the shared api()
// helper) to /trials/{preview,create,edit,delete,auto} — auth is the central
// Discord-OAuth session cookie (sent automatically on the same-origin fetch). The server
// re-resolves the focus-pool weapons and re-validates, so this form is a convenience,
// not a trust boundary.

const BOOT = window.__BOOTSTRAP__;
const { draft, options } = BOOT;
// Mirror the post's CV2 accent colour as the preview's left bar (see #previewBox CSS).
if (BOOT.accent_color) {
  document.documentElement.style.setProperty("--accent", BOOT.accent_color);
}
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of [].concat(kids)) n.append(k);
  return n;
};

$("authNote").textContent =
  "Signed in via Discord (about 30 days). Save writes straight to the draft.";

// item hash (string) -> item record, for hydrating a focus weapon from its saved hash.
const itemByHash = new Map(options.items.map((i) => [String(i.hash), i]));
// The full weapon pool as Tom Select options. Value is the manifest hash (as a string)
// so a pick submits the hash for the light.gg deep link; searching spans name/type/rarity.
const weaponOptions = options.items.map((i) => ({
  value: String(i.hash),
  name: i.name,
  type: i.type,
  rarity: i.rarity,
  label: `${i.name} — ${i.type} · ${i.rarity}`,
}));

// --- bonus focus pool: a Tom Select multi over the weapon pool ----------
// create:true lets the team add a weapon that isn't in the manifest pool as plain text
// (value = the typed name); the server resolves either a hash or a name via resolve_weapon.
const focusTS = new TomSelect($("focusPool"), {
  options: weaponOptions,
  valueField: "value",
  labelField: "label",
  searchField: ["name", "type", "rarity"],
  maxOptions: 50,
  plugins: ["remove_button"],
  hideSelected: true,
  create: true,
  placeholder: "Search weapons…",
  onChange: onEdit,
  render: {
    option: (d, esc) => `<div>${esc(d.label || d.value)}</div>`,
    item: (d, esc) => `<div>${esc(d.label || d.value)}</div>`,
    option_create: (d, esc) => `<div class="create">Add <strong>${esc(d.input)}</strong>…</div>`,
  },
});
// Hydrate from the saved focus pool: by hash when we have one (and it's in the pool),
// else inject the plain name as a one-off option so a carried-over unlinked name survives.
for (const w of draft.focus_pool || []) {
  const hash = w.hash != null ? String(w.hash) : "";
  if (hash && itemByHash.has(hash)) {
    focusTS.addItem(hash, true);
  } else if (w.name) {
    focusTS.addOption({ value: w.name, label: w.name, name: w.name });
    focusTS.addItem(w.name, true);
  }
}

// --- populate the native fields ----------------------------------------
$("resetAt").value = draft.reset_ts
  ? new Date(draft.reset_ts * 1000).toISOString().slice(0, 16)
  : "";
$("mapsText").value = (draft.featured_maps || []).join("\n");
$("notesText").value = (draft.notes || []).join("\n");
$("imageUrl").value = draft.image_url || "";
// Pre-check "use as default" when this week's image already is the saved default.
$("imageDefault").checked =
  !!BOOT.default_image_url && (draft.image_url || "") === BOOT.default_image_url;
$("autopost").checked = !!BOOT.autopost_enabled;

// --- read the form into the payload the server expects -----------------
function readForm() {
  const at = $("resetAt").value;
  return {
    reset_ts: at ? Math.floor(Date.parse(at + "Z") / 1000) : draft.reset_ts,
    maps_text: $("mapsText").value,
    focus_pool: focusTS.getValue(), // array of hash strings and/or typed names
    image_url: $("imageUrl").value.trim(),
    set_default_image: $("imageDefault").checked,
    notes_text: $("notesText").value,
  };
}

// --- status + problems -------------------------------------------------
function setStatus(msg, ok) {
  const s = $("status");
  s.textContent = msg;
  s.className = ok ? "ok" : "err";
}
function showProblems(problems) {
  const box = $("problems");
  box.replaceChildren(...problems.map((p) => el("li", { textContent: p })));
  box.classList.toggle("hidden", !problems.length);
}

// --- preview (debounced ~400ms) ----------------------------------------
let previewTimer;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(renderPreview, 400);
}
function onEdit() {
  schedulePreview();
}
async function renderPreview() {
  try {
    const res = await api("/trials/preview", readForm());
    const body = await res.text();
    // On ok the server returns SAFE HTML (render_post_html: escaped leaves, whitelisted
    // tags, http(s)-validated URLs) — innerHTML renders emoji/markdown. On failure the
    // body is an untrusted error string, so use textContent to keep it escaped.
    if (res.ok) {
      $("previewBox").innerHTML = body;
    } else {
      $("previewBox").textContent = "Preview failed:\n" + body;
    }
  } catch (e) {
    $("previewBox").textContent = "Preview error: " + e;
  }
}

$("form").addEventListener("submit", (e) => e.preventDefault());
$("form").addEventListener("input", onEdit);
$("refreshBtn").addEventListener("click", renderPreview);

// --- action-button visibility ------------------------------------------
// `postThisPeriod` = a post exists for the CURRENT period (Trials may skip a weekend, so
// this is often false); `crossposted` = it's been published to followers. Both seed from
// the GET bootstrap and update after every create/edit/delete. The two Create buttons
// show only when there's no post this period; once one exists they hide and Edit/Delete
// take over. "Edit & publish" is the way to publish a post created unpublished, so it
// hides once crossposted.
let postThisPeriod = !!BOOT.post_this_period;
let crossposted = !!BOOT.crossposted;
function updateButtons() {
  $("createBtn").hidden = postThisPeriod;
  $("createPublishBtn").hidden = postThisPeriod;
  $("editBtn").hidden = !postThisPeriod;
  $("deleteBtn").hidden = !postThisPeriod;
  $("editPublishBtn").hidden = !postThisPeriod || crossposted;
}
updateButtons();

// --- create / edit (± publish) -----------------------------------------
// One helper backs all four post buttons: it POSTs the form to /create or /edit with a
// `publish` flag. The unpublished path is lenient (advisory `warnings`); the publish path
// blocks on `problems`. On success it re-syncs the button state from the response.
async function postAction(path, publish, okMsg) {
  const res = await api("/trials/" + path, { ...readForm(), publish });
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

$("createBtn").addEventListener("click", async () => {
  setStatus("Creating post…", true);
  try {
    await postAction("create", false, "Post created (uncrossposted) ✓");
  } catch (e) {
    setStatus("Create error: " + e, false);
  }
});

$("createPublishBtn").addEventListener("click", async () => {
  if (!confirm("Create the post AND publish it to every follower?")) return;
  setStatus("Creating & publishing…", true);
  try {
    await postAction("create", true, "Published ✓");
  } catch (e) {
    setStatus("Create error: " + e, false);
  }
});

$("editBtn").addEventListener("click", async () => {
  setStatus("Editing post…", true);
  try {
    await postAction("edit", false, "Post edited ✓");
  } catch (e) {
    setStatus("Edit error: " + e, false);
  }
});

$("editPublishBtn").addEventListener("click", async () => {
  if (!confirm("Edit the post AND publish it to every follower?")) return;
  setStatus("Editing & publishing…", true);
  try {
    await postAction("edit", true, "Published ✓");
  } catch (e) {
    setStatus("Edit error: " + e, false);
  }
});

// --- delete post -------------------------------------------------------
$("deleteBtn").addEventListener("click", async () => {
  if (!postThisPeriod) return;
  const msg = crossposted
    ? "Delete the PUBLISHED Trials post? This removes it from the channel and propagates the deletion to every follower (beacon mirrors the removal too). Your form data stays — Create re-posts it."
    : "Delete the in-channel draft post? Your form data stays — Create re-creates it.";
  if (!confirm(msg)) return;
  setStatus("Deleting…", true);
  try {
    const res = await api("/trials/delete", {});
    const data = await res.json();
    if (!res.ok || !data.ok) {
      return setStatus("Delete failed" + (data.error ? ": " + data.error : "."), false);
    }
    postThisPeriod = false;
    crossposted = false;
    updateButtons();
    setStatus("Post deleted — reset to draft.", true);
  } catch (e) {
    setStatus("Delete error: " + e, false);
  }
});

// --- autopost toggle ---------------------------------------------------
$("autopost").addEventListener("change", async () => {
  try {
    const res = await api("/trials/auto", { enabled: $("autopost").checked });
    const data = await res.json();
    $("autopost").checked = !!data.enabled;
    setStatus("Autopost " + (data.enabled ? "enabled" : "disabled") + ".", true);
  } catch (e) {
    setStatus("Autopost toggle error: " + e, false);
  }
});

// Initial render.
renderPreview();
