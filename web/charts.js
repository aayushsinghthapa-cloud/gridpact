/* Hand-drawn SVG charts. No library: the dashboard must run offline with zero installs.
 *
 * Charts.supply(): stacked allocation per agent (or per utility kind when there
 *   are many agents), the ivory supply line, the dashed demand line, and the
 *   hatched DEFICIT WEDGE between them: the shortfall the agents negotiate over.
 * Charts.damage(): cumulative damage per policy for the results view.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";

  function el(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function niceTicks(max, count) {
    const raw = max / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => raw <= s);
    const out = [];
    for (let v = 0; v <= max + 1e-9; v += step) out.push(v);
    return out;
  }

  /* Hour spacing for x-axis labels: the smallest step that keeps labels >= 56px apart. */
  function hourStep(hours, width) {
    return [1, 2, 3, 4, 6, 8, 12, 24].find(h => (width / hours) * h >= 56) || 24;
  }

  function fmtHour(h) {
    const hh = Math.floor(h), mm = Math.round((h - hh) * 60);
    return String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0");
  }

  function fmtKw(v) {
    return v >= 10000 ? (v / 1000).toFixed(1) + " MW" : Math.round(v).toLocaleString() + " kW";
  }

  /* stepped path through values[i] held over [i, i+1] */
  function steppedLine(vals, x, y, from = 0) {
    let d = "";
    vals.forEach((v, i) => {
      const xi = x(i + from), xj = x(i + from + 1), yv = y(v);
      d += (i === 0 ? "M" : "L") + xi.toFixed(1) + "," + yv.toFixed(1) + "L" + xj.toFixed(1) + "," + yv.toFixed(1);
    });
    return d;
  }

  function steppedBand(lower, upper, x, y) {
    if (!upper.length) return "";
    let top = "", bot = "";
    upper.forEach((v, i) => { top += (i === 0 ? "M" : "L") + x(i).toFixed(1) + "," + y(v).toFixed(1) + "L" + x(i + 1).toFixed(1) + "," + y(v).toFixed(1); });
    for (let i = lower.length - 1; i >= 0; i--) { bot += "L" + x(i + 1).toFixed(1) + "," + y(lower[i]).toFixed(1) + "L" + x(i).toFixed(1) + "," + y(lower[i]).toFixed(1); }
    return top + bot + "Z";
  }

  function tooltip(container) {
    let t = container.querySelector(".tooltip");
    if (!t) { t = document.createElement("div"); t.className = "tooltip"; t.hidden = true; container.appendChild(t); }
    return t;
  }

  function placeTooltip(t, container, px) {
    const w = t.offsetWidth, cw = container.clientWidth;
    t.style.left = (px + 14 + w > cw ? px - w - 14 : px + 14) + "px";
    t.style.top = "8px";
  }

  /* opts: {records, totalSteps, stepHours, groups:[{key,label,color,ids}], yMax, scheduled:[{step,label}]} */
  function supply(container, opts) {
    const W = container.clientWidth || 800, H = container.clientHeight || 290;
    const m = { l: 56, r: 14, t: 20, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const N = Math.max(1, opts.totalSteps);
    const recs = opts.records;
    const x = i => m.l + (i / N) * iw;
    const yMax = opts.yMax;
    const y = v => m.t + ih - (Math.max(0, v) / yMax) * ih;

    container.querySelectorAll("svg.plot, .chart-empty").forEach(n => n.remove());
    const svg = el("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
    container.prepend(svg);

    // grid + axes
    const g = el("g", { class: "grid" }, svg);
    const ax = el("g", { class: "axis" }, svg);
    niceTicks(yMax, 5).forEach(v => {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, g);
      const t = el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, ax);
      t.textContent = v === 0 ? "0" : fmtKw(v);
    });
    const hours = N * opts.stepHours;
    const every = hourStep(hours, iw);
    for (let h = 0; h <= hours + 1e-9; h += every) {
      const t = el("text", { x: x(h / opts.stepHours), y: H - 6, "text-anchor": h === 0 ? "start" : "middle" }, ax);
      t.textContent = fmtHour(h);
    }

    // scheduled event markers (known in advance) + injected events (from records)
    const evG = el("g", {}, svg);
    const markers = new Map();
    (opts.scheduled || []).forEach(e => markers.set(e.step, (markers.get(e.step) || []).concat(e.label)));
    recs.forEach(r => (r.events || []).forEach(l => {
      const cur = markers.get(r.step) || [];
      if (!cur.includes(l)) markers.set(r.step, cur.concat(l));
    }));
    let lastLabelEnd = -Infinity;
    [...markers.entries()].sort((a, b) => a[0] - b[0]).forEach(([s, labels]) => {
      el("line", { x1: x(s), x2: x(s), y1: m.t, y2: m.t + ih, stroke: "var(--stress)", "stroke-width": 1, "stroke-dasharray": "2 4", opacity: .55 }, evG);
      el("path", { d: `M${x(s) - 5},${m.t - 9}L${x(s) + 5},${m.t - 9}L${x(s)},${m.t - 2}Z`, fill: "var(--stress)" }, evG);
      // short label beside the marker (the part before ":"), skipped if it would collide
      const short = labels[0].split(":")[0] + (labels.length > 1 ? ` +${labels.length - 1}` : "");
      const tx = x(s) + 8, approx = short.length * 6.4;
      if (tx > lastLabelEnd + 8 && tx + approx < W - m.r) {
        const t = el("text", { x: tx, y: m.t - 2, fill: "var(--stress)", style: "font: 500 12px var(--font)" }, evG);
        t.textContent = short;
        lastLabelEnd = tx + approx;
      }
    });

    if (!recs.length) {
      const e = document.createElement("div");
      e.className = "chart-empty";
      e.textContent = "Press Play or Step to start the scenario";
      container.appendChild(e);
    } else {
      // stacked areas
      let base = recs.map(() => 0);
      opts.groups.forEach(gr => {
        const vals = recs.map(r => gr.ids.reduce((s, id) => s + r.agents[id].alloc_kw, 0));
        const top = vals.map((v, i) => base[i] + v);
        el("path", { d: steppedBand(base, top, x, y), fill: gr.color, "fill-opacity": .78 }, svg);
        // 1.5px surface seam between stacked fills
        el("path", { d: steppedLine(top, x, y), stroke: "var(--panel)", "stroke-width": 1.5 }, svg);
        base = top;
      });
      // deficit wedge: demand above supply
      const sup = recs.map(r => r.supply_kw);
      const dem = recs.map(r => Object.values(r.agents).reduce((s, a) => s + a.request_max_kw, 0));
      const upper = dem.map((d, i) => Math.max(d, sup[i]));
      el("path", { d: steppedBand(sup, upper, x, y), fill: "url(#hatch)" }, svg);
      el("path", { d: steppedLine(dem, x, y), stroke: "var(--text-2)", "stroke-width": 1.5, "stroke-dasharray": "5 4" }, svg);
      el("path", { d: steppedLine(sup, x, y), stroke: "var(--ivory)", "stroke-width": 2 }, svg);
      // playhead
      const ph = x(recs.length);
      el("line", { x1: ph, x2: ph, y1: m.t, y2: m.t + ih, stroke: "var(--ivory)", "stroke-width": 1, opacity: .5 }, svg);
      if (recs.length < N) {
        el("rect", { x: ph, y: m.t, width: Math.max(0, x(N) - ph), height: ih, fill: "var(--bg)", opacity: .35 }, svg);
      }
    }
    el("line", { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), stroke: "var(--line-strong)" }, svg);

    // hover layer: crosshair + tooltip
    const hover = el("line", { y1: m.t, y2: m.t + ih, stroke: "var(--text-2)", "stroke-width": 1, visibility: "hidden" }, svg);
    const hit = el("rect", { x: m.l, y: 0, width: iw, height: H, fill: "transparent" }, svg);
    const tt = tooltip(container);
    hit.addEventListener("mousemove", ev => {
      const rect = svg.getBoundingClientRect();
      const px = (ev.clientX - rect.left) * (W / rect.width);
      const i = Math.floor(((px - m.l) / iw) * N);
      const r = recs[i];
      if (!r) { tt.hidden = true; hover.setAttribute("visibility", "hidden"); return; }
      hover.setAttribute("x1", x(i + .5)); hover.setAttribute("x2", x(i + .5));
      hover.setAttribute("visibility", "visible");
      const demand = Object.values(r.agents).reduce((s, a) => s + a.request_max_kw, 0);
      let h = `<div class="tt-h">${fmtHour(r.hour)} · step ${r.step + 1}</div>`;
      h += `<div class="tt-r"><span>Supply</span><b>${fmtKw(r.supply_kw)}</b></div>`;
      h += `<div class="tt-r"><span>Requested</span><b>${fmtKw(demand)}</b></div>`;
      if (demand > r.supply_kw) h += `<div class="tt-r"><span>Shortfall negotiated</span><b>${fmtKw(demand - r.supply_kw)}</b></div>`;
      opts.groups.slice().reverse().forEach(gr => {
        const v = gr.ids.reduce((s, id) => s + r.agents[id].alloc_kw, 0);
        h += `<div class="tt-r"><span><i class="sw" style="background:${gr.color}"></i>${gr.label}</span><b>${fmtKw(v)}</b></div>`;
      });
      if (r.rounds) h += `<div class="tt-r"><span>Negotiation rounds</span><b>${r.rounds}</b></div>`;
      (r.events || []).forEach(l => { h += `<div class="tt-r" style="color:var(--stress)">${l}</div>`; });
      tt.innerHTML = h;
      tt.hidden = false;
      placeTooltip(tt, container, (ev.clientX - rect.left));
    });
    hit.addEventListener("mouseleave", () => { tt.hidden = true; hover.setAttribute("visibility", "hidden"); });
  }

  /* opts: {series:[{key,label,values,emph,dash}], stepHours} */
  function damage(container, opts) {
    const W = container.clientWidth || 600, H = container.clientHeight || 280;
    const m = { l: 60, r: 120, t: 12, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const N = Math.max(1, opts.series[0].values.length);
    const yMax = Math.max(1, ...opts.series.map(s => s.values[s.values.length - 1] || 0)) * 1.06;
    const x = i => m.l + (i / N) * iw;
    const y = v => m.t + ih - (v / yMax) * ih;
    container.innerHTML = "";
    const svg = el("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
    container.appendChild(svg);
    const g = el("g", { class: "grid" }, svg), ax = el("g", { class: "axis" }, svg);
    niceTicks(yMax, 4).forEach(v => {
      el("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }, g);
      const t = el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, ax);
      t.textContent = v >= 1000 ? (v / 1000).toLocaleString() + "k" : String(v);
    });
    const hours = N * opts.stepHours, every = hourStep(hours, iw);
    for (let h = 0; h <= hours + 1e-9; h += every) {
      const t = el("text", { x: x(h / opts.stepHours), y: H - 6, "text-anchor": h === 0 ? "start" : "middle" }, ax);
      t.textContent = fmtHour(h);
    }
    // direct labels at line ends, nudged apart so they never collide
    const ends = opts.series.map(s => ({ s, yy: y(s.values[s.values.length - 1]) })).sort((a, b) => a.yy - b.yy);
    for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 15) ends[i].yy = ends[i - 1].yy + 15;
    opts.series.slice().sort((a, b) => (a.emph ? 1 : 0) - (b.emph ? 1 : 0)).forEach(s => {
      const d = s.values.map((v, i) => (i ? "L" : "M") + x(i + 1).toFixed(1) + "," + y(v).toFixed(1)).join("");
      el("path", { d: "M" + x(0) + "," + y(0) + d.replace(/^M/, "L"), stroke: s.emph ? "var(--ivory)" : "var(--text-3)",
        "stroke-width": s.emph ? 2.5 : 1.75, "stroke-dasharray": s.dash || "" }, svg);
    });
    ends.forEach(({ s, yy }) => {
      const t = el("text", { x: m.l + iw + 8, y: yy + 4, fill: s.emph ? "var(--ivory)" : "var(--text-2)",
        style: `font: ${s.emph ? 600 : 400} 12px var(--font)` }, svg);
      t.textContent = s.label;
    });
    el("line", { x1: m.l, x2: m.l + iw, y1: y(0), y2: y(0), stroke: "var(--line-strong)" }, svg);
    const hover = el("line", { y1: m.t, y2: m.t + ih, stroke: "var(--text-2)", visibility: "hidden" }, svg);
    const hit = el("rect", { x: m.l, y: 0, width: iw, height: H, fill: "transparent" }, svg);
    const tt = tooltip(container);
    hit.addEventListener("mousemove", ev => {
      const rect = svg.getBoundingClientRect();
      const px = (ev.clientX - rect.left) * (W / rect.width);
      const i = Math.min(N - 1, Math.max(0, Math.floor(((px - m.l) / iw) * N)));
      hover.setAttribute("x1", x(i + 1)); hover.setAttribute("x2", x(i + 1)); hover.setAttribute("visibility", "visible");
      let h = `<div class="tt-h">Cumulative damage at ${fmtHour((i + 1) * opts.stepHours)}</div>`;
      opts.series.slice().sort((a, b) => a.values[i] - b.values[i]).forEach(s => {
        h += `<div class="tt-r"><span>${s.label}</span><b>${Math.round(s.values[i]).toLocaleString()}</b></div>`;
      });
      tt.innerHTML = h; tt.hidden = false;
      placeTooltip(tt, container, ev.clientX - rect.left);
    });
    hit.addEventListener("mouseleave", () => { tt.hidden = true; hover.setAttribute("visibility", "hidden"); });
  }

  window.Charts = { supply, damage, fmtHour, fmtKw };
})();
