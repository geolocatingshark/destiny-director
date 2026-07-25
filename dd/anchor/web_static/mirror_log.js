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
    table: document.getElementById("runsTable"),
    tbody: document.querySelector("#runsTable tbody"),
    windowDays: document.getElementById("windowDays"),
  };

  const expanded = new Set(); // src_msg_ids whose detail panel is open
  let pollToken = 0; // bumped to cancel an in-flight poll chain

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

  function renderDetailTable(data) {
    if (!data.rows.length) return `<p class="detail-loading">No destinations.</p>`;
    const head =
      "<tr><th>Destination</th><th>State</th><th>Crosspost</th>" +
      "<th>Attempts</th><th>Detail</th></tr>";
    const body = data.rows
      .map((r) => {
        const errBits = [];
        if (r.error_class) errBits.push(esc(r.error_class));
        if (r.error_ref)
          errBits.push(`<span class="err-ref">${esc(r.error_ref)}</span>`);
        if (r.error_msg) errBits.push(esc(r.error_msg));
        const detail = errBits.length
          ? `<span class="err-detail">${errBits.join(" · ")}</span>`
          : r.deleted
            ? '<span class="err-detail">deleted</span>'
            : "";
        return (
          `<tr><td class="dest-id">${esc(r.dest_ch_id)}</td>` +
          `<td><span class="state ${esc(r.state)}">${esc(r.state)}</span></td>` +
          `<td>${r.crosspost_state === "NOT_APPLICABLE" ? "—" : esc(r.crosspost_state)}</td>` +
          `<td>${r.attempts}</td><td>${detail}</td></tr>`
        );
      })
      .join("");
    const trunc = data.truncated
      ? `<p class="trunc">Showing the first ${data.rows.length} destinations.</p>`
      : "";
    return `<table class="dests"><thead>${head}</thead><tbody>${body}</tbody></table>${trunc}`;
  }

  async function loadDetail(srcId, container) {
    container.innerHTML = `<p class="detail-loading">Loading destinations…</p>`;
    try {
      const data = await fetchJSON(
        `/mirror-logs/data?src=${encodeURIComponent(srcId)}`,
      );
      container.innerHTML = renderDetailTable(data);
    } catch (e) {
      container.innerHTML = `<p class="detail-error">Failed to load detail: ${esc(e.message)}</p>`;
    }
  }

  function render(runs) {
    els.tbody.replaceChildren();
    for (const run of runs) {
      const st = statusOf(run);
      const tr = document.createElement("tr");
      tr.className = "run" + (expanded.has(run.src_msg_id) ? " open" : "");
      tr.innerHTML =
        `<td><span class="chip ${st.cls}">${esc(st.label)}</span></td>` +
        `<td><div class="src-id">${esc(run.src_msg_id)}</div>` +
        `<div class="src-sub">#${esc(run.src_ch_id)}</div></td>` +
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
        loadDetail(run.src_msg_id, panel);
      }
    }
  }

  function toggle(srcId) {
    if (expanded.has(srcId)) expanded.delete(srcId);
    else expanded.add(srcId);
    if (lastRuns) render(lastRuns);
  }

  let lastRuns = null;

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
      } else {
        els.empty.classList.add("hidden");
        els.table.classList.remove("hidden");
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
