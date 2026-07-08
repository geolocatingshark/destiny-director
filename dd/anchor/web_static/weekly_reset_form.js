// Weekly Reset Overview form script. A self-contained, dependency-free form for editing
// the weekly_reset draft, served statically from /static/weekly_reset_form.js (no build
// step). The page (weekly_reset_form.html) is served by dd.anchor.extensions.weekly_reset,
// which substitutes {draft, options, autopost_enabled, conquest_tiers, reward_fields} into
// a small inline <script> as window.__BOOTSTRAP__ before this script runs. This script
// reads that global, edits the draft client-side and POSTs it (via the shared api()
// helper) to /weekly_reset/{preview,save,publish,auto} — auth is the weekly_reset_session
// cookie (sent automatically on the same-origin fetch), so no token is embedded here. The
// server re-resolves weapons, re-applies the business rules and re-validates, so this form
// is a convenience, not a trust boundary. Native inputs only (Tom Select comes later).

const BOOT = window.__BOOTSTRAP__;
const { draft, options, conquest_tiers } = BOOT;
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of [].concat(kids)) n.append(k);
  return n;
};

$("authNote").textContent =
  "Signed in via Discord for this editing session (about 2 hours). Save writes straight to the draft.";

// name(lower) -> item hash, so weapon slots submit the manifest hash (for the light.gg
// deep link) when the typed name matches a known item; otherwise the raw text is sent and
// the server keeps it as a plain (unlinked) name.
const itemByName = new Map(options.items.map((i) => [i.name.toLowerCase(), i.hash]));

// --- option population -------------------------------------------------
function fillDatalist(id, names) {
  $(id).replaceChildren(...names.map((n) => el("option", { value: n })));
}
fillDatalist("weaponList", options.items.map((i) => i.name));
fillDatalist("strikeList", options.strikes);
fillDatalist("raidList", options.raids);
fillDatalist("dungeonList", options.dungeons);

// A <select> with a blank first option; keeps the current value selectable even if it's
// not in the bounded domain (e.g. a hand-typed rotator carried over from an old draft).
function fillSelect(id, values, selected) {
  selected = selected || "";
  const pool = selected && !values.includes(selected) ? [...values, selected] : values;
  $(id).replaceChildren(
    el("option", { value: "", textContent: "—" }),
    ...pool.map((v) =>
      el("option", { value: v, textContent: v, selected: v === selected }),
    ),
  );
}

// The featured (second) mode out of a "First, Second" crucible value.
function featured(value) {
  const parts = (value || "").split(", ");
  return parts.length > 1 ? parts.slice(1).join(", ") : "";
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
$("gmStrike").value = draft.gm_strike || "";
$("gmWeapon").value = (draft.gm_weapon && draft.gm_weapon.name) || "";
$("quickplayWeapon").value = (draft.quickplay_weapon && draft.quickplay_weapon.name) || "";
$("controlWeapon").value = (draft.control_weapon && draft.control_weapon.name) || "";
$("seasonalRaid").value = draft.seasonal_raid || "";
$("seasonalDungeon").value = draft.seasonal_dungeon || "";
$("zavalaWeapon").value = (draft.zavala_weapon && draft.zavala_weapon.name) || "";
$("crucible1v6").value = draft.crucible_1v6 || "";
$("imageUrl").value = draft.image_url || "";
$("notesText").value = (draft.notes || []).join("\n");
$("linksText").value = (draft.extra_links || [])
  .map((l) => `${l.label} | ${l.url}`)
  .join("\n");
$("autopost").checked = !!BOOT.autopost_enabled;

fillSelect("rotRaid1", options.raids, R[0]);
fillSelect("rotRaid2", options.raids, R[1]);
fillSelect("rotDun1", options.dungeons, D[0]);
fillSelect("rotDun2", options.dungeons, D[1]);
fillSelect("pantheonReprise", options.pantheon, draft.pantheon_reprise);
fillSelect("pantheonEncore", options.pantheon, draft.pantheon_encore);
fillSelect("crucible3v3", options.crucible_modes, featured(draft.crucible_3v3));
fillSelect("crucible6v6", options.crucible_modes, featured(draft.crucible_6v6));

// --- conquests: a checkbox group per tier ------------------------------
const conquestBoxes = {}; // tier -> [checkbox]
for (const tier of conquest_tiers) {
  const chosen = (draft.conquests || {})[tier] || [];
  // Union of the manifest pool + anything already in the draft, so a carried-over pick
  // that isn't in the current pool isn't silently dropped.
  const pool = [...new Set([...(options.conquests[tier] || []), ...chosen])].sort();
  const boxes = [];
  const list = el("div", { className: "checks" });
  for (const name of pool) {
    const cb = el("input", { type: "checkbox", value: name });
    cb.checked = chosen.includes(name);
    boxes.push(cb);
    list.append(el("label", {}, [cb, " " + name]));
  }
  conquestBoxes[tier] = boxes;
  $("conquests").append(el("fieldset", {}, [el("legend", { textContent: tier }), list]));
}

// --- Iron Banner => Trials off, reflected live in the UI ---------------
function syncTrials() {
  const ib = $("ironBanner").checked;
  if (ib) $("trialsActive").checked = false;
  $("trialsActive").disabled = ib;
}
syncTrials();

// --- read the form into the payload the server expects -----------------
function weaponValue(id) {
  const v = $(id).value.trim();
  const hash = itemByName.get(v.toLowerCase());
  return hash ? String(hash) : v;
}

function readForm() {
  const conquests = {};
  for (const tier of conquest_tiers)
    conquests[tier] = conquestBoxes[tier].filter((b) => b.checked).map((b) => b.value);
  const at = $("resetAt").value;
  return {
    reset_ts: at ? Math.floor(Date.parse(at + "Z") / 1000) : draft.reset_ts,
    gm_strike: $("gmStrike").value.trim(),
    gm_weapon: weaponValue("gmWeapon"),
    quickplay_weapon: weaponValue("quickplayWeapon"),
    control_weapon: weaponValue("controlWeapon"),
    zavala_weapon: weaponValue("zavalaWeapon"),
    seasonal_raid: $("seasonalRaid").value.trim(),
    seasonal_dungeon: $("seasonalDungeon").value.trim(),
    rotator_raids: [$("rotRaid1").value, $("rotRaid2").value],
    rotator_dungeons: [$("rotDun1").value, $("rotDun2").value],
    pantheon_reprise: $("pantheonReprise").value,
    pantheon_encore: $("pantheonEncore").value,
    crucible_1v6: $("crucible1v6").value.trim(),
    crucible_3v3: $("crucible3v3").value,
    crucible_6v6: $("crucible6v6").value,
    conquests,
    iron_banner: $("ironBanner").checked,
    trials_active: $("trialsActive").checked,
    update_label: $("updateLabel").value.trim(),
    update_url: $("updateUrl").value.trim(),
    image_url: $("imageUrl").value.trim(),
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
    // Body is server-escaped plain text; innerHTML into the <pre> renders it literally.
    $("previewBox").innerHTML = res.ok ? body : "Preview failed:\n" + body;
  } catch (e) {
    $("previewBox").textContent = "Preview error: " + e;
  }
}

// Any edit re-syncs the Trials gate and re-renders the preview.
$("form").addEventListener("submit", (e) => e.preventDefault());
$("form").addEventListener("input", () => {
  syncTrials();
  schedulePreview();
});

// --- save --------------------------------------------------------------
async function save() {
  const res = await api("/weekly_reset/save", readForm());
  const data = await res.json();
  if (data.problems) {
    showProblems(data.problems);
    return false;
  }
  showProblems([]);
  return true;
}
$("saveBtn").addEventListener("click", async () => {
  setStatus("Saving…", true);
  try {
    const ok = await save();
    setStatus(ok ? "Saved ✓ — draft stored." : "Not saved — see problems above.", ok);
  } catch (e) {
    setStatus("Save error: " + e, false);
  }
});

// --- publish (save first, then publish the saved draft) ----------------
$("publishBtn").addEventListener("click", async () => {
  if (!confirm("Save and publish the current form to the followable?")) return;
  setStatus("Saving…", true);
  try {
    if (!(await save())) return setStatus("Not published — see problems above.", false);
    setStatus("Publishing…", true);
    const res = await api("/weekly_reset/publish", {});
    const data = await res.json();
    if (data.problems) {
      showProblems(data.problems);
      return setStatus("Not published — see problems above.", false);
    }
    setStatus(data.note || "Published ✓", true);
  } catch (e) {
    setStatus("Publish error: " + e, false);
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
