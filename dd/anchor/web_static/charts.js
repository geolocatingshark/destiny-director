// DDCharts — a tiny dependency-free inline-SVG chart harness for the stats dashboard.
// Loaded (deferred) before stats.js; exposes window.DDCharts.
//
// Design follows the dataviz skill: thin 2px lines, recessive hairline gridlines, a
// legend only for >= 2 series (a single series is named by the section title), selective
// direct end-labels, and a crosshair + tooltip hover layer by default. Colours come from
// the caller (validated against the dark chart surface); text uses CSS text tokens via
// classes styled in stats.css — never the series colour.
//
// Series colours are assigned by the caller in a FIXED order (identity, never rank), per
// the skill's non-negotiables.
window.DDCharts = (function () {
  const SVGNS = "http://www.w3.org/2000/svg";

  function el(name, attrs, children) {
    const node = document.createElementNS(SVGNS, name);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    for (const c of children || []) node.appendChild(c);
    return node;
  }

  // Bucket [[Date, value], ...] to the given resolution. Weekly buckets to the ISO-week
  // Monday; monthly to the 1st. Returns a date-sorted [[Date, value], ...]. All
  // arithmetic is in UTC to match the DB's UTC days.
  //
  //   agg="sum"  — add values in a period. For FLOWS (event counts like command usage).
  //   agg="last" — take the period's last (most recent) snapshot. For STOCKS/levels
  //                (autopost reach = active-channel count): summing daily snapshots would
  //                be nonsense, so weekly/monthly show the reach as of the period's end.
  function bucketByResolution(points, resolution, agg) {
    const keyOf = (d) => {
      const y = d.getUTCFullYear();
      const m = d.getUTCMonth();
      const day = d.getUTCDate();
      if (resolution === "monthly") return Date.UTC(y, m, 1);
      if (resolution === "weekly") {
        const t = Date.UTC(y, m, day);
        const dow = (new Date(t).getUTCDay() + 6) % 7; // Mon=0 … Sun=6
        return t - dow * 86400000;
      }
      return Date.UTC(y, m, day); // daily
    };
    const groups = new Map(); // key -> { sum, lastT, lastV }
    for (const [d, v] of points) {
      const k = keyOf(d);
      const t = d.getTime();
      const g = groups.get(k) || { sum: 0, lastT: -Infinity, lastV: 0 };
      g.sum += v;
      if (t >= g.lastT) { g.lastT = t; g.lastV = v; }
      groups.set(k, g);
    }
    return [...groups.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([k, g]) => [new Date(k), agg === "last" ? g.lastV : g.sum]);
  }

  // A "nice" axis top >= max, plus a step, giving ~`ticks` clean gridlines from 0.
  function niceScale(max, ticks) {
    if (max <= 0) return { top: 1, step: 1 };
    const raw = max / ticks;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    return { top: Math.ceil(max / step) * step, step };
  }

  const fmtInt = (n) => Number(n).toLocaleString();

  function fmtDate(d, resolution) {
    if (resolution === "monthly")
      return d.toLocaleString("en", { month: "short", year: "2-digit", timeZone: "UTC" });
    return d.toLocaleString("en", { month: "short", day: "numeric", timeZone: "UTC" });
  }

  // Render a line chart into `container`.
  //   opts.series:     [{ name, color, points: [[Date, number], ...] }]  (assumed x-sorted)
  //   opts.resolution: "daily" | "weekly" | "monthly"  (only used for label formatting)
  //   opts.height:     px (default 240)
  function lineChart(container, opts) {
    const series = (opts.series || []).filter((s) => s.points && s.points.length);
    container.replaceChildren();

    if (!series.length) {
      const empty = document.createElement("p");
      empty.className = "chart-empty muted";
      empty.textContent = "No data for this range yet.";
      container.appendChild(empty);
      return;
    }

    const resolution = opts.resolution || "daily";
    const width = container.clientWidth || 640;
    const height = opts.height || 240;
    const M = { top: 12, right: 16, bottom: 26, left: 48 };
    const plotW = Math.max(1, width - M.left - M.right);
    const plotH = Math.max(1, height - M.top - M.bottom);

    // Domains: x over all timestamps, y from 0 (counts) to a nice top.
    let xmin = Infinity, xmax = -Infinity, ymax = 0;
    for (const s of series)
      for (const [d, v] of s.points) {
        const t = d.getTime();
        if (t < xmin) xmin = t;
        if (t > xmax) xmax = t;
        if (v > ymax) ymax = v;
      }
    if (xmin === xmax) { xmin -= 86400000; xmax += 86400000; } // pad a lone point
    const { top: yTop, step: yStep } = niceScale(ymax, 4);

    const xPix = (t) => M.left + ((t - xmin) / (xmax - xmin)) * plotW;
    const yPix = (v) => M.top + plotH - (v / yTop) * plotH;

    const svg = el("svg", {
      class: "chart-svg",
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height: String(height),
      role: "img",
    });

    // Horizontal gridlines + y-axis tick labels.
    for (let v = 0; v <= yTop + 1e-9; v += yStep) {
      const y = yPix(v);
      svg.appendChild(el("line", { class: "chart-grid", x1: M.left, y1: y, x2: width - M.right, y2: y }));
      svg.appendChild(
        el("text", { class: "chart-tick", x: M.left - 8, y: y + 4, "text-anchor": "end" }, [
          document.createTextNode(fmtInt(v)),
        ]),
      );
    }

    // X-axis labels: up to ~6 evenly spaced ticks from the first series' points.
    const xs = series[0].points.map(([d]) => d);
    const stride = Math.max(1, Math.ceil(xs.length / 6));
    for (let i = 0; i < xs.length; i += stride) {
      const x = xPix(xs[i].getTime());
      svg.appendChild(
        el("text", { class: "chart-tick", x, y: height - 8, "text-anchor": "middle" }, [
          document.createTextNode(fmtDate(xs[i], resolution)),
        ]),
      );
    }

    // One path (+ end dot) per series.
    for (const s of series) {
      const d = s.points
        .map(([dt, v], i) => `${i ? "L" : "M"}${xPix(dt.getTime()).toFixed(1)} ${yPix(v).toFixed(1)}`)
        .join(" ");
      svg.appendChild(el("path", { class: "chart-line", d, stroke: s.color, fill: "none" }));
      const [ld, lv] = s.points[s.points.length - 1];
      // End dot with a 2px surface ring (skill: dots carry a surface ring to stay legible).
      svg.appendChild(
        el("circle", { class: "chart-dot", cx: xPix(ld.getTime()), cy: yPix(lv), r: 4, fill: s.color }),
      );
    }

    container.appendChild(svg);

    // Legend (>= 2 series only) + hover crosshair/tooltip.
    if (series.length >= 2) container.appendChild(_legend(series));
    _attachHover(container, svg, { series, xPix, yPix, M, plotH, width, resolution });
  }

  function _legend(series) {
    const wrap = document.createElement("div");
    wrap.className = "chart-legend";
    for (const s of series) {
      const item = document.createElement("span");
      item.className = "legend-item";
      const key = document.createElement("span");
      key.className = "legend-key";
      key.style.background = s.color;
      const label = document.createElement("span");
      label.textContent = s.name;
      item.append(key, label);
      wrap.appendChild(item);
    }
    return wrap;
  }

  // Crosshair + tooltip: track the pointer, snap to the nearest x in the union of series
  // timestamps, draw a vertical rule, and show each series' value at that period.
  function _attachHover(container, svg, ctx) {
    const { series, xPix, M, plotH, width } = ctx;
    const rule = el("line", { class: "chart-crosshair", y1: M.top, y2: M.top + plotH, x1: 0, x2: 0 });
    rule.style.display = "none";
    svg.appendChild(rule);

    const tip = document.createElement("div");
    tip.className = "chart-tooltip hidden";
    container.appendChild(tip);

    // Union of x timestamps (sorted) so snapping works across series of differing length.
    const times = [...new Set(series.flatMap((s) => s.points.map(([d]) => d.getTime())))].sort(
      (a, b) => a - b,
    );

    const overlay = el("rect", {
      class: "chart-overlay",
      x: M.left,
      y: M.top,
      width: Math.max(1, width - M.left - M.right),
      height: plotH,
      fill: "transparent",
    });
    svg.appendChild(overlay);

    const valueAt = (s, t) => {
      const hit = s.points.find(([d]) => d.getTime() === t);
      return hit ? hit[1] : null;
    };

    overlay.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const scale = width / rect.width;
      const mx = (ev.clientX - rect.left) * scale;
      // Nearest timestamp to the pointer.
      let best = times[0], bd = Infinity;
      for (const t of times) {
        const d = Math.abs(xPix(t) - mx);
        if (d < bd) { bd = d; best = t; }
      }
      const cx = xPix(best);
      rule.setAttribute("x1", cx);
      rule.setAttribute("x2", cx);
      rule.style.display = "";

      const date = new Date(best);
      const rows = series
        .map((s) => {
          const v = valueAt(s, best);
          return v == null ? "" :
            `<div class="tt-row"><span class="tt-key" style="background:${s.color}"></span>` +
            `<span class="tt-name">${s.name}</span><span class="tt-val">${fmtInt(v)}</span></div>`;
        })
        .join("");
      tip.innerHTML = `<div class="tt-date">${fmtDate(date, ctx.resolution)}</div>${rows}`;
      tip.classList.remove("hidden");
      // Position within the container, flipping left of the cursor near the right edge.
      const relX = (cx / scale);
      tip.style.left = (relX > rect.width * 0.6 ? relX - tip.offsetWidth - 12 : relX + 12) + "px";
      tip.style.top = "8px";
    });
    overlay.addEventListener("mouseleave", () => {
      rule.style.display = "none";
      tip.classList.add("hidden");
    });
  }

  // Path for a column with a 4px-rounded top and a square baseline (skill mark spec:
  // "4px rounded data-end, square at the baseline").
  function _colPath(x, yTop, w, yBase) {
    const r = Math.min(4, w / 2, Math.max(0, yBase - yTop));
    return (
      `M${x} ${yBase} L${x} ${yTop + r} Q${x} ${yTop} ${x + r} ${yTop} ` +
      `L${x + w - r} ${yTop} Q${x + w} ${yTop} ${x + w} ${yTop + r} L${x + w} ${yBase} Z`
    );
  }

  // Render a single-series column chart into `container`.
  //   opts.bars:  [{ label, value }]  (x order preserved)
  //   opts.color: column fill
  function barChart(container, opts) {
    const bars = opts.bars || [];
    container.replaceChildren();
    if (!bars.length) {
      const empty = document.createElement("p");
      empty.className = "chart-empty muted";
      empty.textContent = "No data yet.";
      container.appendChild(empty);
      return;
    }

    const width = container.clientWidth || 640;
    const height = opts.height || 220;
    const M = { top: 18, right: 12, bottom: 34, left: 48 };
    const plotW = Math.max(1, width - M.left - M.right);
    const plotH = Math.max(1, height - M.top - M.bottom);

    const ymax = Math.max(...bars.map((b) => b.value), 0);
    const { top: yTop, step: yStep } = niceScale(ymax, 4);
    const yPix = (v) => M.top + plotH - (v / yTop) * plotH;

    const n = bars.length;
    const band = plotW / n;
    const barW = Math.min(24, band - 8); // cap 24px; leftover band is air (skill spec)

    const svg = el("svg", {
      class: "chart-svg",
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height: String(height),
      role: "img",
    });

    for (let v = 0; v <= yTop + 1e-9; v += yStep) {
      const y = yPix(v);
      svg.appendChild(el("line", { class: "chart-grid", x1: M.left, y1: y, x2: width - M.right, y2: y }));
      svg.appendChild(
        el("text", { class: "chart-tick", x: M.left - 8, y: y + 4, "text-anchor": "end" }, [
          document.createTextNode(fmtInt(v)),
        ]),
      );
    }

    const tip = document.createElement("div");
    tip.className = "chart-tooltip hidden";
    container.appendChild(tip);

    bars.forEach((b, i) => {
      const x = M.left + band * i + (band - barW) / 2;
      const yBase = M.top + plotH;
      const yColTop = yPix(b.value);
      const path = el("path", {
        class: "chart-bar",
        d: _colPath(x, yColTop, barW, yBase),
        fill: opts.color,
      });
      svg.appendChild(path);
      // Value on the cap (selective direct label — few bars, so every cap is fine).
      svg.appendChild(
        el("text", { class: "chart-tick", x: x + barW / 2, y: yColTop - 6, "text-anchor": "middle" }, [
          document.createTextNode(fmtInt(b.value)),
        ]),
      );
      // Band label under the column.
      svg.appendChild(
        el("text", { class: "chart-tick", x: x + barW / 2, y: height - 10, "text-anchor": "middle" }, [
          document.createTextNode(b.label),
        ]),
      );
      // Per-mark hover (skill: bar charts ship per-mark hover).
      path.addEventListener("mouseenter", () => {
        tip.innerHTML =
          `<div class="tt-date">${b.label}</div>` +
          `<div class="tt-row"><span class="tt-key" style="background:${opts.color}"></span>` +
          `<span class="tt-name">${opts.unit || "servers"}</span><span class="tt-val">${fmtInt(b.value)}</span></div>`;
        tip.classList.remove("hidden");
        tip.style.left = Math.min(x, width - 120) + "px";
        tip.style.top = "6px";
      });
      path.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    });

    container.appendChild(svg);
  }

  return { lineChart, barChart, bucketByResolution };
})();
