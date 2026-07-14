// Weekly Reset Overview form script. A self-contained form for editing the weekly_reset
// draft, served statically from /static/weekly_reset_form.js (no build step). The page
// (weekly_reset_form.html) is served by dd.anchor.extensions.weekly_reset, which
// substitutes {draft, options, autopost_enabled, conquest_tiers, reward_fields,
// post_this_period, crossposted} into a small inline <script> as window.__BOOTSTRAP__
// before this script runs. This script reads that global, edits the draft client-side and
// POSTs it (via the shared api() helper) to /weekly_reset/{preview,create,edit,delete,auto}
// — auth is the weekly_reset_session cookie (sent automatically on the same-origin fetch),
// so no token is embedded here. The server
// re-resolves weapons, re-applies the business rules and re-validates, so this form is a
// convenience, not a trust boundary.
//
// Widgets: Tom Select (vendored, window.TomSelect) backs the searchable pickers — the four
// weapon slots (option value = manifest hash, label = "name — type · rarity"), the GM
// strike + Crucible featured modes, the raid/dungeon/pantheon selects and the multi-select
// Conquest tiers. readForm() reads them via getValue(); the submitted payload shape is
// unchanged from the native-input version so the server's _context_from_payload contract
// holds.

const BOOT = window.__BOOTSTRAP__;
const { draft, options, conquest_tiers } = BOOT;
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
  "Signed in via Discord (about 30 days). Create/Edit write straight to the live post.";

// Tom Select instances, keyed by element id (plus "conq_<tier>"), so readForm() can pull
// their values with getValue().
const TS = {};

// Any edit re-syncs the Iron-Banner⇒Trials gate and re-renders the debounced preview.
// Native inputs bubble "input"; Tom Select fires this via each instance's onChange.
function onEdit() {
  syncTrials();
  schedulePreview();
}

// item hash (string) -> item record, for hydrating a weapon slot from its saved hash and
// for rendering the "name — type · rarity" label.
const itemByHash = new Map(options.items.map((i) => [String(i.hash), i]));

// The full weapon pool as Tom Select options (~4166 rows). Value is the manifest hash (as
// a string) so the slot submits the hash for the light.gg deep link; searching spans the
// name/type/rarity so typeahead finds a weapon by any of them.
const weaponOptions = options.items.map((i) => ({
  value: String(i.hash),
  name: i.name,
  type: i.type,
  rarity: i.rarity,
  label: `${i.name} — ${i.type} · ${i.rarity}`,
}));

// --- Tom Select builders -----------------------------------------------
// A single-select typeahead over a weapon pool. Hydrates from the saved WeaponRef: by hash
// when we have one (and it's in the pool), else by injecting the plain name as a one-off
// option so a carried-over unlinked name survives and re-submits as raw text (the server
// resolves either a hash or a name via resolve_reward_value).
function tsWeapon(id, weaponRef) {
  const ts = new TomSelect($(id), {
    options: weaponOptions,
    valueField: "value",
    labelField: "label",
    searchField: ["name", "type", "rarity"],
    maxOptions: 50,
    placeholder: "Search weapons…",
    plugins: ["clear_button"],
    onChange: onEdit,
    render: {
      option: (d, esc) => `<div>${esc(d.label || d.value)}</div>`,
      item: (d, esc) => `<div>${esc(d.label || d.value)}</div>`,
    },
  });
  if (weaponRef) {
    const hash = weaponRef.hash != null ? String(weaponRef.hash) : "";
    if (hash && itemByHash.has(hash)) {
      ts.setValue(hash, true);
    } else if (weaponRef.name) {
      ts.addOption({ value: weaponRef.name, label: weaponRef.name, name: weaponRef.name });
      ts.setValue(weaponRef.name, true);
    }
  }
  TS[id] = ts;
  return ts;
}

// A single-select typeahead over a bounded string pool; the option value IS the name, so
// the slot submits the plain name. A carried-over current value not in the pool is injected
// up front so it isn't silently dropped.
function tsSingle(id, values, current) {
  const cur = (current || "").trim();
  const pool = cur && !values.includes(cur) ? [cur, ...values] : values;
  const ts = new TomSelect($(id), {
    options: pool.map((v) => ({ value: v, text: v })),
    maxOptions: 500,
    placeholder: "Search…",
    plugins: ["clear_button"],
    onChange: onEdit,
  });
  ts.setValue(cur, true);
  TS[id] = ts;
  return ts;
}

// --- populate from the draft -------------------------------------------
const R = draft.rotator_raids || ["", ""];
const D = draft.rotator_dungeons || ["", ""];

$("resetAt").value = draft.reset_ts
  ? new Date(draft.reset_ts * 1000).toISOString().slice(0, 16)
  : "";
$("ironBanner").checked = !!draft.iron_banner;
$("trialsActive").checked = !!draft.trials_active;
$("updateLabel").value = (draft.update_link && draft.update_link.label) || "";
$("updateUrl").value = (draft.update_link && draft.update_link.url) || "";
$("eventsNarrative").value = draft.events_narrative || "";
$("crucible1v6").value = draft.crucible_1v6 || "";
$("imageUrl").value = draft.image_url || "";
// Pre-check "use as default" when this week's image already is the saved default.
$("imageDefault").checked =
  !!BOOT.default_image_url && (draft.image_url || "") === BOOT.default_image_url;
$("notesText").value = (draft.notes || []).join("\n");
$("linksText").value = (draft.extra_links || [])
  .map((l) => `${l.label} | ${l.url}`)
  .join("\n");
$("autopost").checked = !!BOOT.autopost_enabled;

// The featured (second) mode out of a "First, Second" crucible value.
function featured(value) {
  const parts = (value || "").split(", ");
  return parts.length > 1 ? parts.slice(1).join(", ") : "";
}

// Weapon pickers.
tsWeapon("gmWeapon", draft.gm_weapon);
tsWeapon("quickplayWeapon", draft.quickplay_weapon);
tsWeapon("controlWeapon", draft.control_weapon);
tsWeapon("zavalaWeapon", draft.zavala_weapon);

// Bounded-pool single-selects.
tsSingle("gmStrike", options.strikes, draft.gm_strike);
tsSingle("seasonalRaid", options.raids, draft.seasonal_raid);
tsSingle("seasonalDungeon", options.dungeons, draft.seasonal_dungeon);
tsSingle("rotRaid1", options.raids, R[0]);
tsSingle("rotRaid2", options.raids, R[1]);
tsSingle("rotDun1", options.dungeons, D[0]);
tsSingle("rotDun2", options.dungeons, D[1]);
tsSingle("pantheonReprise", options.pantheon, draft.pantheon_reprise);
tsSingle("pantheonEncore", options.pantheon, draft.pantheon_encore);
tsSingle("crucible3v3", options.crucible_modes, featured(draft.crucible_3v3));
tsSingle("crucible6v6", options.crucible_modes, featured(draft.crucible_6v6));

// --- conquests: a Tom Select multi per tier ----------------------------
for (const tier of conquest_tiers) {
  const chosen = (draft.conquests || {})[tier] || [];
  // Union of the manifest pool + anything already in the draft, so a carried-over pick that
  // isn't in the current pool isn't silently dropped.
  const pool = [...new Set([...(options.conquests[tier] || []), ...chosen])].sort();
  const sel = el("select", { id: "conq_" + tier, multiple: true });
  $("conquests").append(
    el("div", { className: "field" }, [
      el("label", { htmlFor: "conq_" + tier, textContent: tier }),
      sel,
    ]),
  );
  const ts = new TomSelect(sel, {
    options: pool.map((v) => ({ value: v, text: v })),
    plugins: ["remove_button"],
    maxOptions: 500,
    hideSelected: true,
    placeholder: "Add featured activities…",
    onChange: onEdit,
  });
  ts.setValue(chosen, true);
  TS["conq_" + tier] = ts;
}

// --- Iron Banner => Trials off, reflected live in the UI ---------------
function syncTrials() {
  const ib = $("ironBanner").checked;
  if (ib) $("trialsActive").checked = false;
  $("trialsActive").disabled = ib;
}
syncTrials();

// --- read the form into the payload the server expects -----------------
// getValue() returns the option value: a hash string (weapons) or plain name (everything
// else) for single-selects, and an array of names for the conquest multi-selects — the same
// shapes the native-input form submitted, so _context_from_payload is unaffected.
function readForm() {
  const conquests = {};
  for (const tier of conquest_tiers) conquests[tier] = TS["conq_" + tier].getValue();
  const at = $("resetAt").value;
  return {
    reset_ts: at ? Math.floor(Date.parse(at + "Z") / 1000) : draft.reset_ts,
    gm_strike: TS.gmStrike.getValue().trim(),
    gm_weapon: TS.gmWeapon.getValue(),
    quickplay_weapon: TS.quickplayWeapon.getValue(),
    control_weapon: TS.controlWeapon.getValue(),
    zavala_weapon: TS.zavalaWeapon.getValue(),
    seasonal_raid: TS.seasonalRaid.getValue().trim(),
    seasonal_dungeon: TS.seasonalDungeon.getValue().trim(),
    rotator_raids: [TS.rotRaid1.getValue(), TS.rotRaid2.getValue()],
    rotator_dungeons: [TS.rotDun1.getValue(), TS.rotDun2.getValue()],
    pantheon_reprise: TS.pantheonReprise.getValue(),
    pantheon_encore: TS.pantheonEncore.getValue(),
    crucible_1v6: $("crucible1v6").value.trim(),
    crucible_3v3: TS.crucible3v3.getValue(),
    crucible_6v6: TS.crucible6v6.getValue(),
    conquests,
    iron_banner: $("ironBanner").checked,
    trials_active: $("trialsActive").checked,
    update_label: $("updateLabel").value.trim(),
    update_url: $("updateUrl").value.trim(),
    image_url: $("imageUrl").value.trim(),
    set_default_image: $("imageDefault").checked,
    events_narrative: $("eventsNarrative").value.trim(),
    notes_text: $("notesText").value,
    links_text: $("linksText").value,
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
async function renderPreview() {
  try {
    const res = await api("/weekly_reset/preview", readForm());
    const body = await res.text();
    // On ok, the server returns SAFE HTML (render_post_html: escaped leaves, whitelisted
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

// Native inputs bubble "input"; Tom Select edits arrive via each instance's onChange.
$("form").addEventListener("submit", (e) => e.preventDefault());
$("form").addEventListener("input", onEdit);
$("refreshBtn").addEventListener("click", renderPreview);

// --- action-button visibility ------------------------------------------
// `postThisPeriod` = a post exists for the CURRENT reset week; `crossposted` = it's been
// published to followers. Both seed from the GET bootstrap and update after every
// create/edit/delete. The two Create buttons show only when there's no post this week;
// once one exists they hide and Edit/Delete take over. "Edit & publish" is the way to
// publish a post that was created unpublished, so it hides once crossposted.
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
  const res = await api("/weekly_reset/" + path, { ...readForm(), publish });
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
    ? "Delete the PUBLISHED weekly-reset post? This removes it from the channel and propagates the deletion to every follower (beacon mirrors the removal too). Your form data stays — Create re-posts it."
    : "Delete the in-channel draft post? Your form data stays — Create re-creates it.";
  if (!confirm(msg)) return;
  setStatus("Deleting…", true);
  try {
    const res = await api("/weekly_reset/delete", {});
    const data = await res.json();
    if (!res.ok || !data.ok) {
      return setStatus("Delete failed" + (data.error ? ": " + data.error : "."), false);
    }
    postThisPeriod = false;
    crossposted = false;
    updateButtons();
    setStatus("Post deleted — create a new one when ready.", true);
  } catch (e) {
    setStatus("Delete error: " + e, false);
  }
});

// --- autopost toggle ---------------------------------------------------
$("autopost").addEventListener("change", async () => {
  try {
    const res = await api("/weekly_reset/auto", { enabled: $("autopost").checked });
    const data = await res.json();
    $("autopost").checked = !!data.enabled;
    setStatus("Autopost " + (data.enabled ? "enabled" : "disabled") + ".", true);
  } catch (e) {
    setStatus("Autopost toggle error: " + e, false);
  }
});

// Initial render.
renderPreview();
