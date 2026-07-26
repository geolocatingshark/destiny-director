// Copyright © 2019-present gsfernandes81 — AGPL-3.0-or-later (see repo LICENSE).
//
// Mirror-log page client. Fetches GET /mirror-logs/data (recent runs) and, per expanded
// run, GET /mirror-logs/data?src=<id> (per-destination detail), rendering both tables.
// While any run is still in progress it re-polls every few seconds; otherwise it renders
// once. No live Discord message is involved — this is a stateless read of the ledger.

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
  };

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

  // The expandable detail: the mirrored message itself, as a version render pane (newest
  // version selected, with a diff-vs-previous toggle) plus a jump-to-source button.
  function renderVersionPane(data, run) {
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
    const chips = vs
      .map((v, i) => {
        const active = i === vs.length - 1 ? " active" : "";
        const title = v.captured_at ? new Date(v.captured_at).toLocaleString() : "";
        return (
          `<button type="button" class="vchip${active}" data-idx="${i}" ` +
          `title="${esc(title)}">v${esc(v.version)}</button>`
        );
      })
      .join("");
    return (
      `<div class="versions">` +
      `<div class="version-head">` +
      `<span class="version-label">Versions</span>` +
      `<div class="version-chips">${chips}</div>` +
      `<label class="diff-toggle hidden">` +
      `<input type="checkbox" class="diff-check" /> Highlight changes vs previous` +
      `</label>` +
      jump +
      `</div>` +
      `<div class="render-pane"><p class="detail-loading">Loading render…</p></div>` +
      `</div>`
    );
  }

  // Wire the version chips + diff toggle to the stateless render route. The server
  // returns pre-escaped safe HTML (cv2_render), so it goes straight into innerHTML on
  // ok; an error body is untrusted, so it stays textContent.
  function setupVersionPane(srcId, container, versions) {
    if (!versions.length) return;
    const chips = [...container.querySelectorAll(".vchip")];
    const pane = container.querySelector(".render-pane");
    const toggleLabel = container.querySelector(".diff-toggle");
    const diffCheck = container.querySelector(".diff-check");
    let selectedIdx = versions.length - 1;
    let renderToken = 0;

    async function show() {
      const token = ++renderToken;
      const v = versions[selectedIdx];
      const hasPrev = selectedIdx > 0;
      toggleLabel.classList.toggle("hidden", !hasPrev);
      const diffOn = hasPrev && diffCheck.checked;
      let url = `/mirror-logs/render?src=${encodeURIComponent(srcId)}&v=${encodeURIComponent(v.version)}`;
      if (diffOn)
        url += `&diff=${encodeURIComponent(versions[selectedIdx - 1].version)}`;
      pane.innerHTML = `<p class="detail-loading">Loading render…</p>`;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        const body = await res.text();
        if (token !== renderToken) return; // superseded by a newer selection
        if (res.ok) pane.innerHTML = body;
        else pane.textContent = `Render failed: ${body}`;
      } catch (e) {
        if (token === renderToken) pane.textContent = `Render error: ${e}`;
      }
    }

    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        selectedIdx = Number(chip.dataset.idx);
        chips.forEach((c) => c.classList.toggle("active", c === chip));
        show();
      });
    });
    diffCheck.addEventListener("change", show);
    show();
  }

  async function loadDetail(run, container) {
    container.innerHTML = `<p class="detail-loading">Loading message…</p>`;
    try {
      const data = await fetchJSON(
        `/mirror-logs/data?src=${encodeURIComponent(run.src_msg_id)}`,
      );
      container.innerHTML = renderVersionPane(data, run);
      setupVersionPane(run.src_msg_id, container, data.versions || []);
    } catch (e) {
      container.innerHTML = `<p class="detail-error">Failed to load detail: ${esc(e.message)}</p>`;
    }
  }

  // Channel is named where known (feed name); the latest snapshot's summary sits below.
  // The jump-to-source button lives in the expanded detail (renderVersionPane), so the
  // row just carries the muted msg id as the run identifier.
  function sourceCell(run) {
    const channel = run.src_name ? `#${run.src_name}` : `#${run.src_ch_id}`;
    const summary = run.summary
      ? `<div class="src-summary">${esc(run.summary)}</div>`
      : "";
    return (
      `<div class="src-channel">${esc(channel)}</div>${summary}` +
      `<div class="src-sub">msg ${esc(run.src_msg_id)}</div>`
    );
  }

  function render(runs) {
    els.tbody.replaceChildren();
    const shown = selectedSrc
      ? runs.filter((r) => r.src_ch_id === selectedSrc)
      : runs;
    els.noMatches.classList.toggle("hidden", shown.length > 0 || !runs.length);
    for (const run of shown) {
      const st = statusOf(run);
      const tr = document.createElement("tr");
      tr.className = "run" + (expanded.has(run.src_msg_id) ? " open" : "");
      tr.innerHTML =
        `<td><span class="chip ${st.cls}">${esc(st.label)}</span></td>` +
        `<td>${sourceCell(run)}</td>` +
        `<td class="num">${countsCell(run)}</td>` +
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

  load();
})();
