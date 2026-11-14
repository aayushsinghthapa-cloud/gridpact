/* GridPact dashboard. Vanilla JS, talks to the local Python server.
 * The browser never computes allocations: every number shown comes from a
 * step record produced by the simulator. */
(function () {
  "use strict";

  // Fixed, validated colour order (blue, aqua, violet, magenta, green). Colour follows the
  // utility KIND, never its rank, so a hospital is always blue in every scenario.
  const KIND_ORDER = ["hospital", "water", "emergency", "telecom", "shelter"];
  const KIND_LABEL = { hospital: "Hospitals", water: "Water pumping", emergency: "Emergency services", telecom: "Telecom", shelter: "Relief shelters" };
  const kindColor = k => KIND_ORDER.includes(k) ? `var(--s-${k})` : "var(--text-3)";
  const kindIcon = k => KIND_ORDER.includes(k) ? `#i-${k}` : "#i-bolt";
  const STATUS_ICON = { OK: "#i-check", STRESSED: "#i-alert", FAILED: "#i-x" };
  const STATUS_TEXT = { OK: "OK", STRESSED: "Stressed", FAILED: "FAILED" };
  const COMPACT_AT = 7;          // more agents than this -> compact table instead of cards
  const LOG_KEEP = 48;

  const $ = s => document.querySelector(s);
  const S = {
    scenarios: [], info: null, session: null, seed: 41, records: [],
    playing: false, timer: null, busy: false, done: false,
    rollbacks: 0, queued: [], compare: null, narration: false,
  };

  // ------------------------------------------------------------ api
  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({ error: "bad response from server" }));
    if (!res.ok) { const e = new Error(data.error || res.statusText); e.data = data; throw e; }
    return data;
  }

  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false; t.classList.toggle("is-error", !!isError);
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3200);
  }

  // ------------------------------------------------------------ helpers
  const kw = v => Math.round(v).toLocaleString() + " kW";
  const pct = v => Math.round(v * 100) + "%";
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const agentName = id => (S.info.agents.find(a => a.id === id) || { name: id }).name;

  function groups() {
    const agents = S.info.agents;
    if (agents.length < COMPACT_AT) {
      return agents.slice().sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind))
        .map(a => ({ key: a.id, label: a.name, color: kindColor(a.kind), ids: [a.id] }));
    }
    const kinds = KIND_ORDER.filter(k => agents.some(a => a.kind === k));
    const other = agents.filter(a => !KIND_ORDER.includes(a.kind));
    const out = kinds.map(k => ({ key: k, label: KIND_LABEL[k], color: kindColor(k), ids: agents.filter(a => a.kind === k).map(a => a.id) }));
    if (other.length) out.push({ key: "other", label: "Other", color: "var(--text-3)", ids: other.map(a => a.id) });
    return out;
  }

  function yMax() {
    const desired = S.info.agents.reduce((s, a) => s + a.desired_kw, 0);
    let observed = 0;
    S.records.forEach(r => {
      const d = Object.values(r.agents).reduce((s, a) => s + a.request_max_kw, 0);
      observed = Math.max(observed, d, r.supply_kw);
    });
    return Math.max(S.info.base_supply_kw * 1.12, desired * 1.25, observed * 1.05);
  }

  // ------------------------------------------------------------ session
  async function startSession() {
    stop();
    const scenario = $("#scenario").value;
    const seed = Math.max(0, Math.min(999999, parseInt($("#seed").value, 10) || 0));
    $("#seed").value = seed;
    try {
      const d = await api("/api/session", { scenario, seed });
      Object.assign(S, { session: d.session, info: d.scenario, seed: d.seed, records: [], done: false,
        rollbacks: 0, queued: [], compare: null });
      $("#scenario-desc").textContent = S.info.description;
      $("#g-seed").textContent = `Deterministic · seed ${S.seed}`;
      buildAgents(); buildInject(); renderAll();
      $("#log").innerHTML = `<p class="log-empty">No steps yet. Each 15-minute step, every utility announces a request and an urgency bid; if demand exceeds supply they negotiate here.</p>`;
      $("#narration").hidden = true;
      if (!$("#view-results").hidden) loadResults();
    } catch (e) { toast("Could not start the run: " + e.message, true); }
  }

  async function stepOnce() {
    if (S.busy || S.done || !S.session) return false;
    S.busy = true;
    try {
      const d = await api("/api/step", { session: S.session, n: 1 });
      d.records.forEach(r => S.records.push(r));
      S.queued = [];
      S.done = d.done;
      renderAll(d.records[d.records.length - 1]);
      if (S.done) {
        stop();
        S.compare = null;
        toast("Scenario complete. Open “Results vs baselines” to compare.");
      }
      return true;
    } catch (e) {
      if (e.data && e.data.rolled_back) S.rollbacks++;
      stop(); renderGuarantees();
      toast(e.message, true);
      return false;
    } finally { S.busy = false; }
  }

  function play() {
    if (S.done) return;
    S.playing = true; setPlayButton();
    const tick = async () => {
      if (!S.playing) return;
      const ok = await stepOnce();
      if (ok && S.playing && !S.done) S.timer = setTimeout(tick, parseInt($("#speed").value, 10));
    };
    tick();
  }
  function stop() { S.playing = false; clearTimeout(S.timer); setPlayButton(); }
  function setPlayButton() {
    const b = $("#play");
    b.setAttribute("aria-pressed", String(S.playing));
    b.querySelector("use").setAttribute("href", S.playing ? "#i-pause" : "#i-play");
    b.querySelector("span").textContent = S.playing ? "Pause" : (S.done ? "Done" : "Play");
    b.disabled = S.done; $("#step").disabled = S.done;
    document.querySelectorAll(".inject-btn").forEach(x => { x.disabled = S.done; });
  }

  async function inject(ev) {
    if (!S.session || S.done) return;
    try {
      const d = await api("/api/inject", { session: S.session, event_id: ev.id });
      S.queued.push(d.label);
      S.compare = null;
      $("#inject-queue").textContent = "Queued for " + Charts.fmtHour(d.queued_for_step * S.info.step_minutes / 60) + ": " + S.queued.join("; ");
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------ builders
  function buildInject() {
    const box = $("#inject");
    box.innerHTML = "";
    S.info.injectable.forEach(ev => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "inject-btn";
      b.innerHTML = `<svg aria-hidden="true"><use href="#i-alert"/></svg><span>${esc(ev.label)}</span>`;
      b.addEventListener("click", () => inject(ev));
      box.appendChild(b);
    });
    $("#inject-queue").textContent = "";
  }

  function buildAgents() {
    const box = $("#agents");
    const agents = S.info.agents;
    $("#agents-note").textContent = agents.length >= COMPACT_AT
      ? `${agents.length} autonomous agents, one per utility`
      : "Each card is one autonomous agent with private state";
    if (agents.length >= COMPACT_AT) {
      box.className = "agents-compact";
      box.innerHTML = `<table class="agent-table"><thead><tr>
        <th>Utility</th><th>Status</th><th class="num">Grid power</th><th>vs request</th>
        <th class="num">Reserve</th><th class="num">Bid</th><th class="num">Credits</th></tr></thead><tbody>${
        agents.map(a => `<tr data-id="${a.id}"><td><span class="cell-name"><i class="kind-dot" style="--c:${kindColor(a.kind)}"></i>${esc(a.name)}</span></td>
          <td class="c-status"></td><td class="num c-alloc"></td>
          <td><div class="meter" style="--c:${kindColor(a.kind)}"><div class="meter-fill"></div><div class="meter-mark"></div></div></td>
          <td class="num c-res"></td><td class="num c-bid"></td><td class="num c-cred"></td></tr>`).join("")}</tbody></table>`;
      return;
    }
    box.className = "agents";
    box.innerHTML = agents.slice().sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)).map(a => `
      <article class="agent" data-id="${a.id}" style="--c:${kindColor(a.kind)}">
        <div class="agent-head">
          <span class="agent-icon"><svg aria-hidden="true"><use href="${kindIcon(a.kind)}"/></svg></span>
          <h3 class="agent-name">${esc(a.name)}</h3>
          <span class="status status-OK" data-f="status"><svg aria-hidden="true"><use href="#i-check"/></svg><span>OK</span></span>
        </div>
        <div>
          <div class="metric-row"><span>Grid power</span><span class="nowrap"><b data-f="alloc">–</b> <span class="of" data-f="req"></span></span></div>
          <div class="meter" title="Bar: allocation vs request. Tick: critical load (below it the utility fails)"><div class="meter-fill" data-f="allocbar"></div><div class="meter-mark" data-f="minmark"></div></div>
        </div>
        <div>
          <div class="metric-row"><span>${esc(a.reserve_label)}</span><b data-f="res">–</b></div>
          <div class="meter meter-reserve" data-f="resmeter"><div class="meter-fill" data-f="resbar"></div></div>
        </div>
        <div class="agent-act" data-f="act">Waiting for first step</div>
        <div class="agent-foot">
          <span>Urgency bid <b data-f="bid">–</b></span>
          <span>Fairness credits <b data-f="cred">0.0</b> <span data-f="credup"></span></span>
        </div>
      </article>`).join("");
  }

  // ------------------------------------------------------------ renderers
  function renderAll(last) {
    renderClock(); renderChart(); renderAgents(last); renderGuarantees(last);
    if (last) prependLog(last);
    setPlayButton();
    $("#narrate").hidden = !(S.narration && S.records.length);
  }

  function renderClock() {
    const n = S.records.length, N = S.info.steps;
    $("#clock").textContent = "T+" + Charts.fmtHour(n * S.info.step_minutes / 60);
    $("#clock-sub").textContent = `step ${n} of ${N} · ${S.info.step_minutes} min each`;
  }

  function renderChart() {
    const gs = groups();
    $("#legend").innerHTML = gs.map(g => `<span><i class="sw" style="background:${g.color}"></i>${esc(g.label)}</span>`).join("")
      + `<span><i class="sw sw-line"></i>Available supply</span><span><i class="sw sw-dash"></i>Total requested</span>`
      + `<span><i class="sw sw-hatch"></i>Requested but not supplied (load + reserve top-up)</span>`;
    Charts.supply($("#chart"), {
      records: S.records, totalSteps: S.info.steps, stepHours: S.info.step_minutes / 60,
      groups: gs, yMax: yMax(), scheduled: S.info.scheduled,
    });
    const r = S.records[S.records.length - 1];
    if (!r) { $("#readout").innerHTML = ""; return; }
    const demand = Object.values(r.agents).reduce((s, a) => s + a.request_max_kw, 0);
    const short = demand - r.supply_kw;
    $("#readout").innerHTML = `<span>Supply <b>${kw(r.supply_kw)}</b></span><span>Allocated <b>${kw(r.allocated_kw)}</b></span>`
      + (short > 0 ? `<span class="short">Short <b>${kw(short)}</b></span>` : `<span>Uncontested</span>`)
      + `<span>Damage so far <b>${Math.round(r.total_damage).toLocaleString()}</b></span>`;
  }

  function agentAction(r, id) {
    const a = r.agents[id];
    if (a.status === "FAILED") {
      const why = a.cascade_from.length ? ` (cascade from ${a.cascade_from.map(agentName).join(", ")})` : "";
      return { cls: "act-fail", icon: "#i-x", text: "Below critical load" + why };
    }
    if (a.conceded_kw > 0) return { cls: "act-concede", icon: "#i-down", text: `Conceded ${kw(a.conceded_kw)} this step` };
    if (r.log.some(l => l.kind === "hold" && l.agent === id)) return { cls: "act-hold", icon: "#i-lock", text: "Holding firm at critical load" };
    if (a.cascade_from.length) return { cls: "act-hold", icon: "#i-alert", text: `Degraded by ${a.cascade_from.map(agentName).join(", ")} outage` };
    if (a.discharge_kw > 0.5) return { cls: "", icon: "#i-bolt", text: `Running ${kw(a.discharge_kw)} from reserve` };
    if (!r.contested) return { cls: "", icon: "#i-check", text: "Fully served" };
    return { cls: "", icon: "#i-check", text: "Kept its request" };
  }

  function statusHTML(s) {
    return `<svg aria-hidden="true"><use href="${STATUS_ICON[s]}"/></svg><span>${STATUS_TEXT[s]}</span>`;
  }

  function renderAgents(last) {
    const r = last || S.records[S.records.length - 1];
    S.info.agents.forEach(cfg => {
      const card = document.querySelector(`[data-id="${cfg.id}"]`);
      if (!card) return;
      if (!r) return;
      const a = r.agents[cfg.id];
      const fill = a.request_max_kw > 0 ? Math.min(1, a.alloc_kw / a.request_max_kw) : 1;
      const minPos = a.request_max_kw > 0 ? Math.min(1, a.min_kw / a.request_max_kw) : 0;
      if (card.tagName === "TR") {
        card.className = a.status === "FAILED" ? "is-failed" : "";
        card.querySelector(".c-status").innerHTML = `<span class="status status-${a.status}">${statusHTML(a.status)}</span>`;
        card.querySelector(".c-alloc").textContent = `${kw(a.alloc_kw)} / ${kw(a.request_max_kw)}`;
        card.querySelector(".meter-fill").style.width = pct(fill);
        card.querySelector(".meter-mark").style.left = `calc(${pct(minPos)} - 1px)`;
        card.querySelector(".c-res").textContent = pct(a.reserve_frac);
        card.querySelector(".c-bid").textContent = a.bid.toFixed(2);
        card.querySelector(".c-cred").textContent = a.credits.toFixed(1);
        return;
      }
      const f = n => card.querySelector(`[data-f="${n}"]`);
      card.classList.toggle("is-failed", a.status === "FAILED");
      card.classList.toggle("is-stressed", a.status === "STRESSED");
      const st = f("status");
      st.className = `status status-${a.status}`; st.innerHTML = statusHTML(a.status);
      f("alloc").textContent = kw(a.alloc_kw);
      f("req").textContent = `of ${kw(a.request_max_kw)}`;
      f("allocbar").style.width = pct(fill);
      f("minmark").style.left = `calc(${pct(minPos)} - 1px)`;
      f("res").textContent = pct(a.reserve_frac);
      f("resbar").style.width = pct(a.reserve_frac);
      const rm = f("resmeter");
      rm.classList.toggle("is-low", a.reserve_frac < 0.3 && a.reserve_frac >= 0.05);
      rm.classList.toggle("is-empty", a.reserve_frac < 0.05);
      const act = agentAction(r, cfg.id);
      const ae = f("act");
      ae.className = "agent-act " + act.cls;
      ae.innerHTML = `<svg aria-hidden="true"><use href="${act.icon}"/></svg><span>${esc(act.text)}</span>`;
      f("bid").textContent = a.bid.toFixed(2);
      f("cred").textContent = a.credits.toFixed(1);
      f("credup").innerHTML = Math.abs(a.credits_earned) >= 0.05
        ? `<span class="${a.credits_earned > 0 ? "credits-up" : "credits-down"}">${a.credits_earned > 0 ? "+" : "−"}${Math.abs(a.credits_earned).toFixed(1)}</span>` : "";
    });
  }

  function renderGuarantees(last) {
    // Re-check capacity in the browser too, from raw watts in every record.
    let viol = 0;
    S.records.forEach(r => {
      const sum = Object.values(r.agents).reduce((s, a) => s + a.alloc_w, 0);
      const ok = r.validator[r.validator.length - 1].ok;
      if (sum > r.supply_w || !ok) viol++;
    });
    $("#g-viol").textContent = viol;
    $("#g-capacity").classList.toggle("is-bad", viol > 0);
    $("#g-commit").textContent = S.records.length;
    $("#g-roll").textContent = S.rollbacks;
    const r = last || S.records[S.records.length - 1];
    if (r) {
      const v = r.validator[r.validator.length - 1];
      const passed = v.checks.filter(c => c.ok).length;
      $("#g-checks").textContent = `${passed}/${v.checks.length} checks passed at ${Charts.fmtHour(r.hour)}` + (r.rejected ? " (proposal rejected, fallback applied)" : "");
    } else $("#g-checks").textContent = "waiting for first step";
  }

  const LOG_ICON = { credit: "#i-handshake", concede: "#i-down", hold: "#i-lock", fallback: "#i-alert", reject: "#i-alert", short: "#i-x", agree: "#i-handshake", grant: "#i-check", open: "#i-bolt", phase: "#i-alert" };

  function prependLog(r) {
    const box = $("#log");
    if (box.querySelector(".log-empty")) box.innerHTML = "";
    box.querySelectorAll(".log-step").forEach(n => n.classList.add("is-old"));
    const div = document.createElement("div");
    div.className = "log-step";
    const demand = Object.values(r.agents).reduce((s, a) => s + a.request_max_kw, 0);
    const tag = r.contested
      ? `<span class="log-tag is-contested">contested · short ${kw(demand - r.supply_kw)} · ${r.rounds} round${r.rounds === 1 ? "" : "s"}${r.fallback ? " · fallback" : ""}</span>`
      : `<span class="log-tag">uncontested</span>`;
    let h = `<div class="log-step-head"><span class="log-time">${Charts.fmtHour(r.hour)}</span>${tag}</div>`;
    r.events.forEach(e => { h += `<div class="log-event"><svg aria-hidden="true"><use href="#i-alert"/></svg><span>${esc(e)}</span></div>`; });
    const lines = r.contested ? r.log.filter(l => l.kind !== "open") : r.log;
    lines.forEach(l => {
      h += `<div class="log-line k-${l.kind}"><span class="log-round">${l.round ? "R" + l.round : ""}</span><span>${esc(l.text)}</span></div>`;
    });
    const failures = Object.entries(r.agents).filter(([, a]) => a.new_failure);
    failures.forEach(([id, a]) => {
      const why = a.cascade_from.length ? `: cascade from ${a.cascade_from.map(agentName).join(", ")}` : "";
      h += `<div class="log-line k-short"><span class="log-round"></span><span>${esc(agentName(id))} FAILED${esc(why)}.</span></div>`;
    });
    const v = r.validator[r.validator.length - 1];
    h += `<div class="log-validator" title="Validator checks for this step">${r.rejected ? `<span class="bad"><svg aria-hidden="true"><use href="#i-x"/></svg>Proposal rejected</span>` : ""}${
      v.checks.map(c => `<span class="${c.ok ? "" : "bad"}"><svg aria-hidden="true"><use href="${c.ok ? "#i-check" : "#i-x"}"/></svg>${esc(c.name)}</span>`).join("")}<span>· committed</span></div>`;
    div.innerHTML = h;
    box.prepend(div);
    while (box.children.length > LOG_KEEP) box.lastChild.remove();
  }

  // ------------------------------------------------------------ results
  const METRICS = [
    { k: "total_damage", label: "Total damage", hint: "Weighted harm from unserved load (lower is better)", fmt: v => Math.round(v).toLocaleString(), better: "low" },
    { k: "failure_events", label: "Failure events", hint: "Times a utility dropped below critical load", fmt: v => v, better: "low" },
    { k: "longest_outage_h", label: "Longest single outage", hint: "Worst continuous failure, any utility", fmt: v => v.toFixed(2) + " h", better: "low" },
    { k: "critical_unserved_kwh", label: "Critical energy unserved", hint: "Energy below critical load, after reserves", fmt: v => Math.round(v).toLocaleString() + " kWh", better: "low" },
    { k: "jain_fairness", label: "Jain's fairness index", hint: "On mean service ratio per utility; 1.0 = equal", fmt: v => v.toFixed(3), better: "high" },
    { k: "capacity_violations", label: "Capacity violations", hint: "Steps where allocations exceeded supply", fmt: v => v, better: "low", guarantee: true },
    { k: "rounds_mean", label: "Negotiation rounds per contested step", hint: "Mean (max)", fmt: (v, m, p) => p === "gridpact" ? `${v.toFixed(2)} (${m.rounds_max})` : "none", better: null },
    { k: "fallback_steps", label: "Fallback steps", hint: "Steps resolved by the deterministic fallback", fmt: v => v, better: null },
    { k: "compute_ms_mean", label: "Compute time per step", hint: "Mean (p95), this laptop", fmt: (v, m) => `${v.toFixed(2)} ms (${m.compute_ms_p95.toFixed(2)})`, better: null },
  ];

  async function loadResults() {
    const box = $("#results");
    if (!S.session) return;
    if (S.compare) return renderResults();
    box.innerHTML = `<p class="loading">Running all four policies on ${esc(S.info.name)}, seed ${S.seed}…</p>`;
    try { S.compare = await api("/api/compare", { session: S.session }); renderResults(); }
    catch (e) { box.innerHTML = `<p class="loading">Could not run the comparison: ${esc(e.message)}. Restart the run and try again.</p>`; }
  }

  function renderResults() {
    const c = S.compare, P = c.policies, order = c.order;
    const base = order.filter(p => p !== "gridpact");
    const ch = c.damage_change_pct;
    const worstBase = base.slice().sort((a, b) => ch[a] - ch[b])[0];   // biggest reduction
    const bestBase = base.slice().sort((a, b) => P[a].total_damage - P[b].total_damage)[0];
    const fmtPct = v => `${Math.abs(v).toFixed(1)}%`;
    let headline;
    if (ch[worstBase] < 0) {
      headline = `GridPact cut total damage by <em>${fmtPct(ch[worstBase])}</em> against ${esc(P[worstBase].label.toLowerCase())}`;
    } else headline = `GridPact did not reduce damage against the baselines on this run`;
    const others = base.filter(p => p !== worstBase).map(p =>
      `${ch[p] <= 0 ? "−" : "+"}${fmtPct(ch[p])} vs ${esc(P[p].label.toLowerCase())}`).join(", ");
    const injected = (c.injected || []).length;
    const box = $("#results");
    box.innerHTML = `<div class="results-wrap">
      <div class="results-head">
        <div>
          <h1 class="headline">${headline}.</h1>
          <p class="results-sub">${others}. Zero capacity violations for every policy. Same scenario (${esc(S.info.name)}), seed ${c.seed}, and identical event log for all four runs; every number below was computed by the simulator just now.</p>
        </div>
        <button class="btn" id="rerun" type="button"><svg aria-hidden="true"><use href="#i-reset"/></svg><span>Re-run comparison</span></button>
      </div>
      <div class="results-grid">
        <section class="panel"><div class="panel-head"><h2>Every metric, same conditions</h2><span class="panel-note">Dot marks the best value per row</span></div>
          <div class="cmp-wrap"><table class="cmp-table"><thead><tr><th>Metric</th>${order.map(p => `<th class="${p === "gridpact" ? "col-gp" : ""}">${esc(P[p].label)}</th>`).join("")}</tr></thead>
          <tbody>${METRICS.map(m => {
            const vals = order.map(p => P[p][m.k]);
            const best = m.better === "low" ? Math.min(...vals) : m.better === "high" ? Math.max(...vals) : null;
            return `<tr class="${m.guarantee ? "row-guarantee" : ""}"><td><span class="metric-name">${m.label}</span><span class="metric-hint">${m.hint}</span></td>${
              order.map(p => {
                const v = P[p][m.k];
                const isBest = best !== null && v === best && !(m.guarantee);
                return `<td class="${p === "gridpact" ? "col-gp" : ""}"><span class="${isBest ? "best" : ""}">${m.fmt(v, P[p], p)}</span></td>`;
              }).join("")}</tr>`;
          }).join("")}</tbody></table></div>
          <p class="results-note">Damage is a model quantity: weight × (comfort shortfall + 6–8× critical shortfall) × escalation for each consecutive unserved step. Baselines get identical requests, reserves and physics; only the allocation rule differs. Events in this comparison: the scenario's scheduled events${injected ? `, plus ${injected} injected live (${c.injected.map(e => esc(e.label) + " at " + Charts.fmtHour(e.step * S.info.step_minutes / 60)).join("; ")})` : ", no live injections"}.</p>
        </section>
        <div class="col-main">
          <section class="panel"><div class="panel-head"><h2>Cumulative damage over the scenario</h2></div>
            <div class="cmp-chart" id="cmp-chart"></div></section>
          <section class="panel"><div class="panel-head"><h2>Longest outage per utility</h2><span class="panel-note">hours, failure events in brackets</span></div>
            <table class="outage-table"><thead><tr><th>Utility</th>${order.map(p => `<th>${esc(P[p].label)}</th>`).join("")}</tr></thead><tbody>${
              S.info.agents.map(a => `<tr><td>${esc(a.name)}</td>${order.map(p => {
                const x = P[p].agents[a.id];
                return `<td class="${p === "gridpact" ? "col-gp" : ""}">${x.longest_outage_h.toFixed(2)} (${x.failures})</td>`;
              }).join("")}</tr>`).join("")}</tbody></table></section>
        </div>
      </div></div>`;
    $("#rerun").addEventListener("click", () => { S.compare = null; loadResults(); });
    drawCompare();
  }

  function drawCompare() {
    const c = S.compare;
    if (!c || !$("#cmp-chart")) return;
    const dash = { priority: "6 4", proportional: "2 3", equal: "10 3 2 3" };
    Charts.damage($("#cmp-chart"), {
      stepHours: S.info.step_minutes / 60,
      series: c.order.map(p => ({ key: p, label: ({ gridpact: "GridPact", priority: "Strict priority", proportional: "Proportional", equal: "Equal split" })[p] || c.policies[p].label, values: c.policies[p].damage_series, emph: p === "gridpact", dash: dash[p] })),
    });
  }

  // ------------------------------------------------------------ tabs, keys, init
  function showTab(which) {
    const live = which === "live";
    $("#tab-live").setAttribute("aria-selected", String(live));
    $("#tab-results").setAttribute("aria-selected", String(!live));
    $("#view-live").hidden = !live; $("#view-results").hidden = live;
    if (live) renderChart(); else { stop(); loadResults(); }
  }

  async function narrate() {
    const box = $("#narration");
    box.hidden = false; box.textContent = "Asking the narrator…";
    try {
      const d = await api("/api/narrate", { session: S.session, step: S.records.length - 1 });
      box.innerHTML = esc(d.text) + `<small>${d.enabled ? "LLM narration of the log above. The allocation itself never uses it." : ""}</small>`;
    } catch (e) { box.textContent = "Narration unavailable: " + e.message; }
  }

  async function init() {
    try {
      const d = await api("/api/scenarios");
      S.scenarios = d.scenarios; S.narration = d.narration;
      $("#scenario").innerHTML = d.scenarios.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    } catch (e) { toast("Server not reachable. Start it with: python3 gridpact.py", true); return; }
    $("#scenario").addEventListener("change", startSession);
    $("#restart").addEventListener("click", startSession);
    $("#seed").addEventListener("keydown", e => { if (e.key === "Enter") startSession(); });
    $("#play").addEventListener("click", () => (S.playing ? stop() : play()));
    $("#step").addEventListener("click", () => { stop(); stepOnce(); });
    $("#tab-live").addEventListener("click", () => showTab("live"));
    $("#tab-results").addEventListener("click", () => showTab("results"));
    $("#narrate").addEventListener("click", narrate);
    document.addEventListener("keydown", e => {
      if (/INPUT|SELECT|TEXTAREA|BUTTON/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey) return;
      if (e.key === " " && !$("#view-live").hidden) { e.preventDefault(); S.playing ? stop() : play(); }
      if (e.key === "ArrowRight" && !$("#view-live").hidden) { e.preventDefault(); stop(); stepOnce(); }
    });
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (S.info) { renderChart(); drawCompare(); } }, 120); });
    // URL options (handy for screenshots / rehearsals): ?scenario=id&seed=N&steps=N&tab=results
    const q = new URLSearchParams(location.search);
    if (q.get("scenario")) $("#scenario").value = q.get("scenario");
    if (q.get("seed")) $("#seed").value = q.get("seed");
    await startSession();
    const pre = Math.min(parseInt(q.get("steps") || "0", 10) || 0, S.info.steps);
    if (pre > 0) {
      const d = await api("/api/step", { session: S.session, n: pre });
      d.records.forEach(r => { S.records.push(r); prependLog(r); });
      S.done = d.done;
      renderAll();
    }
    if (q.get("tab") === "results") showTab("results");
  }

  init();
})();
