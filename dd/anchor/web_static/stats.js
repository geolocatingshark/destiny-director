// Statistics dashboard client. Framework-free vanilla JS, served from /static/stats.js
// and loaded (deferred) after shared.js. Fetches GET /stats/data (JSON, DB-only) and
// renders the dashboard. Time series arrive at daily granularity; weekly/monthly
// re-bucketing and the inline-SVG charts are layered in by a later chunk. For now this
// renders the leaderboard / current-totals / populations / server tables so the page is
// fully functional.
//
// The payload shape (see dd/anchor/extensions/stats_page.py::_collect_data):
//   commands:     [[name, "YYYY-MM-DD", count], ...]
//   autoposts:    [["YYYY-MM-DD", feed, kind, count], ...]   kind: "follow" | "mirror"
//   current:      [{feed, follows, mirrors}, ...]
//   populations:  [[id, population], ...]                    id is a string (snowflake)

const _byId = (id) => document.getElementById(id);

// Small DOM helper: a <tr> from an array of cell specs. A spec is a string/number (plain
// cell) or {text, num:true} for a right-aligned numeric cell.
function _row(cells) {
  const tr = document.createElement("tr");
  for (const spec of cells) {
    const td = document.createElement("td");
    if (spec && typeof spec === "object") {
      td.textContent = spec.text;
      if (spec.num) td.className = "num";
    } else {
      td.textContent = spec;
    }
    tr.appendChild(td);
  }
  return tr;
}

function _fillTable(tableId, rows) {
  const tbody = _byId(tableId).querySelector("tbody");
  tbody.replaceChildren(...rows.map(_row));
}

const _fmt = (n) => Number(n).toLocaleString();
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Fetched payload + current time-resolution, shared by the chart renderers so the
// resolution toggle can re-render without re-fetching.
const STATE = { data: null, resolution: "daily" };

// --- section renderers ------------------------------------------------------

function renderCommands(commands) {
  // Leaderboard: sum daily counts per command, descending.
  const totals = new Map();
  for (const [name, , count] of commands) {
    totals.set(name, (totals.get(name) || 0) + count);
  }
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  _fillTable(
    "commandsTable",
    ranked.map(([name, total]) => [name, { text: _fmt(total), num: true }]),
  );
  _byId("section-commands").classList.remove("hidden");
}

function renderAutoposts(current) {
  _fillTable(
    "currentTable",
    current.map((c) => [
      c.feed,
      { text: _fmt(c.follows), num: true },
      { text: _fmt(c.mirrors), num: true },
    ]),
  );
  _byId("section-autoposts").classList.remove("hidden");
}

function renderPopulations(populations) {
  const pops = populations.map(([, pop]) => pop);
  const total = pops.reduce((a, b) => a + b, 0);
  const count = pops.length;
  const summary = _byId("populationsSummary");
  summary.replaceChildren(
    ..._stats([
      ["Servers", count],
      ["Total population", total],
    ]),
  );
  _byId("section-populations").classList.remove("hidden");
}

function _stats(pairs) {
  return pairs.map(([label, value]) => {
    const wrap = document.createElement("div");
    wrap.className = "stat";
    const v = document.createElement("span");
    v.className = "value";
    v.textContent = _fmt(value);
    const l = document.createElement("span");
    l.className = "label";
    l.textContent = label;
    wrap.append(v, l);
    return wrap;
  });
}

function renderServers(populations) {
  // Keep the full list around so the search box can filter without re-fetching.
  const all = populations
    .map(([id, pop]) => ({ id: String(id), pop }))
    .sort((a, b) => b.pop - a.pop);

  const draw = (rows) =>
    _fillTable(
      "serversTable",
      rows.map((r) => [r.id, { text: _fmt(r.pop), num: true }]),
    );

  draw(all);
  const search = _byId("serverSearch");
  search.addEventListener("input", () => {
    const q = search.value.trim();
    draw(q ? all.filter((r) => r.id.includes(q)) : all);
  });
  _byId("section-servers").classList.remove("hidden");
}

// --- time-series charts -----------------------------------------------------

// Collapse the per-command daily rows into one total-invocations-per-day series.
function commandDailyTotals(commands) {
  const byDay = new Map();
  for (const [, iso, count] of commands) byDay.set(iso, (byDay.get(iso) || 0) + count);
  return [...byDay.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([iso, v]) => [new Date(iso + "T00:00:00Z"), v]);
}

function renderCommandsChart() {
  const points = DDCharts.bucketByResolution(
    commandDailyTotals(STATE.data.commands || []),
    STATE.resolution,
    "sum", // command usage is a FLOW — periods add up
  );
  DDCharts.lineChart(_byId("commandsChart"), {
    resolution: STATE.resolution,
    // Single series → on-brand accent, no legend (the section title names it).
    series: [{ name: "Commands", color: cssVar("--accent"), points }],
  });
}

// Split the autopost snapshot rows into per-day follow/mirror totals (summed across all
// feeds), as two [Date, count] series sharing the same dates.
function autopostDailyByKind(autoposts) {
  const byDay = new Map();
  for (const [iso, , kind, count] of autoposts) {
    const g = byDay.get(iso) || { follow: 0, mirror: 0 };
    g[kind] = (g[kind] || 0) + count;
    byDay.set(iso, g);
  }
  const dates = [...byDay.keys()].sort();
  const at = (k) => dates.map((iso) => [new Date(iso + "T00:00:00Z"), byDay.get(iso)[k]]);
  return { follow: at("follow"), mirror: at("mirror") };
}

function renderAutopostsChart() {
  const { follow, mirror } = autopostDailyByKind(STATE.data.autoposts || []);
  const res = STATE.resolution;
  // Reach is a STOCK (active-channel count), so aggregate by the period's last snapshot.
  DDCharts.lineChart(_byId("autopostsChart"), {
    resolution: res,
    series: [
      { name: "Followers", color: cssVar("--accent"), points: DDCharts.bucketByResolution(follow, res, "last") },
      { name: "Mirrors", color: cssVar("--accent-strong"), points: DDCharts.bucketByResolution(mirror, res, "last") },
    ],
  });
}

// Re-render every time-series chart at the current resolution. (Populations is a
// distribution, not a time series, so it is rendered once at load — not here.)
function renderTimeCharts() {
  if (!STATE.data) return;
  renderCommandsChart();
  renderAutopostsChart();
}

// Server population distribution: count servers per [10^k, 10^(k+1)) band (mirrors the
// old /stats populations log breakdown), rendered as a column chart.
function populationLogBands(populations) {
  const counts = new Map();
  for (const [, pop] of populations) {
    if (pop > 0) {
      const k = Math.floor(Math.log10(pop));
      counts.set(k, (counts.get(k) || 0) + 1);
    }
  }
  const ks = [...counts.keys()];
  if (!ks.length) return [];
  const lo = Math.min(...ks), hi = Math.max(...ks);
  const compact = (n) => (n >= 1e6 ? n / 1e6 + "M" : n >= 1e3 ? n / 1e3 + "K" : String(n));
  const bands = [];
  for (let k = lo; k <= hi; k++) {
    bands.push({ label: `${compact(10 ** k)}–${compact(10 ** (k + 1))}`, value: counts.get(k) || 0 });
  }
  return bands;
}

function renderPopulationsChart() {
  DDCharts.barChart(_byId("populationsChart"), {
    bars: populationLogBands(STATE.data.populations || []),
    color: cssVar("--accent-strong"),
    unit: "servers",
  });
}

function initToolbar() {
  const tb = _byId("toolbar");
  tb.classList.remove("hidden");
  tb.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.resolution = btn.dataset.res;
      tb.querySelectorAll(".seg-btn").forEach((b) =>
        b.classList.toggle("active", b === btn),
      );
      renderTimeCharts();
    });
  });
}

// --- boot -------------------------------------------------------------------

async function load() {
  try {
    const res = await fetch("/stats/data", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STATE.data = data;

    renderCommands(data.commands || []);
    renderAutoposts(data.current || []);
    renderPopulations(data.populations || []);
    renderServers(data.populations || []);

    initToolbar();
    renderTimeCharts();
    renderPopulationsChart(); // distribution — resolution-independent, render once

    _byId("loading").classList.add("hidden");
  } catch (e) {
    const err = _byId("error");
    err.textContent = "Failed to load statistics: " + e.message;
    err.classList.remove("hidden");
    _byId("loading").classList.add("hidden");
  }
}

document.addEventListener("DOMContentLoaded", load);

// Charts size to their container width, so re-render (debounced) on resize.
let _resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(renderTimeCharts, 150);
});
