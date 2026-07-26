// Copyright © 2019-present gsfernandes81 — AGPL-3.0-or-later (see repo LICENSE).
//
// Mirror-log page client. Fetches GET /mirror-logs/data (recent runs + overview) and,
// per expanded run, GET /mirror-logs/data?src=<id> (that run's stats + version list),
// rendering the overview (KPIs, health bar, per-day chart via the shared DDCharts
// engine), the run list with per-run progress bars, and the expandable run detail
// (progress-card stats + the message render/diff). While any run is still in progress it
// re-polls every few seconds. No live Discord message is involved — a stateless ledger read.

"use strict";

(function () {
  const POLL_MS = 5000;

  const els = {
    loading: document.getElementById("loading"),
    error: document.getElementById("error"),
    empty: document.getElementById("empty"),
    noMatches: document.getElementById("noMatches"),
    filterBar: document.getElementById("filterBar"),
    srcFilter: document.getElementById("srcFilter"),
    table: document.getElementById("runsTable"),
    tbody: document.querySelector("#runsTable tbody"),
    windowDays: document.getElementById("windowDays"),
    overview: document.getElementById("overview"),
    overviewStats: document.getElementById("overviewStats"),
    overviewBar: document.getElementById("overviewBar"),
    overviewChart: document.getElementById("overviewChart"),
  };

  const cssVar = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const expanded = new Set(); // src_msg_ids whose detail panel is open
  let pollToken = 0; // bumped to cancel an in-flight poll chain
  let selectedSrc = ""; // "" = all; else a src_ch_id string

  const DISCORD = "https://discord.com/channels";

  async function fetchJSON(url) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  function esc(s) {
    return String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  }

  function statusOf(run) {
    if (run.pending > 0) return { cls: "progress", label: "In progress" };
    if (run.failed > 0 && run.delivered > 0)
      return { cls: "partial", label: "Partial" };
    if (run.failed > 0) return { cls: "failed", label: "Failed" };
    if (run.cancelled > 0 && run.delivered === 0)
      return { cls: "cancelled", label: "Cancelled" };
    return { cls: "clean", label: "Clean" };
  }

  function relTime(iso) {
    if (!iso) return "—";
    const then = new Date(iso).getTime();
    const secs = Math.max(0, (Date.now() - then) / 1000);
    if (secs < 45) return "just now";
    const mins = secs / 60;
    if (mins < 60) return `${Math.round(mins)}m ago`;
    const hrs = mins / 60;
    if (hrs < 24) return `${Math.round(hrs)}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  }

  function fmtDuration(startIso, endIso) {
    if (!startIso || !endIso) return "";
    const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
    if (ms < 0) return "";
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  function countsCell(run) {
    const parts = [
      `<span class="${run.delivered ? "ok" : "zero"}">${run.delivered}/${run.total}</span>`,
    ];
    if (run.failed) parts.push(`<span class="bad">✗${run.failed}</span>`);
    if (run.pending) parts.push(`<span class="pend">…${run.pending}</span>`);
    if (run.cancelled) parts.push(`<span class="zero">⊘${run.cancelled}</span>`);
    return `<span class="counts">${parts.join(" ")}</span>`;
  }

  function crosspostCell(run) {
    if (!run.crosspost_done && !run.crosspost_pending) return "—";
    let out = `<span class="ok">${run.crosspost_done} ✓</span>`;
    if (run.crosspost_pending)
      out += ` <span class="pend">+${run.crosspost_pending}…</span>`;
    return `<span class="counts">${out}</span>`;
  }

  function whenCell(run) {
    const dur = run.pending
      ? "running"
      : fmtDuration(run.started, run.last_at) || "";
    return `${esc(relTime(run.started))}${dur ? ` <span class="dur">· ${esc(dur)}</span>` : ""}`;
  }

  function fmtSecs(secs) {
    if (!isFinite(secs) || secs < 0) return "";
    const s = Math.round(secs);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  // A stacked bar of the run state (delivered green / failed red / cancelled grey),
  // proportional to total; the unfilled track is what's still pending. Mirrors the old
  // Discord progress card's bar. `big` renders the taller detail variant with a % label.
  function progressBar(run, big) {
    const total = run.total || 1;
    const pct = (n) => (100 * n) / total;
    const resolved = run.delivered + run.failed + run.cancelled;
    const resolvedPct = Math.round((resolved / total) * 100);
    const seg = (cls, n) =>
      n > 0 ? `<span class="pseg ${cls}" style="width:${pct(n)}%"></span>` : "";
    const bar =
      `<div class="pbar${big ? " big" : ""}" role="img" ` +
      `aria-label="${resolvedPct}% resolved (${run.delivered} delivered, ` +
      `${run.failed} failed, ${run.pending} pending)">` +
      seg("done", run.delivered) +
      seg("fail", run.failed) +
      seg("cancel", run.cancelled) +
      `</div>`;
    return big
      ? `<div class="pbar-row">${bar}<span class="pbar-pct">${resolvedPct}%</span></div>`
      : bar;
  }

  // Rate + ETA off resolved (excluding cancels) over elapsed time — the old card's
  // throughput line. ETA only while work remains; returns "" when not yet meaningful.
  function throughputLine(run) {
    const resolved = run.delivered + run.failed; // throughput_resolved
    const start = run.started ? new Date(run.started).getTime() : 0;
    const end = run.pending
      ? Date.now()
      : run.last_at
        ? new Date(run.last_at).getTime()
        : 0;
    const secs = (end - start) / 1000;
    if (!start || secs <= 0 || resolved === 0) return "";
    const rate = resolved / secs;
    const remaining = run.total - (run.delivered + run.failed + run.cancelled);
    let out = `${rate.toFixed(1)} ch/s`;
    if (remaining > 0) out += ` · ETA ~${fmtSecs(remaining / rate)}`;
    return out;
  }

  // The run stats block shown above the message render (the old progress-card content):
  // a big progress bar, a tile grid of the counts, timing/throughput, and — when there
  // are failures — the grouped error breakdown.
  function renderRunStats(run, detail) {
    const remaining = run.total - (run.delivered + run.failed + run.cancelled);
    const tile = (label, value, cls) =>
      `<div class="stat-tile"><div class="stat-val ${cls || ""}">${value}</div>` +
      `<div class="stat-label">${label}</div></div>`;
    const tiles = [
      tile("Delivered", run.delivered, run.delivered ? "ok" : ""),
      tile("Failed", run.failed, run.failed ? "bad" : ""),
      tile("Remaining", remaining, remaining ? "pend" : ""),
      tile("Cancelled", run.cancelled, run.cancelled ? "muted" : ""),
      tile("Crosspost", `${run.crosspost_done}${run.crosspost_pending ? `+${run.crosspost_pending}…` : ""}`),
      tile("Attempts", run.max_attempts),
      tile("Version", `v${run.version}`),
    ].join("");

    const dur = run.pending
      ? `${fmtSecs((Date.now() - new Date(run.started).getTime()) / 1000)} (running)`
      : fmtDuration(run.started, run.last_at) || "—";
    const tp = throughputLine(run);
    const meta = [
      `<span title="wall-clock time from first to last delivery">⏱ ${esc(dur)}</span>`,
      tp ? `<span title="resolved channels per second">⚡ ${esc(tp)}</span>` : "",
    ]
      .filter(Boolean)
      .join(" · ");

    const fails = detail.failures || [];
    let breakdown = "";
    if (fails.length) {
      const rows = fails
        .map(
          (f) =>
            `<li><code>${esc(f.ref || "—")}</code> ×${f.count}` +
            `${f.error_class ? ` <span class="muted">(${esc(f.error_class.toLowerCase())})</span>` : ""}` +
            `${f.sample ? `<div class="fail-sample">${esc(f.sample)}</div>` : ""}</li>`,
        )
        .join("");
      breakdown = `<div class="fail-breakdown"><div class="stat-heading">Failure breakdown</div><ul>${rows}</ul></div>`;
    }

    return (
      `<div class="run-stats">` +
      progressBar(run, true) +
      `<div class="stat-tiles">${tiles}</div>` +
      `<div class="stat-meta">${meta}</div>` +
      breakdown +
      `</div>`
    );
  }

  // The overview card: aggregate KPIs + a health bar over the shown runs, and a per-day
  // "channels delivered" bar chart (reusing the shared DDCharts engine, as /stats does).
  function sumRuns(runs) {
    const acc = {
      total: 0,
      delivered: 0,
      failed: 0,
      pending: 0,
      cancelled: 0,
      crosspost_done: 0,
    };
    for (const r of runs) {
      acc.total += r.total;
      acc.delivered += r.delivered;
      acc.failed += r.failed;
      acc.pending += r.pending;
      acc.cancelled += r.cancelled;
      acc.crosspost_done += r.crosspost_done;
    }
    return acc;
  }

  let lastShown = [];

  function renderOverview(runs) {
    lastShown = runs;
    if (!runs.length) {
      els.overview.classList.add("hidden");
      return;
    }
    const s = sumRuns(runs);
    const resolvedTP = s.delivered + s.failed;
    const successPct = resolvedTP ? Math.round((s.delivered / resolvedTP) * 100) : 100;
    const tile = (label, value, cls) =>
      `<div class="stat-tile"><div class="stat-val ${cls || ""}">${value}</div>` +
      `<div class="stat-label">${label}</div></div>`;
    els.overviewStats.innerHTML = [
      tile("Runs", runs.length),
      tile("Delivered", s.delivered, "ok"),
      tile("Failed", s.failed, s.failed ? "bad" : ""),
      tile("Success", successPct + "%", s.failed ? "" : "ok"),
      tile("Crossposts", s.crosspost_done),
    ].join("");
    els.overviewBar.innerHTML = progressBar(s, true);
    // Unhide before drawing so the chart reads a real container width (it sizes to
    // clientWidth; a display:none container measures 0).
    els.overview.classList.remove("hidden");
    renderOverviewChart(runs);
  }

  function renderOverviewChart(runs) {
    if (!window.DDCharts || !els.overviewChart) return;
    const byDay = new Map(); // day-epoch -> delivered count
    for (const r of runs) {
      if (!r.started) continue;
      const d = new Date(r.started);
      d.setHours(0, 0, 0, 0);
      byDay.set(d.getTime(), (byDay.get(d.getTime()) || 0) + r.delivered);
    }
    const bars = [...byDay.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([k, v]) => ({
        label: new Date(k).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }),
        value: v,
      }));
    window.DDCharts.barChart(els.overviewChart, {
      bars,
      color: cssVar("--accent"),
      unit: "",
    });
  }

  // A "Jump to source ↗" button for the mirrored message, when we know its source guild
  // (from the latest captured snapshot). Empty for sources predating the capture deploy.
  function sourceButton(run) {
    if (!run.src_guild_id) return "";
    const href = `${DISCORD}/${run.src_guild_id}/${run.src_ch_id}/${run.src_msg_id}`;
    return (
      `<a class="jump-source" href="${esc(href)}" target="_blank" rel="noopener">` +
      `Jump to source ↗</a>`
    );
  }

  // The expandable detail's message view: every captured version rendered as its own
  // column in a horizontally-scrollable row (no vertical scroll), oldest→newest, each
  // labelled by the operation it was (v1 = Create, later = Update). A "highlight changes
  // vs previous" toggle re-renders every v2+ column as an inline diff against the one
  // before it. Plus the jump-to-source button.
  function renderVersionColumns(data, run) {
    const vs = data.versions || [];
    const jump = sourceButton(run);
    if (!vs.length) {
      return (
        `<div class="versions"><div class="version-head">` +
        `<span class="version-label">Message</span>${jump}</div>` +
        `<p class="detail-loading">No version snapshots for this source yet — ` +
        `capture began at deploy, so older runs have none.</p></div>`
      );
    }
    const control =
      vs.length > 1
        ? `<label class="diff-toggle"><input type="checkbox" class="diff-check" /> ` +
          `Highlight changes vs previous</label>`
        : `<span class="version-hint">only version so far — edits are captured as ` +
          `new versions and shown as diffs</span>`;
    const cols = vs
      .map((v, i) => {
        const op = i === 0 ? "Create" : "Update";
        const opCls = i === 0 ? "create" : "update";
        const abs = v.captured_at ? new Date(v.captured_at).toLocaleString() : "";
        return (
          `<div class="vcol" data-idx="${i}">` +
          `<div class="vcol-head">` +
          `<span class="op-tag ${opCls}">${op}</span>` +
          `<span class="vcol-ver">v${esc(v.version)}</span>` +
          `<span class="vcol-time" title="${esc(abs)}">${esc(relTime(v.captured_at))}</span>` +
          `</div>` +
          `<div class="vcol-body"><p class="detail-loading">Loading…</p></div>` +
          `</div>`
        );
      })
      .join("");
    return (
      `<div class="versions">` +
      `<div class="version-head"><span class="version-label">Versions</span>` +
      control +
      jump +
      `</div>` +
      `<div class="vcols">${cols}</div>` +
      `</div>`
    );
  }

  // Fetch each version column's render (or its diff-vs-previous when the toggle is on).
  // The server returns pre-escaped safe HTML (cv2_render) → innerHTML; an error body is
  // untrusted → textContent. Each column carries its own token so a toggle mid-fetch
  // can't land a stale render.
  function setupVersionColumns(srcId, container, versions) {
    if (!versions.length) return;
    const cols = [...container.querySelectorAll(".vcol")];
    const diffCheck = container.querySelector(".diff-check");
    const tokens = new WeakMap();

    async function renderCol(col) {
      const idx = Number(col.dataset.idx);
      const v = versions[idx];
      const body = col.querySelector(".vcol-body");
      const diffOn = !!diffCheck && diffCheck.checked && idx > 0;
      let url = `/mirror-logs/render?src=${encodeURIComponent(srcId)}&v=${encodeURIComponent(v.version)}`;
      if (diffOn) url += `&diff=${encodeURIComponent(versions[idx - 1].version)}`;
      const token = (tokens.get(col) || 0) + 1;
      tokens.set(col, token);
      body.innerHTML = `<p class="detail-loading">Loading…</p>`;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        const html = await res.text();
        if (tokens.get(col) !== token) return; // superseded
        if (res.ok) body.innerHTML = html;
        else body.textContent = `Render failed: ${html}`;
      } catch (e) {
        if (tokens.get(col) === token) body.textContent = `Render error: ${e}`;
      }
    }

    cols.forEach(renderCol);
    if (diffCheck)
      diffCheck.addEventListener("change", () => cols.forEach(renderCol));
  }

  async function loadDetail(run, container) {
    container.innerHTML = `<p class="detail-loading">Loading message…</p>`;
    try {
      const data = await fetchJSON(
        `/mirror-logs/data?src=${encodeURIComponent(run.src_msg_id)}`,
      );
      container.innerHTML =
        renderRunStats(run, data) + renderVersionColumns(data, run);
      setupVersionColumns(run.src_msg_id, container, data.versions || []);
    } catch (e) {
      container.innerHTML = `<p class="detail-error">Failed to load detail: ${esc(e.message)}</p>`;
    }
  }

  // Source message column: the channel name links to the source *message*, and the
  // message summary links to the source *channel* (per request). Both need the source
  // guild id (from the latest snapshot); without it they fall back to plain text.
  function sourceCell(run) {
    const name = run.src_name ? `#${run.src_name}` : `#${run.src_ch_id}`;
    const g = run.src_guild_id;
    const msgHref = g
      ? `${DISCORD}/${g}/${run.src_ch_id}/${run.src_msg_id}`
      : null;
    const chHref = g ? `${DISCORD}/${g}/${run.src_ch_id}` : null;
    const channel = msgHref
      ? `<a href="${esc(msgHref)}" target="_blank" rel="noopener" ` +
        `title="Jump to source message">${esc(name)}</a>`
      : esc(name);
    let summary = "";
    if (run.summary) {
      summary = chHref
        ? `<a href="${esc(chHref)}" target="_blank" rel="noopener" ` +
          `title="Open source channel">${esc(run.summary)}</a>`
        : esc(run.summary);
    }
    return (
      `<div class="src-channel">${channel}</div>` +
      (summary ? `<div class="src-summary">${summary}</div>` : "") +
      `<div class="src-sub">msg ${esc(run.src_msg_id)}</div>`
    );
  }

  function render(runs) {
    els.tbody.replaceChildren();
    const shown = selectedSrc
      ? runs.filter((r) => r.src_ch_id === selectedSrc)
      : runs;
    renderOverview(shown);
    els.noMatches.classList.toggle("hidden", shown.length > 0 || !runs.length);
    for (const run of shown) {
      const st = statusOf(run);
      const tr = document.createElement("tr");
      tr.className = "run" + (expanded.has(run.src_msg_id) ? " open" : "");
      tr.innerHTML =
        `<td><span class="chip ${st.cls}">${esc(st.label)}</span></td>` +
        `<td>${sourceCell(run)}</td>` +
        `<td class="num">${countsCell(run)}${progressBar(run)}</td>` +
        `<td class="num">${crosspostCell(run)}</td>` +
        `<td><span class="when">${whenCell(run)}</span></td>`;
      tr.addEventListener("click", () => toggle(run.src_msg_id));
      els.tbody.appendChild(tr);

      if (expanded.has(run.src_msg_id)) {
        const dr = document.createElement("tr");
        dr.className = "detail-row";
        const td = document.createElement("td");
        td.colSpan = 5;
        const panel = document.createElement("div");
        panel.className = "detail";
        td.appendChild(panel);
        dr.appendChild(td);
        els.tbody.appendChild(dr);
        loadDetail(run, panel);
      }
    }
  }

  function toggle(srcId) {
    if (expanded.has(srcId)) expanded.delete(srcId);
    else expanded.add(srcId);
    if (lastRuns) render(lastRuns);
  }

  let lastRuns = null;

  // Rebuild the source-channel dropdown from the distinct sources in the loaded runs,
  // preserving the current selection (drop it if that source is no longer present).
  function populateFilter(runs) {
    const seen = new Map(); // src_ch_id -> label
    for (const r of runs) {
      if (!seen.has(r.src_ch_id)) {
        seen.set(r.src_ch_id, r.src_name ? `#${r.src_name}` : `#${r.src_ch_id}`);
      }
    }
    if (![...seen.keys()].includes(selectedSrc)) selectedSrc = "";
    const opts = ['<option value="">All source channels</option>'];
    for (const [id, label] of [...seen.entries()].sort((a, b) =>
      a[1].localeCompare(b[1]),
    )) {
      const sel = id === selectedSrc ? " selected" : "";
      opts.push(`<option value="${esc(id)}"${sel}>${esc(label)}</option>`);
    }
    els.srcFilter.innerHTML = opts.join("");
    els.filterBar.classList.toggle("hidden", seen.size < 2);
  }

  els.srcFilter.addEventListener("change", () => {
    selectedSrc = els.srcFilter.value;
    if (lastRuns) render(lastRuns);
  });

  async function load() {
    const token = ++pollToken;
    try {
      const data = await fetchJSON("/mirror-logs/data");
      if (token !== pollToken) return; // superseded by a newer load
      lastRuns = data.runs;
      if (els.windowDays) els.windowDays.textContent = String(data.window_days);
      els.loading.classList.add("hidden");
      els.error.classList.add("hidden");

      if (!data.runs.length) {
        els.empty.classList.remove("hidden");
        els.table.classList.add("hidden");
        els.filterBar.classList.add("hidden");
        els.noMatches.classList.add("hidden");
        els.overview.classList.add("hidden");
      } else {
        els.empty.classList.add("hidden");
        els.table.classList.remove("hidden");
        populateFilter(data.runs);
        render(data.runs);
      }

      // Keep the view live only while something is still in flight.
      const anyPending = data.runs.some((r) => r.pending > 0);
      if (anyPending) setTimeout(() => token === pollToken && load(), POLL_MS);
    } catch (e) {
      if (token !== pollToken) return;
      els.loading.classList.add("hidden");
      els.error.textContent = `Failed to load mirror logs: ${e.message}`;
      els.error.classList.remove("hidden");
    }
  }

  // Charts size to their container width, so re-draw the overview chart on resize
  // (debounced) — matching the /stats page's behaviour.
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (lastShown.length) renderOverviewChart(lastShown);
    }, 150);
  });

  load();
})();
