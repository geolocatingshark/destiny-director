// Rotation editor application script. A self-contained, dependency-free form for editing
// a rotation's JSON document, served statically from /static/editor.js (no build step).
// The page (editor.html) is served by dd.anchor.extensions.rotation_editor, which
// substitutes {type, data, vocab} into a small inline <script> as window.__BOOTSTRAP__
// before this script runs. This script reads that global, edits the document client-side
// and POSTs it (via the shared api() helper) to /rotation/preview and /rotation/edit —
// auth is the rotation_session cookie (sent automatically on the same-origin fetch), so
// no token is embedded here. The server re-validates against the JSON schema, so this
// form is a convenience, not a trust boundary.
//
// Each post type has its own bespoke form (tagged .ls-only / .xur-only in the markup);
// on load the other type's nodes are removed and the matching type's block builds its
// fields and assigns collect(). lost_sector is tabbed (Sectors / Planet cycles /
// Preview); xur_location is a simpler Locations / Preview.

const BOOTSTRAP = window.__BOOTSTRAP__;
const { type, data, vocab } = BOOTSTRAP;
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of [].concat(kids)) n.append(k);
  return n;
};

document.getElementById("typeName").textContent = type;
document.getElementById("authNote").textContent =
  "Signed in via Discord (about 30 days). Changes save straight to the database.";

// Each post type has its own bespoke form; drop the other type's markup so the
// tab bar and collect() only ever see this type's fields.
document
  .querySelectorAll(type === "lost_sector" ? ".xur-only" : ".ls-only")
  .forEach((n) => n.remove());

// collect() is assigned by the active type's block below; the shared preview /
// save plumbing calls it without caring which post type produced the document.
let collect;
// consistencyProblems() returns a (possibly empty) list of reasons the document
// isn't internally consistent; the Save handler blocks while it's non-empty. The
// lost_sector block sets it; other types leave the no-op default.
let consistencyProblems = () => [];

// ===== lost_sector form ================================================
if (type === "lost_sector") {
  $("referenceDate").value = data.reference_date || "";

  // --- checkbox group helper -------------------------------------------
  function checkGroup(options, selected) {
    const wrap = el("div", { className: "checks" });
    const boxes = [];
    for (const opt of options) {
      const cb = el("input", { type: "checkbox", value: opt });
      cb.checked = (selected || []).includes(opt);
      boxes.push(cb);
      wrap.append(el("label", {}, [cb, " " + opt]));
    }
    wrap._value = () => boxes.filter((b) => b.checked).map((b) => b.value);
    return wrap;
  }

  // --- sectors ---------------------------------------------------------
  const sectorsBox = $("sectors");
  const sectorRows = [];

  function difficultyFields(title, diff) {
    const champs = checkGroup(vocab.champions, diff.champions);
    const shields = checkGroup(vocab.shields, diff.shields);
    const box = el("fieldset", {}, [
      el("legend", { textContent: title }),
      el("div", { className: "field" }, [el("label", { textContent: "Champions" }), champs]),
      el("div", { className: "field" }, [el("label", { textContent: "Shields" }), shields]),
    ]);
    box._value = () => ({ champions: champs._value(), shields: shields._value() });
    return box;
  }

  function addSector(s = {}) {
    const name = el("input", { type: "text", value: s.name || "", placeholder: "Sector name" });
    let prevName = name.value.trim();
    name.addEventListener("input", () => { refreshSectorNames(); validateConsistency(); });
    // On commit, propagate a rename to every schedule day that referenced the old
    // name, so the Sectors and Planet-cycles tabs never drift apart.
    name.addEventListener("change", () => {
      const oldName = prevName;
      const newName = name.value.trim();
      if (oldName && newName && oldName !== newName) propagateRename(oldName, newName);
      prevName = newName;
      refreshSectorNames();
      validateConsistency();
    });
    const gfx = el("input", { type: "url", value: s.shortlink_gfx || "", placeholder: "https://…" });
    const expert = difficultyFields("Expert", s.expert || {});
    const master = difficultyFields("Master", s.master || {});
    const remove = el("button", { className: "tiny danger", type: "button", textContent: "✕" });

    const card = el("div", { className: "card" }, [
      el("div", { className: "card-head" }, [el("span", { className: "grow" }), remove]),
      el("div", { className: "field" }, [el("label", { textContent: "Name" }), name]),
      el("div", { className: "field" }, [el("label", { textContent: "Graphic link" }), gfx]),
      el("div", { className: "diff-cols" }, [el("div", {}, [expert]), el("div", {}, [master])]),
    ]);
    const entry = {
      card,
      nameInput: name,
      value: () => ({
        name: name.value.trim(),
        shortlink_gfx: gfx.value.trim(),
        expert: expert._value(),
        master: master._value(),
      }),
    };
    remove.addEventListener("click", () => {
      const goneName = name.value.trim();
      card.remove();
      sectorRows.splice(sectorRows.indexOf(entry), 1);
      // Auto-remove this sector's schedule days — unless another sector still
      // holds the same name (the duplicate case).
      if (goneName && !sectorRows.some((r) => r.value().name === goneName)) {
        for (const zone of vocab.zones)
          for (const row of [...zoneRows[zone].children])
            if (row._value() === goneName) row.remove();
      }
      refreshSectorNames();
      validateConsistency();
    });
    sectorRows.push(entry);
    sectorsBox.append(card);
  }

  function refreshSectorNames() {
    const dl = $("sectorNames");
    dl.replaceChildren(
      ...sectorRows.map((r) => el("option", { value: r.value().name })).filter((o) => o.value),
    );
  }

  // --- consistency between the Sectors and Planet-cycles tabs ----------
  function nameCounts() {
    const counts = {};
    for (const r of sectorRows) {
      const n = r.value().name;
      counts[n] = (counts[n] || 0) + 1;
    }
    return counts;
  }

  function propagateRename(oldName, newName) {
    for (const zone of vocab.zones)
      for (const row of zoneRows[zone].children)
        if (row._value() === oldName) row._input.value = newName;
  }

  // Highlight blank/duplicate sector names and schedule days that match no
  // sector; return the problems (empty means the document is internally consistent).
  function validateConsistency() {
    const counts = nameCounts();
    const names = new Set(sectorRows.map((r) => r.value().name).filter(Boolean));
    const problems = new Set();
    for (const r of sectorRows) {
      const n = r.nameInput.value.trim();
      const bad = !n || counts[n] > 1;
      r.nameInput.classList.toggle("invalid", bad);
      if (!n) problems.add("a sector has a blank name");
      else if (counts[n] > 1) problems.add(`duplicate sector name "${n}"`);
    }
    for (const zone of vocab.zones)
      for (const row of zoneRows[zone].children) {
        const v = row._value();
        const bad = !!v && !names.has(v);
        row._input.classList.toggle("invalid", bad);
        if (bad) problems.add(`"${v}" in ${zone} isn't a sector`);
      }
    return [...problems];
  }

  (data.sectors || []).forEach(addSector);
  if (!sectorRows.length) addSector();
  refreshSectorNames();
  $("addSector").addEventListener("click", () => { addSector(); refreshSectorNames(); validateConsistency(); });

  // --- schedule (per zone, ordered list of sector names) ---------------
  const scheduleBox = $("schedule");
  const zoneRows = {};
  function addScheduleEntry(list, value) {
    const input = el("input", { type: "text", value: value || "", className: "grow" });
    input.setAttribute("list", "sectorNames");
    input.addEventListener("input", validateConsistency);
    const up = el("button", { className: "tiny secondary", type: "button", textContent: "↑" });
    const down = el("button", { className: "tiny secondary", type: "button", textContent: "↓" });
    const del = el("button", { className: "tiny danger", type: "button", textContent: "✕" });
    const row = el("div", { className: "row" }, [input, up, down, del]);
    row._value = () => input.value.trim();
    row._input = input;
    del.addEventListener("click", () => { row.remove(); validateConsistency(); });
    up.addEventListener("click", () => row.previousElementSibling && row.parentNode.insertBefore(row, row.previousElementSibling));
    down.addEventListener("click", () => row.nextElementSibling && row.parentNode.insertBefore(row.nextElementSibling, row));
    list.append(row);
  }
  for (const zone of vocab.zones) {
    const list = el("div");
    const add = el("button", { className: "tiny secondary", type: "button", textContent: "+ Day" });
    add.addEventListener("click", () => { addScheduleEntry(list); validateConsistency(); });
    const fs = el("fieldset", { className: "zone" }, [el("legend", { textContent: zone }), list, add]);
    scheduleBox.append(fs);
    zoneRows[zone] = list;
    ((data.schedule || {})[zone] || []).forEach((n) => addScheduleEntry(list, n));
  }

  // Wire the shared Save gate + paint the initial highlight now the schedule exists.
  consistencyProblems = validateConsistency;
  validateConsistency();

  collect = () => {
    const schedule = {};
    for (const zone of vocab.zones)
      schedule[zone] = [...zoneRows[zone].children].map((r) => r._value()).filter(Boolean);
    return {
      version: data.version || 1,
      reference_date: $("referenceDate").value,
      schedule,
      sectors: sectorRows.map((r) => r.value()),
    };
  };
}

// ===== xur_location form ===============================================
if (type === "xur_location") {
  const locationsBox = $("locations");
  const locationRows = [];

  function addLocation(loc = {}) {
    const api = el("input", { type: "text", value: loc.api_location_name || "", placeholder: "API location name (from Bungie)" });
    const friendly = el("input", { type: "text", value: loc.friendly_location_name || "", placeholder: "Friendly name (shown in the post)" });
    const link = el("input", { type: "url", value: loc.link || "", placeholder: "https://… (optional)" });
    const remove = el("button", { className: "tiny danger", type: "button", textContent: "✕" });

    const card = el("div", { className: "card" }, [
      el("div", { className: "card-head" }, [el("span", { className: "grow" }), remove]),
      el("div", { className: "field" }, [el("label", { textContent: "API location name" }), api]),
      el("div", { className: "field" }, [el("label", { textContent: "Friendly name" }), friendly]),
      el("div", { className: "field" }, [el("label", { textContent: "Link" }), link]),
    ]);
    const entry = {
      card,
      value: () => ({
        api_location_name: api.value.trim(),
        friendly_location_name: friendly.value.trim(),
        link: link.value.trim(),
      }),
    };
    remove.addEventListener("click", () => {
      card.remove();
      locationRows.splice(locationRows.indexOf(entry), 1);
    });
    locationRows.push(entry);
    locationsBox.append(card);
  }

  (data.locations || []).forEach(addLocation);
  if (!locationRows.length) addLocation();
  $("addLocation").addEventListener("click", () => addLocation());

  collect = () => ({
    version: data.version || 1,
    locations: locationRows
      .map((r) => r.value())
      .filter((r) => r.api_location_name)
      .map((r) => {
        const out = { api_location_name: r.api_location_name };
        if (r.friendly_location_name) out.friendly_location_name = r.friendly_location_name;
        if (r.link) out.link = r.link;
        return out;
      }),
  });
}

// --- submit helpers ----------------------------------------------------
function setStatus(msg, ok) {
  const s = $("status");
  s.textContent = msg;
  s.className = ok ? "ok" : "err";
}

// Delegates to the shared api() wrapper (shared.js); auth is the rotation_session cookie
// (same-origin fetch sends it). Same request shape as before the html/js split.
async function post(path) {
  return api(path, { type, data: collect() });
}

// --- tabs --------------------------------------------------------------
const tabBar = $("tabBar");
function showTab(name) {
  for (const b of tabBar.querySelectorAll("button"))
    b.classList.toggle("active", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".panel"))
    p.classList.toggle("active", p.dataset.panel === name);
  if (name === "preview") runPreview();
}
tabBar.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (btn) showTab(btn.dataset.tab);
});

// --- preview -----------------------------------------------------------
async function runPreview() {
  const box = $("previewBox");
  setStatus("Rendering preview…", true);
  box.textContent = "Rendering…";
  try {
    const res = await post("/rotation/preview");
    const body = await res.text();
    if (!res.ok) {
      box.textContent = "Preview failed:\n" + body;
      return setStatus("Preview failed — see the Preview tab.", false);
    }
    box.innerHTML = body;
    setStatus("Preview updated.", true);
  } catch (e) {
    box.textContent = "Preview error: " + e;
    setStatus("Preview error.", false);
  }
}
$("refreshPreview").addEventListener("click", runPreview);

// --- save --------------------------------------------------------------
$("saveBtn").addEventListener("click", async () => {
  const problems = consistencyProblems();
  if (problems.length) {
    return setStatus("Fix before saving: " + problems.slice(0, 4).join("; "), false);
  }
  setStatus("Saving…", true);
  try {
    const res = await post("/rotation/edit");
    const body = await res.text();
    if (!res.ok) return setStatus("Save failed: " + body, false);
    setStatus("Saved ✓ — changes are live. Keep editing or close the tab.", true);
  } catch (e) { setStatus("Save error: " + e, false); }
});

// Activate the initial tab. lost_sector keeps its static default (Sectors);
// xur_location's default tab lived in the removed markup, so activate it here.
if (type === "xur_location") showTab("locations");
