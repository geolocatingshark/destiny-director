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

// --- boot -------------------------------------------------------------------

async function load() {
  try {
    const res = await fetch("/stats/data", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    renderCommands(data.commands || []);
    renderAutoposts(data.current || []);
    renderPopulations(data.populations || []);
    renderServers(data.populations || []);

    _byId("loading").classList.add("hidden");
  } catch (e) {
    const err = _byId("error");
    err.textContent = "Failed to load statistics: " + e.message;
    err.classList.remove("hidden");
    _byId("loading").classList.add("hidden");
  }
}

document.addEventListener("DOMContentLoaded", load);
