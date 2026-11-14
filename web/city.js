/* GridPact on Mumbai: real data, recorded disasters, four policies.
 *
 * The browser never allocates power. It asks the engine for a run
 * (/api/city/run) and draws what came back. The map shading is the one
 * derived quantity: the homes sector's shortfall at that hour, spread over
 * the wards the historic reports name as affected (stated on the page).
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const $ = s => document.querySelector(s);
  const POLICY_ORDER = ["gridpact", "priority", "proportional", "equal"];
  const POLICY_NAME = { gridpact: "GridPact", priority: "Strict priority", proportional: "Proportional", equal: "Equal split" };
  const DASH = { gridpact: "", priority: "6 4", proportional: "2 3", equal: "10 3 2 3" };
  const KIND_LABEL = { replay: "Replay of a recorded day", analog: "Recorded event on today's grid", forecast: "Forecast" };
  const SECTOR_COLOR = { hospital: "var(--s-hospital)", water: "var(--s-water)", emergency: "var(--s-emergency)",
                         telecom: "var(--s-telecom)", homes: "var(--s-homes)", plant: "var(--ivory)", weather: "var(--text-3)" };
  const S = { info: null, run: null, disaster: "grid_2020", intensity: 1, seed: 41, policy: "gridpact",
              t: 40, playing: null, srcIndex: {}, wardEls: {}, facEls: [], reqId: 0 };

  // ------------------------------------------------------------ helpers
  function el(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmtInt = v => Math.round(v).toLocaleString("en-IN");
  function fmtBig(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return (v / 1e6).toFixed(1) + " M";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + " M";
    if (a >= 1e4) return (v / 1e3).toFixed(0) + " k";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + " k";
    return a < 0.5 ? "0" : String(Math.round(v));
  }
  const fmtMW = v => (v >= 100 ? Math.round(v).toLocaleString("en-IN") : v.toFixed(1)) + " MW";
  const hourOf = t => t * 0.25;
  const fmtHour = h => Charts.fmtHour(h);
  function pct(a, b) { return b === 0 ? 0 : (100 * (a - b)) / b; }
  function signedPct(v) {
    const r = Math.abs(v) < 0.05 ? "0.0" : Math.abs(v).toFixed(1);
    return (v < -0.05 ? "−" : v > 0.05 ? "+" : "±") + r + "%";
  }
  function sup(id) {
    const n = S.srcIndex[id];
    if (!n) return "";
    const src = S.info.sources[id];
    return `<a class="src" href="#src-${n}" title="${esc(src.publisher + ": " + src.title)}">${n}</a>`;
  }
  function setStatus(text) { $("#run-status").textContent = text; }

  const tt = $("#tt");
  function showTip(html, ev) {
    tt.innerHTML = html; tt.hidden = false;
    const pad = 14, w = tt.offsetWidth, h = tt.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
    tt.style.left = Math.max(8, x) + "px"; tt.style.top = Math.max(8, y) + "px";
  }
  function hideTip() { tt.hidden = true; }

  function disaster() { return S.info.disasters.find(d => d.id === S.disaster); }
  function series(p) { return S.run.policies[p || S.policy].series; }

  // ------------------------------------------------------------ static sections
  function indexSources() {
    // Number sources in the order the JSON lists them, so the list at the bottom is stable.
    Object.keys(S.info.sources).forEach((id, i) => { S.srcIndex[id] = i + 1; });
  }

  function renderFacts() {
    const I = S.info, P = I.population, g = I.grid;
    const crit = I.sectors.filter(s => s.id !== "homes").reduce((a, s) => a + s.desired_mw, 0);
    const items = [
      ["Population", (P.total / 1e7).toFixed(2) + " crore", `${fmtInt(P.total)} people, Census ${P.year}`, P.source],
      ["Households", (P.households / 1e5).toFixed(1) + " lakh", `${(100 * P.slum / P.total).toFixed(0)}% of residents live in slums`, P.source],
      ["Where", I.location.label, `${I.area_km2.value} km², 24 BMC wards`, I.location.source],
      ["Record demand", g.record_peak.mw.toLocaleString("en-IN") + " MW", g.record_peak.when, g.record_peak.source],
      ["Critical services", crit.toFixed(0) + " MW", `${(100 * crit / g.record_peak.mw).toFixed(1)}% of the record peak`, null],
    ];
    $("#facts").innerHTML = items.map(([k, v, sub, src]) =>
      `<div class="fact"><dt>${k}</dt><dd><b>${esc(v)}</b><span>${esc(sub)}${src ? sup(src) : ""}</span></dd></div>`).join("");
  }

  function renderDisasters() {
    const box = $("#disasters");
    box.innerHTML = S.info.disasters.map(d => `
      <button type="button" class="disaster" role="radio" data-id="${d.id}" aria-checked="${d.id === S.disaster}">
        <span class="d-kind d-kind-${d.kind}">${esc(KIND_LABEL[d.kind])}</span>
        <span class="d-name">${esc(d.name)}</span>
        <span class="d-date">${esc(d.date)}</span>
      </button>`).join("");
    box.querySelectorAll(".disaster").forEach(b => b.addEventListener("click", () => {
      if (S.disaster === b.dataset.id) return;
      S.disaster = b.dataset.id;
      box.querySelectorAll(".disaster").forEach(x => x.setAttribute("aria-checked", String(x === b)));
      S.t = defaultHour();
      $("#hour").value = S.t;
      renderRecord(); updateIntensityNote(); run();
    }));
    box.addEventListener("keydown", ev => {
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(ev.key)) return;
      const btns = [...box.querySelectorAll(".disaster")];
      const i = btns.findIndex(b => b.dataset.id === S.disaster);
      const j = (i + (ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : btns.length - 1)) % btns.length;
      btns[j].focus(); btns[j].click(); ev.preventDefault();
    });
  }

  function renderPolicySeg() {
    const box = $("#policy");
    box.innerHTML = POLICY_ORDER.map(p => `<button type="button" role="radio" class="seg-btn" data-p="${p}" aria-checked="${p === S.policy}">${POLICY_NAME[p]}</button>`).join("");
    box.querySelectorAll(".seg-btn").forEach(b => b.addEventListener("click", () => {
      S.policy = b.dataset.p;
      box.querySelectorAll(".seg-btn").forEach(x => x.setAttribute("aria-checked", String(x === b)));
      if (S.run) { drawSupply(); drawServices(); updateTime(); }
    }));
  }

  function defaultHour() {
    // Start the scrubber an hour after the disaster begins, where the story is.
    const d = S.info ? disaster() : null;
    if (!d) return 40;
    return Math.min(95, Math.round((d.loss.onset_hour + 1) * 4));
  }

  function updateIntensityNote() {
    const d = disaster();
    const v = S.intensity;
    $("#intensity-out").textContent = v.toFixed(2) + "×";
    let note;
    if (Math.abs(v - 1) < 1e-9) note = d.loss.kind === "assumption" ? "our assumed loss" : "as recorded";
    else note = (v > 1 ? "stronger" : "milder") + " than " + (d.loss.kind === "assumption" ? "assumed" : "recorded");
    $("#intensity-note").textContent = note;
  }

  function renderRecord() {
    const d = disaster();
    $("#record-kind").textContent = KIND_LABEL[d.kind];
    $("#record-kind").className = "kind-badge d-kind-" + d.kind;
    $("#record-title").textContent = d.name;
    $("#record-date").textContent = d.date;
    $("#record-summary").textContent = d.summary;
    $("#record-facts").innerHTML = d.record.map(r =>
      `<div><dt>${esc(r.label)}</dt><dd>${esc(r.value)}${sup(r.source)}</dd></div>`).join("");
    $("#timeline").innerHTML = d.timeline.map((e, i) =>
      `<li><span class="tl-n">${i + 1}</span><time>${fmtHour(e.hour)}</time><span>${esc(e.text)}${sup(e.source)}</span></li>`).join("");
    const lossNote = d.loss.note + (d.demand.note ? " " + d.demand.note : "");
    $("#record-assumption").innerHTML = `<b>${d.loss.kind === "assumption" ? "Assumption" : "How we model it"}.</b> ${esc(lossNote)}`;
  }

  function renderSectors() {
    const tag = k => `<span class="tag tag-${k}">${k === "measured" ? "Measured" : k === "derived" ? "Derived" : "Assumption"}</span>`;
    $("#sectors").innerHTML = S.info.sectors.map(s => `
      <article class="sector" style="--c:${SECTOR_COLOR[s.kind]}">
        <header>
          <svg class="sector-icon" aria-hidden="true"><use href="#i-${s.kind}"/></svg>
          <h3>${esc(s.name)}</h3>
        </header>
        <div class="sector-nums">
          <div><span>Normal load</span><b>${fmtMW(s.desired_mw)}</b></div>
          <div><span>Critical</span><b>${fmtMW(s.critical_mw)}</b></div>
          <div><span>${esc(s.reserve_label)}</span><b>${s.backup_mwh ? s.backup_mwh.toLocaleString("en-IN") + " MWh" : "none"}</b></div>
          <div><span>Serves</span><b>${fmtBig(s.units)} ${esc(s.unit)}</b></div>
        </div>
        <p class="formula">${esc(s.formula)}</p>
        <ul class="inputs">${s.inputs.map(i => `<li>${tag(i.kind)}<span>${esc(i.label)}: <b>${typeof i.value === "number" && i.value < 1 && i.value > 0 ? Math.round(i.value * 100) + "%" : Number(i.value).toLocaleString("en-IN")}</b>${i.source ? sup(i.source) : ""}</span></li>`).join("")}
          <li>${tag("assumption")}<span>Harm weight: ${esc(s.harm.note)}</span></li>
        </ul>
      </article>`).join("");
    const limits = [
      "Supply is one pooled number for the whole city. There are no feeders or substations, so the map spreads the homes shortfall evenly over the wards that the historic reports name as affected.",
      "Population and households are from Census 2011, the latest census. Today's city is larger, so people-counts are conservative.",
      "The tower count is from 2012–13. The mobile network has grown since, so its load here is a lower bound.",
      "Service is taken as proportional to power delivered: a pumping system at 70% of its load serves 70% of residents.",
      "The 2005 and 2021 days replay recorded patterns on today's demand. They are not reconstructions of those days' grids.",
      "Daily demand between the published minimum (05:00) and maximum (16:00) is a smooth interpolation.",
    ];
    $("#limits").innerHTML = limits.map(l => `<li>${esc(l)}</li>`).join("");
  }

  function renderSources() {
    const I = S.info;
    $("#sources-note").textContent = `Accessed ${I.accessed}`;
    $("#sources").innerHTML = Object.entries(I.sources).map(([id, s]) =>
      `<li id="src-${S.srcIndex[id]}"><span class="src-pub">${esc(s.publisher)}</span> <a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}<svg aria-hidden="true"><use href="#i-ext"/></svg></a>${s.caveat ? `<span class="src-caveat">${esc(s.caveat)}</span>` : ""}</li>`).join("");
  }

  // ------------------------------------------------------------ map
  function project(lon, lat) {
    const p = S.info.map.projection;
    return [(lon - p.lon_min) * p.kx * p.scale, (p.lat_max - lat) * p.ky * p.scale];
  }

  function wardZone(w) {
    for (const [zid, z] of Object.entries(S.info.zones)) if (zid !== "_note" && z.wards && z.wards.includes(w)) return zid;
    return null;
  }

  function renderMap() {
    const M = S.info.map, box = $("#map");
    box.innerHTML = "";
    const svg = el("svg", { class: "plot map-svg", viewBox: `-30 -30 ${M.width + 60} ${M.height + 60}`, preserveAspectRatio: "xMidYMid meet" });
    box.appendChild(svg);
    const wardsG = el("g", { class: "wards" }, svg);
    const wardsPop = S.info.population.wards;
    M.wards.forEach(w => {
      const path = el("path", { d: w.d, class: "ward", tabindex: "0", role: "img" }, wardsG);
      const [pop, hh, slum] = wardsPop[w.ward];
      const zone = S.info.zones[wardZone(w.ward)];
      S.wardEls[w.ward] = { path, pop, hh, slum, zone: wardZone(w.ward) };
      const tip = ev => {
        const p = S.wardEls[w.ward].powered;
        showTip(`<div class="tt-h">Ward ${esc(w.ward)} <span class="tt-sub">${esc(zone ? zone.label : "")}</span></div>
          <div class="tt-r"><span>Residents (2011)</span><b>${fmtInt(pop)}</b></div>
          <div class="tt-r"><span>Households</span><b>${fmtInt(hh)}</b></div>
          <div class="tt-r"><span>In slums</span><b>${Math.round(100 * slum / pop)}%</b></div>
          <div class="tt-r"><span>With power at ${fmtHour(hourOf(S.t))}</span><b>${p == null ? "–" : Math.round(100 * p) + "%"}</b></div>`, ev);
      };
      path.addEventListener("mousemove", tip);
      path.addEventListener("mouseleave", hideTip);
      path.addEventListener("focus", () => { const r = path.getBoundingClientRect(); tip({ clientX: r.right, clientY: r.top }); });
      path.addEventListener("blur", hideTip);
    });
    // ward labels only where there is room (the island city wards are tiny)
    const labelsG = el("g", { class: "ward-labels", "aria-hidden": "true" }, svg);
    M.wards.forEach(w => {
      if (!w.centroid) return;
      const t = el("text", { x: w.centroid[0], y: w.centroid[1] + 9, "text-anchor": "middle" }, labelsG);
      t.textContent = w.ward;
    });
    const facG = el("g", { class: "facilities" }, svg);
    S.facEls = S.info.facilities.map(f => {
      const [x, y] = project(f.lon, f.lat);
      const g = el("g", { class: "fac fac-" + f.kind, transform: `translate(${x.toFixed(1)},${y.toFixed(1)})`, tabindex: "0", role: "img", "aria-label": f.name }, facG);
      const ring = el("circle", { r: 22, class: "fac-ring" }, g);
      if (f.kind === "weather") el("path", { d: "M0,-13 L12,9 L-12,9 Z", class: "fac-dot", style: `fill:${SECTOR_COLOR.weather}` }, g);
      else if (f.kind === "plant") el("rect", { x: -11, y: -11, width: 22, height: 22, rx: 3, class: "fac-dot", style: `fill:${SECTOR_COLOR.plant}` }, g);
      else el("circle", { r: 12, class: "fac-dot", style: `fill:${SECTOR_COLOR[f.kind] || "var(--text-2)"}` }, g);
      const tip = ev => {
        const st = facStatus(f);
        showTip(`<div class="tt-h">${esc(f.name)}</div>${f.note ? `<div class="tt-r"><span>${esc(f.note)}</span></div>` : ""}${st ? `<div class="tt-r"><span>${esc(st.sector)} at ${fmtHour(hourOf(S.t))}</span><b>${st.label}</b></div>` : ""}<div class="tt-r"><span>Approximate location</span></div>`, ev);
      };
      g.addEventListener("mousemove", tip);
      g.addEventListener("mouseleave", hideTip);
      return { f, ring };
    });
  }

  function facStatus(f) {
    if (!S.run || !["hospital", "water", "emergency"].includes(f.kind)) return null;
    const a = series().agents[f.kind];
    const code = a.status[S.t];
    const served = a.effective_mw[S.t] / a.desired_mw[S.t];
    const sector = S.info.sectors.find(s => s.id === f.kind).name;
    const word = code === "F" ? "Below critical" : code === "S" ? "Strained" : "Normal";
    return { code, sector, label: `${word} · ${Math.round(100 * Math.min(1, served))}% of normal load` };
  }

  function updateMap() {
    if (!S.run) return;
    const d = disaster(), homes = series().agents.homes, t = S.t;
    const share = Math.min(1, homes.effective_mw[t] / homes.desired_mw[t]);
    const P = S.info.population;
    const affected = new Set(d.affected.flatMap(z => S.info.zones[z].wards));
    const hhAff = [...affected].reduce((a, w) => a + P.wards[w][1], 0);
    const dark = Math.min(1, (P.households * (1 - share)) / hhAff);
    for (const [w, e] of Object.entries(S.wardEls)) {
      e.powered = affected.has(w) ? 1 - dark : 1;
      e.path.style.fillOpacity = (0.07 + 0.55 * e.powered).toFixed(3);
      e.path.setAttribute("aria-label", `Ward ${w}: ${Math.round(100 * e.powered)}% of households with power`);
    }
    S.facEls.forEach(({ f, ring }) => {
      const st = facStatus(f);
      ring.setAttribute("class", "fac-ring" + (st && st.code === "F" ? " is-fail" : st && st.code === "S" ? " is-stress" : ""));
    });
    // zone summary under the map
    $("#zones").innerHTML = ["island_city", "western_suburbs", "eastern_suburbs"].map(z => {
      const Z = S.info.zones[z];
      const hh = Z.wards.reduce((a, w) => a + P.wards[w][1], 0);
      const on = Z.wards.reduce((a, w) => a + P.wards[w][1] * S.wardEls[w].powered, 0) / hh;
      return `<div class="zone"><span>${esc(Z.label.replace(" (BEST area)", ""))}</span><b>${Math.round(100 * on)}%</b><span class="zone-hh">${fmtBig(hh * (1 - on))} households dark</span></div>`;
    }).join("");
  }

  // ------------------------------------------------------------ time
  function updateTime() {
    const h = hourOf(S.t);
    $("#map-clock").textContent = fmtHour(h);
    updateMap();
    document.querySelectorAll(".cursor").forEach(c => {
      const x = c.dataset.x0 * 1 + (S.t + 0.5) * c.dataset.dx;
      c.setAttribute("x1", x); c.setAttribute("x2", x);
    });
    if (S.run) {
      const s = series(), dem = S.run.demand_mw[S.t], sup_ = s.supply_mw[S.t];
      const short = Math.max(0, dem - sup_);
      $("#supply-readout").innerHTML = `<span>${fmtHour(h)}</span><span>Demand <b>${fmtMW(dem)}</b></span><span>Supply <b>${fmtMW(sup_)}</b></span><span class="short">Unmet <b>${fmtMW(short)}</b></span>`;
    }
  }

  function setT(t) {
    S.t = Math.max(0, Math.min(95, t));
    $("#hour").value = S.t;
    updateTime();
  }

  function togglePlay(force) {
    const on = force !== undefined ? force : !S.playing;
    const btn = $("#play");
    if (on && !S.playing) {
      if (S.t >= 95) setT(0);
      S.playing = setInterval(() => { if (S.t >= 95) { togglePlay(false); return; } setT(S.t + 1); }, 140);
    } else if (!on && S.playing) { clearInterval(S.playing); S.playing = null; }
    btn.setAttribute("aria-pressed", String(!!S.playing));
    btn.setAttribute("aria-label", S.playing ? "Pause" : "Play the day");
    btn.querySelector("use").setAttribute("href", S.playing ? "#i-pause" : "#i-play");
  }

  // ------------------------------------------------------------ charts
  function frame(container, H, m) {
    const W = container.clientWidth || 700;
    container.innerHTML = "";
    const svg = el("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
    container.appendChild(svg);
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x = i => m.l + (i / 96) * iw;
    return { svg, W, H, iw, ih, x };
  }

  function niceMax(v) {
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    return [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map(k => k * mag).find(k => k >= v);
  }

  function hourAxis(F, ax, m) {
    const every = F.iw > 520 ? 3 : 6;
    for (let h = 0; h <= 24; h += every) {
      const t = el("text", { x: F.x(h * 4), y: F.H - 6, "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, ax);
      t.textContent = fmtHour(h);
    }
  }

  function cursor(F, m) {
    return el("line", { class: "cursor", y1: m.t, y2: m.t + F.ih, "data-x0": m.l, "data-dx": F.iw / 96,
                        x1: F.x(S.t + 0.5), x2: F.x(S.t + 0.5) }, F.svg);
  }

  function hitLayer(F, m, onHover, container) {
    const hit = el("rect", { x: m.l, y: 0, width: F.iw, height: F.H, fill: "transparent", style: "cursor: crosshair" }, F.svg);
    const idx = ev => {
      const r = F.svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (F.W / r.width);
      return Math.min(95, Math.max(0, Math.floor(((px - m.l) / F.iw) * 96)));
    };
    hit.addEventListener("mousemove", ev => onHover(idx(ev), ev));
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("click", ev => { togglePlay(false); setT(idx(ev)); });
  }

  function stepPath(vals, x, y) {
    let d = "";
    vals.forEach((v, i) => { d += (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + "L" + x(i + 1).toFixed(1) + "," + y(v).toFixed(1); });
    return d;
  }

  function drawSupply() {
    const box = $("#supply-chart"), m = { l: 56, r: 14, t: 22, b: 24 };
    const F = frame(box, box.clientHeight || 280, m);
    const s = series(), dem = S.run.demand_mw, sup_ = s.supply_mw;
    const yMax = niceMax(Math.max(...dem, ...sup_) * 1.05);
    const y = v => m.t + F.ih - (v / yMax) * F.ih;
    const g = el("g", { class: "grid" }, F.svg), ax = el("g", { class: "axis" }, F.svg);
    for (let k = 0; k <= 4; k++) {
      const v = (yMax / 4) * k;
      el("line", { x1: m.l, x2: m.l + F.iw, y1: y(v), y2: y(v) }, g);
      const t = el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, ax);
      t.textContent = v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + "k" : String(v);
    }
    hourAxis(F, ax, m);
    // critical services band at the bottom: how small they are is the point
    const crit = S.info.sectors.filter(x => x.id !== "homes").reduce((a, x) => a + x.desired_mw, 0);
    el("rect", { x: m.l, y: y(crit), width: F.iw, height: y(0) - y(crit), class: "crit-band" }, F.svg);
    const cl = el("text", { x: m.l + 6, y: y(crit) - 5, class: "crit-label" }, F.svg);
    cl.textContent = `Critical services: ${crit.toFixed(0)} MW`;
    // deficit wedge: demand above supply
    let wedge = "";
    for (let i = 0; i < 96; i++) {
      if (dem[i] > sup_[i] + 0.5) wedge += `M${F.x(i).toFixed(1)},${y(sup_[i]).toFixed(1)}L${F.x(i + 1).toFixed(1)},${y(sup_[i]).toFixed(1)}L${F.x(i + 1).toFixed(1)},${y(dem[i]).toFixed(1)}L${F.x(i).toFixed(1)},${y(dem[i]).toFixed(1)}Z`;
    }
    if (wedge) el("path", { d: wedge, fill: "url(#hatch)" }, F.svg);
    // timeline markers, numbered like the list above
    const d = disaster();
    const placed = [];
    d.timeline.forEach((e, i) => {
      const xx = F.x(e.hour * 4);
      el("line", { x1: xx, x2: xx, y1: m.t, y2: m.t + F.ih, class: "tl-mark" }, F.svg);
      let lx = xx;
      while (placed.some(p => Math.abs(p - lx) < 16)) lx += 16;
      placed.push(lx);
      const c = el("g", { class: "tl-badge", transform: `translate(${lx},${m.t - 11})` }, F.svg);
      el("circle", { r: 8 }, c);
      const t = el("text", { y: 4, "text-anchor": "middle" }, c);
      t.textContent = String(i + 1);
    });
    el("path", { d: stepPath(dem, F.x, y), class: "line-demand" }, F.svg);
    el("path", { d: stepPath(sup_, F.x, y), class: "line-supply" }, F.svg);
    el("line", { x1: m.l, x2: m.l + F.iw, y1: y(0), y2: y(0), stroke: "var(--line-strong)" }, F.svg);
    cursor(F, m);
    hitLayer(F, m, (i, ev) => {
      const homes = s.agents.homes;
      showTip(`<div class="tt-h">${fmtHour(hourOf(i))}</div>
        <div class="tt-r"><span>Demand</span><b>${fmtMW(dem[i])}</b></div>
        <div class="tt-r"><span>Supply</span><b>${fmtMW(sup_[i])}</b></div>
        <div class="tt-r"><span>Unmet</span><b>${fmtMW(Math.max(0, dem[i] - sup_[i]))}</b></div>
        <div class="tt-r"><span>Homes powered (${POLICY_NAME[S.policy]})</span><b>${Math.round(100 * Math.min(1, homes.effective_mw[i] / homes.desired_mw[i]))}%</b></div>`, ev);
    });
    $("#supply-legend").innerHTML = `<span><i class="lg-line lg-supply"></i>Supply available</span><span><i class="lg-line lg-demand"></i>City demand</span><span><i class="lg-hatch"></i>Unmet demand</span><span><i class="lg-band"></i>Critical services</span>`;
    updateTime();
  }

  function drawServices() {
    const sectors = S.info.sectors;
    const box = $("#services");
    box.innerHTML = sectors.map(s => `
      <figure class="svc" style="--c:${SECTOR_COLOR[s.kind]}">
        <figcaption><svg aria-hidden="true"><use href="#i-${s.kind}"/></svg><b>${esc(s.name)}</b><span>${fmtMW(s.desired_mw)} · ${fmtBig(s.units)} ${esc(s.unit)}</span></figcaption>
        <div class="svc-chart" data-id="${s.id}"></div>
        <div class="svc-low" data-id="${s.id}"></div>
      </figure>`).join("");
    sectors.forEach(s => {
      const c = box.querySelector(`.svc-chart[data-id="${s.id}"]`);
      const m = { l: 34, r: 8, t: 6, b: 20 };
      const F = frame(c, c.clientHeight || 120, m);
      const y = v => m.t + F.ih - (Math.min(1.05, v) / 1.05) * F.ih;
      const g = el("g", { class: "grid" }, F.svg), ax = el("g", { class: "axis" }, F.svg);
      [0, 0.5, 1].forEach(v => {
        el("line", { x1: m.l, x2: m.l + F.iw, y1: y(v), y2: y(v) }, g);
        const t = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, ax);
        t.textContent = Math.round(v * 100) + "%";
      });
      [0, 6, 12, 18, 24].forEach(h => {
        const t = el("text", { x: F.x(h * 4), y: F.H - 5, "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, ax);
        t.textContent = String(h).padStart(2, "0");
      });
      const a0 = series("gridpact").agents[s.id];
      el("path", { d: stepPath(a0.min_mw.map((v, i) => v / a0.desired_mw[i]), F.x, y), class: "line-min" }, F.svg);
      const order = POLICY_ORDER.filter(p => p !== S.policy).concat([S.policy]);
      order.forEach(p => {
        const a = series(p).agents[s.id];
        const vals = a.effective_mw.map((v, i) => v / a.desired_mw[i]);
        el("path", { d: stepPath(vals, F.x, y), class: "line-policy" + (p === S.policy ? " is-sel" : ""), "stroke-dasharray": p === S.policy ? "" : DASH[p] || "" }, F.svg);
      });
      cursor(F, m);
      hitLayer(F, m, (i, ev) => {
        let h = `<div class="tt-h">${esc(s.name)} at ${fmtHour(hourOf(i))}</div>`;
        POLICY_ORDER.forEach(p => {
          const a = series(p).agents[s.id];
          h += `<div class="tt-r${p === S.policy ? " is-sel" : ""}"><span>${POLICY_NAME[p]}</span><b>${Math.round(100 * Math.min(1, a.effective_mw[i] / a.desired_mw[i]))}%</b></div>`;
        });
        showTip(h, ev);
      });
      // lowest service per policy, so the small multiples can be read without hovering
      const low = POLICY_ORDER.map(p => {
        const a = series(p).agents[s.id];
        const v = Math.min(...a.effective_mw.map((x, i) => x / a.desired_mw[i]));
        return `<span class="${p === S.policy ? "is-sel" : ""}">${POLICY_NAME[p]} <b>${Math.round(100 * Math.min(1, v))}%</b></span>`;
      }).join("");
      box.querySelector(`.svc-low[data-id="${s.id}"]`).innerHTML = `<span class="svc-low-h">Lowest</span>${low}`;
    });
    $("#services-legend").innerHTML = POLICY_ORDER.map(p =>
      `<span class="${p === S.policy ? "is-sel" : ""}"><svg class="lg-policy" viewBox="0 0 28 8" aria-hidden="true"><line x1="1" y1="4" x2="27" y2="4" stroke-dasharray="${p === S.policy ? "" : DASH[p]}"/></svg>${POLICY_NAME[p]}</span>`).join("")
      + `<span><svg class="lg-policy lg-min" viewBox="0 0 28 8" aria-hidden="true"><line x1="1" y1="4" x2="27" y2="4"/></svg>Critical minimum</span>`;
    updateTime();
  }

  // ------------------------------------------------------------ outcome
  function renderOutcome() {
    const R = S.run, P = R.policies, F = R.forecast, d = disaster();
    const gp = P.gridpact.harm;
    const others = POLICY_ORDER.filter(p => p !== "gridpact");
    const best = others.reduce((a, p) => (P[p].harm < P[a].harm ? p : a), others[0]);
    const worst = others.reduce((a, p) => (P[p].harm > P[a].harm ? p : a), others[0]);
    const dBest = pct(gp, P[best].harm), dWorst = pct(gp, P[worst].harm);
    let verdict;
    if (Math.abs(dBest) < 1) verdict = `GridPact matched the best baseline, ${POLICY_NAME[best].toLowerCase()} (${signedPct(dBest)} harm)`;
    else if (dBest < 0) verdict = `GridPact caused the least harm: ${signedPct(dBest)} against the next best, ${POLICY_NAME[best].toLowerCase()}`;
    else verdict = `GridPact caused ${signedPct(dBest)} more harm than the best baseline, ${POLICY_NAME[best].toLowerCase()}`;
    const vsWorst = dWorst < -1 ? `, and ${signedPct(dWorst)} against ${POLICY_NAME[worst].toLowerCase()}` : "";
    $("#outcome-head").innerHTML = `${esc(verdict)}${esc(vsWorst)}. Across ${R.forecast_members} forecast versions of the day it had the lowest harm in <b>${R.forecast_wins}</b>.`;
    $("#outcome-note").textContent = `Recorded inputs at ${S.intensity.toFixed(2)}× intensity, seed ${R.seed}. Cells show the forecast median, with the 10th–90th percentile range of ${R.forecast_members} versions of the day underneath.`;

    const cols = [
      { k: "harm", h: "Harm", sub: "household-hours, equivalent" },
      { k: "homes_dark_hh_h", h: "Homes without power", sub: "household-hours" },
      { k: "water_red_res_h", h: "Water reduced", sub: "resident-hours" },
      { k: "hospital_red_bed_h", h: "Hospital beds on reduced power", sub: "bed-hours" },
      { k: "telecom_red_res_h", h: "Mobile degraded", sub: "resident-hours" },
      { k: "failure_events", h: "Critical failures", sub: "sector failure events" },
    ];
    const minOf = k => Math.min(...POLICY_ORDER.map(p => F[p][k].p50));
    const head = `<thead><tr><th scope="col">Policy</th>${cols.map(c => `<th scope="col"><span>${c.h}</span><small>${c.sub}</small></th>`).join("")}</tr></thead>`;
    const body = POLICY_ORDER.map(p => `<tr class="${p === "gridpact" ? "is-gp" : ""}"><th scope="row">${POLICY_NAME[p]}</th>${cols.map(c => {
      const f = F[p][c.k], isMin = f.p50 <= minOf(c.k) + 1e-9;
      const fmt = c.k === "failure_events" ? (v => String(Math.round(v))) : fmtBig;
      return `<td class="${isMin ? "is-min" : ""}"><b>${fmt(f.p50)}</b><small>${fmt(f.p10)}–${fmt(f.p90)}</small></td>`;
    }).join("")}</tr>`).join("");
    $("#outcome-table").innerHTML = head + `<tbody>${body}</tbody>`;
    const viol = R.forecast_violations + POLICY_ORDER.reduce((a, p) => a + P[p].violations, 0);
    const runs = (R.forecast_members + 1) * POLICY_ORDER.length;
    $("#guarantee-line span").textContent = `${viol} capacity violations in ${runs} runs (${POLICY_ORDER.length} policies × ${R.forecast_members + 1} versions of the day, ${runs * 96} validated steps). Every allocation passed the same validator as the live demo.`;
    $("#guarantee-line").classList.toggle("is-bad", viol > 0);
  }

  // ------------------------------------------------------------ run
  async function run() {
    const id = ++S.reqId;
    const started = performance.now();
    document.body.classList.add("is-loading");
    setStatus("Running 4 policies × 25 versions of the day…");
    try {
      const r = await fetch("/api/city/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disaster: S.disaster, intensity: S.intensity, seed: S.seed }) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
      if (id !== S.reqId) return;                 // a newer request superseded this one
      S.run = data;
      const ms = performance.now() - started;
      setStatus(`${disaster().name} · ran ${4 * (data.forecast_members + 1)} simulations in ${(ms / 1000).toFixed(1)} s`);
      drawSupply(); drawServices(); renderOutcome(); updateTime();
    } catch (e) {
      if (id === S.reqId) setStatus("Run failed: " + e.message);
    } finally {
      if (id === S.reqId) document.body.classList.remove("is-loading");
    }
  }

  let debounce = null;
  function wire() {
    const inten = $("#intensity");
    inten.addEventListener("input", () => { S.intensity = parseFloat(inten.value); updateIntensityNote(); clearTimeout(debounce); debounce = setTimeout(run, 250); });
    $("#seed").addEventListener("change", () => {
      const v = parseInt($("#seed").value, 10);
      if (Number.isFinite(v) && v >= 0) { S.seed = v; run(); } else { $("#seed").value = S.seed; }
    });
    $("#hour").addEventListener("input", () => { togglePlay(false); setT(parseInt($("#hour").value, 10)); });
    $("#play").addEventListener("click", () => togglePlay());
    document.addEventListener("keydown", ev => {
      const tag = (ev.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      if (ev.key === " ") { ev.preventDefault(); togglePlay(); }
      else if (ev.key === "ArrowRight" && !ev.target.closest(".disasters")) { togglePlay(false); setT(S.t + 1); }
      else if (ev.key === "ArrowLeft" && !ev.target.closest(".disasters")) { togglePlay(false); setT(S.t - 1); }
    });
    let rs = null;
    window.addEventListener("resize", () => { clearTimeout(rs); rs = setTimeout(() => { if (S.run) { drawSupply(); drawServices(); } }, 120); });
  }

  async function init() {
    try {
      const r = await fetch("/api/city");
      const info = await r.json();
      if (!r.ok) throw new Error(info.error || r.statusText);
      S.info = info;
    } catch (e) {
      setStatus("Could not load city data: " + e.message);
      return;
    }
    indexSources();
    const q = new URLSearchParams(location.search);
    if (q.get("d") && S.info.disasters.some(d => d.id === q.get("d"))) S.disaster = q.get("d");
    S.t = defaultHour();
    $("#hour").value = S.t;
    renderFacts(); renderDisasters(); renderPolicySeg(); renderRecord(); updateIntensityNote();
    renderMap(); renderSectors(); renderSources(); wire();
    run();
  }

  init();
})();
