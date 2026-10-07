/* Skyscanner price tracker — dashboard (χωρίς εξαρτήσεις) */
(() => {
  "use strict";

  const PLACES = { CHQ: "Χανιά", ATH: "Αθήνα", HER: "Ηράκλειο", SKG: "Θεσσαλονίκη", JMK: "Μύκονος", JTR: "Σαντορίνη", RHO: "Ρόδος" };
  const TZ = "Europe/Athens";
  const params = new URLSearchParams(location.search);
  const DEMO = params.get("demo") === "1";
  const SVGNS = "http://www.w3.org/2000/svg";

  const state = { data: null, route: null, date: null, flight: null, gran: 6, when: "upcoming" };
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ utils
  function el(tag, attrs, ...kids) {
    const n = tag.startsWith("svg:") ? document.createElementNS(SVGNS, tag.slice(4)) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.setAttribute("class", v);
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return n;
  }
  const svg = (tag, attrs, ...kids) => el("svg:" + tag, attrs, ...kids);
  const place = (c) => PLACES[c] || c;
  const routeLabel = (r) => { const [a, b] = r.split("-"); return `${place(a)} → ${place(b)}`; };

  const euroFmt0 = new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const euroFmt2 = new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const euro = (v) => (v == null ? "—" : (Math.abs(v - Math.round(v)) < 0.005 ? euroFmt0 : euroFmt2).format(v));
  const euroR = (v) => (v == null ? "—" : euroFmt0.format(Math.round(v)));
  const pct = (v) => `${Math.round(v * 100)}%`;

  const fmtDT = new Intl.DateTimeFormat("el-GR", { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const fmtDH = new Intl.DateTimeFormat("el-GR", { timeZone: TZ, weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const fmtDay = new Intl.DateTimeFormat("el-GR", { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric" });
  const fmtHM = new Intl.DateTimeFormat("el-GR", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const fmtYMD = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
  const fmtLongDate = new Intl.DateTimeFormat("el-GR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
  const fmtShortDate = new Intl.DateTimeFormat("el-GR", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

  const dateOnly = (ymd) => new Date(ymd + "T00:00:00Z"); // για ονόματα ημερών
  const todayAthens = () => fmtYMD.format(new Date());

  function athensToDate(localStr) {
    // "2026-10-13T06:05" (ώρα Ελλάδας) -> Date
    const [d, t = "00:00"] = localStr.split("T");
    const [y, m, dd] = d.split("-").map(Number);
    const [hh, mi] = t.split(":").map(Number);
    const guess = Date.UTC(y, m - 1, dd, hh, mi);
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" })
      .formatToParts(new Date(guess)).map((p) => [p.type, p.value]));
    const asTz = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute);
    return new Date(guess - (asTz - guess));
  }

  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 60) return `πριν ${Math.max(mins, 1)} λεπτά`;
    const h = Math.round(mins / 60);
    if (h < 48) return `πριν ${h} ${h === 1 ? "ώρα" : "ώρες"}`;
    return `πριν ${Math.round(h / 24)} μέρες`;
  }

  function deltaNode(v, cls = "") {
    if (v == null) return el("span", { class: `delta flat ${cls}`, text: "νέα" });
    if (Math.abs(v) < 0.5) return el("span", { class: `delta flat ${cls}`, text: "— ίδια με χθες" });
    const up = v > 0;
    return el("span", { class: `delta ${up ? "up" : "down"} ${cls}`, text: `${up ? "▲" : "▼"} ${euro(Math.abs(v))} σε 24ω` });
  }

  const REC = {
    buy: { label: "Αγόρασε τώρα", cls: "buy" },
    wait: { label: "Περίμενε", cls: "wait" },
    hold: { label: "Αμφίρροπο", cls: "hold" },
  };
  function badge(f) {
    if (f.status === "departed") return el("span", { class: "badge hold" }, el("span", { class: "dot" }), "Αναχώρησε");
    if (f.status === "missing") return el("span", { class: "badge missing" }, el("span", { class: "dot" }), "Δεν εμφανίζεται");
    const r = f.forecast ? REC[f.forecast.rec] : null;
    if (!r) return el("span", { class: "badge hold" }, el("span", { class: "dot" }), "Χωρίς πρόβλεψη");
    return el("span", { class: `badge ${r.cls}` }, el("span", { class: "dot" }), r.label);
  }

  // ------------------------------------------------------------------ hash state
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.get("route")) state.route = h.get("route");
    if (h.get("date")) state.date = h.get("date");
    if (h.get("f")) state.flight = h.get("f");
    if (h.get("g")) state.gran = +h.get("g") || 6;
    if (h.get("when")) state.when = h.get("when");
  }
  function writeHash() {
    const h = new URLSearchParams();
    if (state.route) h.set("route", state.route);
    if (state.date) h.set("date", state.date);
    if (state.flight) h.set("f", state.flight);
    if (state.gran !== 6) h.set("g", state.gran);
    if (state.when !== "upcoming") h.set("when", state.when);
    history.replaceState(null, "", "#" + h.toString());
  }

  // ------------------------------------------------------------------ filters
  function segmented(container, options, current, onPick) {
    container.replaceChildren(...options.map(([value, label]) =>
      el("button", { type: "button", role: "radio", "aria-checked": String(value === current), onclick: () => onPick(value) }, label)));
  }

  function renderFilters() {
    const d = state.data;
    segmented($("route-seg"), d.config.routes.map((r) => [r, routeLabel(r)]), state.route, (v) => {
      state.route = v; state.date = null; state.flight = null; renderAll();
    });
    segmented($("gran-seg"), [[6, "ανά 6ω"], [12, "ανά 12ω"], [24, "ανά 24ω"]], state.gran, (v) => { state.gran = v; renderAll(); });
    segmented($("when-seg"), [["upcoming", "Επερχόμενες"], ["past", "Παρελθόν"]], state.when, (v) => {
      state.when = v; state.date = null; state.flight = null; renderAll();
    });
  }

  // ------------------------------------------------------------------ dates
  function datesFor(route) {
    const all = Object.keys(state.data.dates[route] || {}).sort();
    const today = todayAthens();
    return state.when === "upcoming" ? all.filter((d) => d >= today) : all.filter((d) => d < today).reverse();
  }

  function dayStats(route, date) {
    const ids = state.data.dates[route][date] || [];
    const fl = ids.map((id) => state.data.flights[id]);
    const live = fl.filter((f) => f.status === "active");
    const min = Math.min(...(live.length ? live : fl).map((f) => f.last_price));
    const series = state.data.day_min[`${route}|${date}`] || [];
    let change = null;
    if (series.length > 1) {
      const last = series[series.length - 1];
      const lastT = new Date(last[0]);
      const prev = [...series].reverse().find((p) => lastT - new Date(p[0]) >= 20 * 3600e3);
      if (prev) change = last[1] - prev[1];
    }
    return { min, change, n: fl.length, live: live.length };
  }

  function renderDates() {
    const strip = $("date-strip");
    const dates = datesFor(state.route);
    if (!dates.includes(state.date)) state.date = dates[0] || null;
    strip.replaceChildren(...dates.map((date) => {
      const s = dayStats(state.route, date);
      const lbl = fmtShortDate.format(dateOnly(date));
      return el("button", {
        class: "date-card", type: "button", "aria-pressed": String(date === state.date),
        "aria-label": `${lbl}, από ${euroR(s.min)}`,
        onclick: () => { state.date = date; state.flight = null; renderAll(); },
      },
      el("div", { class: "d", text: date === todayAthens() ? `Σήμερα · ${lbl.split(" ").slice(1).join(" ")}` : lbl }),
      el("div", { class: "p num", text: `από ${euroR(s.min)}` }),
      el("div", { class: "c" }, deltaNode(s.change)));
    }));
    if (!dates.length) {
      strip.replaceChildren(el("div", { class: "muted", text: state.when === "upcoming" ? "Δεν υπάρχουν ακόμη επερχόμενες πτήσεις." : "Δεν υπάρχουν παλαιότερες πτήσεις ακόμη." }));
    }
    const cur = strip.querySelector('[aria-pressed="true"]');
    if (cur) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  // ------------------------------------------------------------------ flight list
  function sparkline(hist) {
    const pts = hist.slice(-28);
    const w = 84, h = 24;
    if (pts.length < 2) return svg("svg", { width: w, height: h, "aria-hidden": "true" });
    const ys = pts.map((p) => p[1]);
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const sx = (i) => (i / (pts.length - 1)) * (w - 4) + 2;
    const sy = (v) => (hi === lo ? h / 2 : h - 3 - ((v - lo) / (hi - lo)) * (h - 6));
    let dpath = `M${sx(0)},${sy(ys[0])}`;
    for (let i = 1; i < ys.length; i++) dpath += `H${sx(i)}V${sy(ys[i])}`;
    return svg("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" },
      svg("path", { d: dpath, fill: "none", stroke: "var(--series)", "stroke-width": 1.5, "stroke-linejoin": "round" }),
      svg("circle", { cx: sx(ys.length - 1), cy: sy(ys[ys.length - 1]), r: 2.5, fill: "var(--series)" }));
  }

  function renderList() {
    const list = $("flight-list");
    const order = { active: 0, missing: 1, departed: 2 };
    const ids = [...((state.data.dates[state.route] || {})[state.date] || [])]
      .sort((a, b) => order[state.data.flights[a].status] - order[state.data.flights[b].status]);
    $("list-title").textContent = state.date ? `Πτήσεις · ${fmtLongDate.format(dateOnly(state.date))}` : "Πτήσεις";
    const dn = state.data.ai && state.data.ai.day_notes ? state.data.ai.day_notes[`${state.route}|${state.date}`] : null;
    $("day-note").hidden = !dn;
    $("day-note").textContent = dn || "";
    if (!ids.length) { list.replaceChildren(el("div", { class: "card empty", text: "Καμία πτήση." })); return; }
    const dayCard = dayChartCard();
    if (!ids.includes(state.flight)) {
      const live = ids.filter((id) => state.data.flights[id].status === "active");
      const pool = live.length ? live : ids;
      state.flight = pool.reduce((a, b) => (state.data.flights[a].last_price <= state.data.flights[b].last_price ? a : b));
    }
    list.replaceChildren(...(dayCard ? [dayCard.card] : []), ...ids.map((id) => {
      const f = state.data.flights[id];
      const dep = f.dep_local.slice(11, 16), arr = (f.arr_local || "").slice(11, 16);
      return el("button", {
        class: `flight ${f.status !== "active" ? "dim" : ""}`, type: "button", "aria-pressed": String(id === state.flight),
        onclick: () => { state.flight = id; renderAll(); if (matchMedia("(max-width: 860px)").matches) $("detail").scrollIntoView({ behavior: "smooth" }); },
      },
      el("div", {}, el("div", { class: "times num", text: arr ? `${dep} → ${arr}` : dep }),
        el("div", { class: "meta", text: [f.carrier, f.flight_no].filter(Boolean).join(" · ") })),
      el("div", {}, el("div", { class: "price num", text: euro(f.last_price) }),
        f.status === "active" ? deltaNode(f.change_24h) : el("div", { class: "delta flat", text: f.status === "departed" ? "τελευταία τιμή" : "τελευταία φορά " + ago(f.last_seen) })),
      el("div", { class: "row3" }, badge(f), sparkline(f.history)));
    }));
    if (dayCard) renderChart(dayCard.wrap, dayCard.pseudo);
  }

  function dayChartCard() {
    const series = state.data.day_min[`${state.route}|${state.date}`] || [];
    if (series.length < 2) return null;
    const vals = series.map((p) => p[1]);
    const pseudo = { history: series, status: "day", forecast: null, carrier: "φθηνότερη πτήση της ημέρας",
      dep_local: (state.data.dates[state.route][state.date] || []).map((id) => state.data.flights[id].dep_local).sort().pop() };
    const wrap = el("div", { class: "chart-wrap" });
    const card = el("div", { class: "card" },
      el("h3", { class: "section-title", text: "Φθηνότερη τιμή της ημέρας διαχρονικά" }),
      el("div", { class: "muted", style: "font-size:13px", text: `Χαμηλότερη: ${euro(Math.min(...vals))} · Υψηλότερη: ${euro(Math.max(...vals))} · Τώρα: ${euro(vals[vals.length - 1])}` }),
      wrap);
    return { card, wrap, pseudo };
  }

  // ------------------------------------------------------------------ chart
  function niceTicks(lo, hi, count = 5) {
    const span = hi - lo || 1;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }

  function renderChart(wrap, f) {
    const width = Math.max(wrap.clientWidth || 600, 280);
    const height = f.status === "day" ? (width < 480 ? 170 : 190) : (width < 480 ? 240 : 300);
    const m = { l: 46, r: 14, t: 18, b: 30 };
    const hist = f.history.map(([t, v]) => ({ t: new Date(t), v, kind: "hist" }));
    const lastObs = hist[hist.length - 1];
    const steps = (f.forecast && f.status === "active" ? f.forecast.steps : []).filter((s) => s.h % state.gran === 0);
    const fc = steps.map((s) => ({ t: new Date(s.t), v: s.q50, lo: s.q10, hi: s.q90, h: s.h, kind: "fc" }));
    const depT = athensToDate(f.dep_local);

    const tMin = hist[0].t.getTime();
    let tMax = Math.max(lastObs.t.getTime(), fc.length ? fc[fc.length - 1].t.getTime() : 0);
    if (f.status === "active") tMax = Math.max(tMax, depT.getTime());
    if (tMax - tMin < 6 * 3600e3) tMax = tMin + 6 * 3600e3;
    const vals = hist.map((p) => p.v).concat(fc.flatMap((p) => [p.lo, p.hi]));
    let yLo = Math.min(...vals), yHi = Math.max(...vals);
    const pad = Math.max((yHi - yLo) * 0.12, 4);
    yLo = Math.max(0, yLo - pad); yHi += pad;
    const ticks = niceTicks(yLo, yHi, width < 480 ? 5 : 7);
    yLo = Math.min(yLo, ticks[0]); yHi = Math.max(yHi, ticks[ticks.length - 1]);

    const X = (t) => m.l + ((t - tMin) / (tMax - tMin)) * (width - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - yLo) / (yHi - yLo)) * (height - m.t - m.b);

    const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: "0",
      "aria-label": `Ιστορικό και πρόβλεψη τιμής για ${f.flight_no || f.carrier}. Χρησιμοποίησε τα βελάκια για τιμές.` });

    // πλέγμα + άξονας y
    for (const v of ticks) {
      root.append(svg("line", { x1: m.l, x2: width - m.r, y1: Y(v), y2: Y(v), stroke: "var(--grid)", "stroke-width": 1 }));
      root.append(svg("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)", class: "num" }, euroR(v)));
    }
    // άξονας x: ημέρες (μεσάνυχτα Ελλάδας)
    const pxPerDay = (width - m.l - m.r) / ((tMax - tMin) / 86400e3);
    const every = Math.max(1, Math.ceil(68 / pxPerDay)); // τουλάχιστον ~68px ανά ετικέτα
    const startDay = new Date(fmtYMD.format(new Date(tMin)) + "T00:00:00Z");
    for (let i = 1, k = 0; i < 400; i++) {
      const ymd = new Date(startDay.getTime() + i * 86400e3).toISOString().slice(0, 10);
      const dd = athensToDate(ymd + "T00:00"); // μεσάνυχτα Ελλάδας (σωστά και με αλλαγή ώρας)
      if (dd.getTime() > tMax) break;
      if (k++ % every) continue;
      const x = X(dd.getTime());
      root.append(svg("line", { x1: x, x2: x, y1: height - m.b, y2: height - m.b + 4, stroke: "var(--axis)" }));
      root.append(svg("text", { x, y: height - m.b + 17, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)" }, fmtDay.format(dd)));
    }
    root.append(svg("line", { x1: m.l, x2: width - m.r, y1: height - m.b, y2: height - m.b, stroke: "var(--axis)" }));

    // ζώνη αβεβαιότητας + διάμεση πρόβλεψη
    if (fc.length) {
      const top = [{ t: lastObs.t, v: lastObs.v }, ...fc.map((p) => ({ t: p.t, v: p.hi }))];
      const bot = [...fc.map((p) => ({ t: p.t, v: p.lo })).reverse(), { t: lastObs.t, v: lastObs.v }];
      root.append(svg("path", { d: "M" + top.concat(bot).map((p) => `${X(p.t)},${Y(p.v)}`).join("L") + "Z", fill: "var(--series-band)" }));
      const med = [{ t: lastObs.t, v: lastObs.v }, ...fc];
      root.append(svg("path", { d: "M" + med.map((p) => `${X(p.t)},${Y(p.v)}`).join("L"), fill: "none", stroke: "var(--series)", "stroke-width": 2, "stroke-dasharray": "5 4", "stroke-linejoin": "round" }));
    }
    // ιστορικό (step: η τιμή ισχύει μέχρι την επόμενη παρατήρηση)
    let dh = `M${X(hist[0].t)},${Y(hist[0].v)}`;
    for (let i = 1; i < hist.length; i++) dh += `H${X(hist[i].t)}V${Y(hist[i].v)}`;
    root.append(svg("path", { d: dh, fill: "none", stroke: "var(--series)", "stroke-width": 2, "stroke-linejoin": "round" }));

    // «τώρα» & «αναχώρηση»
    const vline = (t, label, anchor) => {
      const x = X(t);
      root.append(svg("line", { x1: x, x2: x, y1: m.t, y2: height - m.b, stroke: "var(--axis)", "stroke-dasharray": "2 3" }));
      root.append(svg("text", { x: x + (anchor === "end" ? -4 : 4), y: m.t - 5, "text-anchor": anchor, "font-size": 11, fill: "var(--ink-2)" }, label));
    };
    if (f.status === "active") {
      const close = X(depT.getTime()) - X(lastObs.t.getTime()) < 80;
      if (!close) vline(lastObs.t.getTime(), "τώρα", X(lastObs.t) > width * 0.7 ? "end" : "start");
      if (depT.getTime() <= tMax) vline(depT.getTime(), close ? "τώρα · αναχώρηση" : "αναχώρηση", "end");
    }
    root.append(svg("circle", { cx: X(lastObs.t), cy: Y(lastObs.v), r: 4.5, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2 }));

    // crosshair + tooltip
    const pts = hist.concat(fc);
    const cross = svg("line", { y1: m.t, y2: height - m.b, stroke: "var(--ink-2)", "stroke-width": 1, visibility: "hidden" });
    const dot = svg("circle", { r: 5, fill: "var(--series)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
    root.append(cross, dot);
    const hit = svg("rect", { x: m.l, y: m.t, width: width - m.l - m.r, height: height - m.t - m.b, fill: "transparent" });
    root.append(hit);
    const tip = $("tooltip");
    let cur = pts.length - 1;
    const show = (i, cx, cy) => {
      cur = i;
      const p = pts[i];
      const x = X(p.t), y = Y(p.v);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible");
      dot.setAttribute("fill", p.kind === "fc" ? "var(--surface)" : "var(--series)");
      dot.setAttribute("stroke", p.kind === "fc" ? "var(--series)" : "var(--surface)");
      tip.replaceChildren(
        el("div", { class: "tv num", text: p.kind === "fc" ? `≈ ${euroR(p.v)}` : euro(p.v) }),
        el("div", { class: "tl", text: p.kind === "fc" ? `πρόβλεψη · 80%: ${euroR(p.lo)}–${euroR(p.hi)}` : (f.status === "day" ? "φθηνότερη της ημέρας" : "τιμή Skyscanner") }),
        el("div", { class: "tl num", text: fmtDT.format(p.t) }));
      tip.hidden = false;
      const r = root.getBoundingClientRect();
      const px = cx ?? r.left + x, py = cy ?? r.top + y;
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = px + 14, topPx = py - th - 12;
      if (left + tw > innerWidth - 8) left = px - tw - 14;
      if (topPx < 8) topPx = py + 14;
      tip.style.left = `${Math.max(8, left)}px`; tip.style.top = `${topPx}px`;
    };
    const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); };
    const nearest = (clientX) => {
      const r = root.getBoundingClientRect();
      const x = (clientX - r.left) * (width / r.width);
      let best = 0, bd = Infinity;
      pts.forEach((p, i) => { const dd = Math.abs(X(p.t) - x); if (dd < bd) { bd = dd; best = i; } });
      return best;
    };
    hit.addEventListener("pointermove", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
    hit.addEventListener("pointerdown", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
    hit.addEventListener("pointerleave", hide);
    root.addEventListener("focus", () => show(cur));
    root.addEventListener("blur", hide);
    root.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { show(Math.max(0, cur - 1)); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(Math.min(pts.length - 1, cur + 1)); e.preventDefault(); }
      if (e.key === "Escape") hide();
    });
    wrap.replaceChildren(root);
  }

  // ------------------------------------------------------------------ detail
  function renderDetail() {
    const box = $("detail");
    const f = state.data.flights[state.flight];
    if (!f) { box.replaceChildren(); return; }
    const fc = f.forecast;
    const dep = f.dep_local.slice(11, 16), arr = (f.arr_local || "").slice(11, 16);

    let recBox;
    if (f.status === "departed") {
      recBox = el("div", { class: "rec-box" }, el("strong", { text: "Η πτήση αναχώρησε" }),
        `Χαμηλότερη τιμή που είδαμε: ${euro(f.min_price)} · τελευταία: ${euro(f.last_price)}.`);
    } else if (f.status === "missing") {
      recBox = el("div", { class: "rec-box" }, el("strong", { text: "Δεν εμφανίζεται στον τελευταίο έλεγχο" }),
        "Πιθανώς έχει γεμίσει ή δεν πουλιέται πλέον μέσω Skyscanner.");
    } else if (fc) {
      const note = state.data.ai && state.data.ai.flight_notes ? state.data.ai.flight_notes[state.flight] : null;
      recBox = el("div", { class: "rec-box" }, el("strong", {}, badge(f)), fc.rec_text,
        fc.steps.length ? ` Πιθανή χαμηλότερη τιμή μέχρι την πτήση: ~${euroR(fc.expected_low)}.` : "",
        note ? el("div", { class: "ai-note" }, el("span", { class: "ai-chip sm", text: "AI" }), note) : null);
    }

    const tiles = el("div", { class: "tiles" },
      tile("Χαμηλότερη", euro(f.min_price)),
      tile("Υψηλότερη", euro(f.max_price)),
      tile("Πιθ. πτώσης", fc && f.status === "active" ? pct(fc.p_drop) : "—", fc ? `πτώση ≥ ${euroR(fc.drop_threshold)}` : null),
      tile("Παρατηρήσεις", String(f.n_obs), `από ${fmtDay.format(new Date(f.first_seen))}`));

    const chartWrap = el("div", { class: "chart-wrap" });
    const hasFc = !!(fc && f.status === "active" && fc.steps.filter((s) => s.h % state.gran === 0).length);
    const legend = el("div", { class: "legend" },
      el("span", {}, el("i", { class: "key-line" }), "Τιμή (ιστορικό)"),
      hasFc ? el("span", {}, el("i", { class: "key-line dash" }), "Πιθανότερη τιμή") : null,
      hasFc ? el("span", {}, el("i", { class: "key-band" }), "Εύρος 80%") : null);

    const head = el("div", { class: "card" },
      el("div", { class: "detail-head" },
        el("div", {},
          el("div", { class: "sub", text: `${routeLabel(f.route)} · ${fmtLongDate.format(dateOnly(f.dep_date))}` }),
          el("h2", { style: "font-size:20px;margin-top:2px", text: `${dep}${arr ? " → " + arr : ""} · ${[f.carrier, f.flight_no].filter(Boolean).join(" ")}` })),
        el("div", { style: "text-align:right" },
          el("div", { class: "big num", text: euro(f.last_price) }),
          f.status === "active" ? deltaNode(f.change_24h) : null)),
      recBox, tiles);

    const chartCard = el("div", { class: "card" },
      el("h3", { class: "section-title", text: "Ιστορικό & πρόβλεψη" }), chartWrap, legend);

    const cards = [head, chartCard];
    const simCard = similarCard(f);
    if (simCard) cards.push(simCard);
    if (fc && f.status === "active" && fc.steps.length) cards.push(forecastTable(f));
    cards.push(historyTable(f));
    box.replaceChildren(...cards);
    renderChart(chartWrap, f);
  }

  function tile(label, value, sub) {
    return el("div", { class: "tile" }, el("div", { class: "l", text: label }), el("div", { class: "v num", text: value }),
      sub ? el("div", { class: "l", text: sub }) : null);
  }

  function forecastTable(f) {
    const steps = f.forecast.steps.filter((s) => s.h % state.gran === 0);
    const rows = steps.map((s) => {
      const d = s.q50 - f.last_price;
      return el("tr", {},
        el("td", { class: "num nowrap", text: fmtDH.format(new Date(s.t)) }),
        el("td", { class: "r num hide-sm", text: `+${s.h}ω` }),
        el("td", { class: "r num" }, el("strong", { text: euroR(s.q50) })),
        el("td", { class: `r num delta ${Math.abs(d) < 1 ? "flat" : d > 0 ? "up" : "down"}`, text: Math.abs(d) < 1 ? "≈" : `${d > 0 ? "+" : "−"}${euroR(Math.abs(d))}` }),
        el("td", { class: "r num muted", text: `${euroR(s.q10)}–${euroR(s.q90)}` }));
    });
    return el("div", { class: "card" },
      el("h3", { class: "section-title", text: `Πρόβλεψη ανά ${state.gran} ώρες` }),
      el("div", { class: "table-scroll" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", { text: "Πότε" }), el("th", { class: "r hide-sm", text: "Σε" }), el("th", { class: "r", text: "Πιθανότερη" }),
          el("th", { class: "r", text: "vs τώρα" }), el("th", { class: "r", text: "Εύρος 80%" }))),
        el("tbody", {}, rows))));
  }

  function historyTable(f) {
    const rows = [...f.history].reverse().map(([t, v], i, arr) => {
      const prev = arr[i + 1];
      const d = prev ? v - prev[1] : null;
      return el("tr", {}, el("td", { class: "num", text: fmtDT.format(new Date(t)) }),
        el("td", { class: "r num", text: euro(v) }),
        el("td", { class: `r num delta ${d == null || Math.abs(d) < 0.5 ? "flat" : d > 0 ? "up" : "down"}`, text: d == null || Math.abs(d) < 0.5 ? "" : `${d > 0 ? "+" : "−"}${euro(Math.abs(d))}` }));
    });
    return el("div", { class: "card" }, el("details", {},
      el("summary", { text: `Όλο το ιστορικό τιμών (${f.history.length} μετρήσεις)` }),
      el("div", { class: "table-scroll" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", { text: "Μέτρηση" }), el("th", { class: "r", text: "Τιμή" }), el("th", { class: "r", text: "Μεταβολή" }))),
        el("tbody", {}, rows)))));
  }

  // ------------------------------------------------------------------ model card
  const METHOD_NAMES = { naive: "«Η τιμή μένει ίδια»", empirical: "Εμπειρικό (στατιστική)", analog: "AI k-NN (παρόμοιες περιπτώσεις)",
    gbm: "AI Gradient Boosting", ensemble: "AI Ensemble (συνδυασμός)" };

  function renderModel() {
    const mdl = state.data.model;
    const bt = mdl.backtest || {};
    const chosen = bt[mdl.method];
    const how = el("div", {},
      el("h3", { class: "section-title", text: "Πώς γίνεται η ανάλυση και η πρόβλεψη" }),
      el("ol", { class: "how" },
        el("li", {}, el("strong", { text: "Συλλογή: " }), "κάθε 6 ώρες γίνεται αναζήτηση στο Skyscanner για κάθε μέρα (σήμερα + 10) και κρατιέται η φθηνότερη τιμή κάθε απευθείας πτήσης."),
        el("li", {}, el("strong", { text: "Ιστορικό: " }), "κάθε τιμή αποθηκεύεται με ώρα, ώστε κάθε πτήση να έχει τη δική της καμπύλη τιμής."),
        el("li", {}, el("strong", { text: "Χαρακτηριστικά: " }), "για κάθε πτήση υπολογίζονται οι ώρες ως την αναχώρηση, η τάση 6ω/24ω, η θέση της τιμής σε σχέση με το ελάχιστο/μέγιστο της και με τις άλλες πτήσεις της ημέρας, η ημέρα, η ώρα και η αεροπορική."),
        el("li", {}, el("strong", { text: "AI πρόβλεψη: " }), "εκπαιδεύονται μοντέλα (στατιστικό, k-NN «παρόμοιες περιπτώσεις», Gradient Boosting και ο συνδυασμός τους) και ελέγχονται σε δεδομένα που δεν έχουν δει. Χρησιμοποιείται αυτόματα το πιο ακριβές."),
        el("li", {}, el("strong", { text: "Πιθανότητα πτώσης: " }), "από πτήσεις που έχουν ήδη φύγει, μαθαίνει πόσο συχνά εμφανίστηκε φθηνότερη τιμή σε παρόμοια κατάσταση."),
        el("li", {}, el("strong", { text: "AI αναλυτής: " }), "το Claude διαβάζει όλα τα παραπάνω και γράφει την ανάλυση στα ελληνικά (καλύτερες επιλογές, εναλλακτικές πτήσεις, σχόλια ανά μέρα και πτήση).")));

    const status = el("div", {},
      el("h3", { class: "section-title", text: "Πόσο αξιόπιστη είναι;" }),
      el("p", {}, el("strong", { text: "Επιλεγμένη μέθοδος: " }), mdl.method_label),
      el("p", { text: `Δεδομένα: ${mdl.data_days} μέρες · ${mdl.n_flights} πτήσεις · ${mdl.n_observations} μετρήσεις τιμής.` }),
      chosen ? el("p", { text: `Σε δεδομένα που δεν είχε δει, η πιθανότερη τιμή απείχε κατά μέσο όρο ${euro(chosen.mae_eur)} από την πραγματική (το «μένει ίδια» απείχε ${euro(bt.naive.mae_eur)}). Το εύρος 80% περιείχε την πραγματική τιμή στο ${pct(chosen.coverage80)} των περιπτώσεων.` })
        : el("p", { text: "Το πρώτο backtest γίνεται μόλις μαζευτούν ~3 μέρες δεδομένων. Μέχρι τότε η πρόβλεψη είναι ουδέτερη υπόθεση με μεγάλη αβεβαιότητα." }),
      el("p", {}, el("strong", { text: "Πιθανότητα πτώσης: " }), mdl.drop_label,
        mdl.drop_backtest && mdl.drop_backtest[`brier_${mdl.drop_method}`] != null ? ` (σφάλμα Brier ${mdl.drop_backtest[`brier_${mdl.drop_method}`]}, μικρότερο = καλύτερο)` : ""));

    const blocks = [how, status];
    const names = ["naive", "empirical", "analog", "gbm", "ensemble"].filter((k) => bt[k]);
    if (names.length > 1) {
      blocks.push(el("div", {},
        el("h3", { class: "section-title", text: "Σύγκριση μεθόδων (backtest)" }),
        el("table", {},
          el("thead", {}, el("tr", {}, el("th", { text: "Μέθοδος" }), el("th", { class: "r", text: "Βελτίωση*" }), el("th", { class: "r", text: "Μέσο σφάλμα" }), el("th", { class: "r", text: "Εντός 80%" }))),
          el("tbody", {}, names.map((k) => el("tr", {},
            el("td", {}, k === mdl.method ? el("strong", { text: `${METHOD_NAMES[k]} ✓` }) : METHOD_NAMES[k]),
            el("td", { class: "r num", text: k === "naive" ? "—" : `${Math.round((1 - bt[k].pinball / bt.naive.pinball) * 100)}%` }),
            el("td", { class: "r num", text: euro(bt[k].mae_eur) }),
            el("td", { class: "r num", text: k === "naive" ? "—" : pct(bt[k].coverage80) }))))),
        el("p", { style: "font-size:12px", text: "* Πόσο καλύτερη είναι συνολικά η πρόβλεψη (πιθανότερη τιμή + εύρος) από το «μένει ίδια». Με αυτό επιλέγεται η μέθοδος." })));
    }
    if (chosen && chosen.by_horizon && chosen.by_horizon.length) {
      blocks.push(el("div", {},
        el("h3", { class: "section-title", text: "Μέσο σφάλμα ανά ορίζοντα" }),
        el("table", {},
          el("thead", {}, el("tr", {}, el("th", { text: "Πόσο μπροστά" }), el("th", { class: "r", text: "Μοντέλο" }), el("th", { class: "r", text: "«Μένει ίδια»" }))),
          el("tbody", {}, chosen.by_horizon.map((r) => el("tr", {},
            el("td", { text: r.label }),
            el("td", { class: "r num" }, el("strong", { text: euro(r.mae_eur) })),
            el("td", { class: "r num muted", text: euro(r.naive_mae_eur) })))))));
    }
    $("model-card").replaceChildren(el("div", { class: "card" }, el("div", { class: "grid2" }, ...blocks)));
  }

  // ------------------------------------------------------------------ AI
  function aiSourceLabel(ai) {
    if (ai.source === "claude") return `AI ανάλυση · Claude${ai.stale ? " (προηγούμενη)" : ""} · ${fmtHM.format(new Date(ai.generated_at))}`;
    return "Αυτόματη σύνοψη (χωρίς AI κλειδί)";
  }

  function renderAI() {
    const box = $("ai-card");
    const ai = state.data.ai;
    if (!ai || !ai.overview) { box.replaceChildren(); return; }
    const picks = (ai.best_picks || []).filter((p) => p.route === state.route && state.data.flights[p.flight_id]);
    box.replaceChildren(el("div", { class: "card ai" },
      el("div", { class: "ai-head" },
        el("span", { class: "ai-chip", text: "AI" }),
        el("h2", { class: "section-title", style: "margin:0", text: aiSourceLabel(ai) })),
      el("p", { class: "ai-text", text: ai.overview }),
      ai.routes && ai.routes[state.route] ? el("p", { class: "ai-text muted2", text: ai.routes[state.route] }) : null,
      picks.length ? el("div", { class: "picks" },
        el("span", { class: "muted", style: "font-size:13px", text: "Καλύτερες επιλογές τώρα:" }),
        picks.map((p) => {
          const f = state.data.flights[p.flight_id];
          return el("button", { class: "pick", type: "button", title: p.reason,
            onclick: () => { state.when = "upcoming"; state.date = p.date; state.flight = p.flight_id; renderAll(); $("detail").scrollIntoView({ behavior: "smooth", block: "start" }); } },
            el("strong", { text: `${fmtShortDate.format(dateOnly(p.date))} · ${f.dep_local.slice(11, 16)} · ${euro(f.last_price)}` }),
            el("span", { class: "pick-reason", text: p.reason }));
        })) : null,
      ai.note ? el("p", { class: "muted", style: "font-size:12px;margin:8px 0 0", text: ai.note }) : null));
  }

  function similarCard(f) {
    const sim = f.forecast && f.forecast.similar;
    if (!sim || !sim.n || f.status !== "active") return null;
    return el("div", { class: "card" },
      el("h3", { class: "section-title", text: "AI ανάλυση: παρόμοιες περιπτώσεις" }),
      el("p", { class: "muted2", style: "margin:0 0 4px;font-size:14px",
        text: `Από ${sim.n} στιγμές σε ${sim.n_flights} πτήσεις που έχουν ήδη φύγει, που έμοιαζαν περισσότερο με την τωρινή (ώρες ως την πτήση, τάση, θέση τιμής, αεροπορική, ώρα):` }),
      el("div", { class: "tiles" },
        tile("Είδαν φθηνότερη", pct(sim.pct_drop), `κατά ≥ ${euroR(f.forecast.drop_threshold)}`),
        tile("Μέση εξοικονόμηση", sim.pct_drop > 0 ? euro(sim.avg_saving_when_drop) : "—", "όταν έπεσε"),
        tile("Έκλεισαν ακριβότερα", pct(sim.pct_final_higher), "στην αναχώρηση"),
        tile("Τυπική μεταβολή", `${sim.median_final_change > 0 ? "+" : ""}${euroR(sim.median_final_change)}`, "μέχρι την πτήση")));
  }

  // ------------------------------------------------------------------ status
  function renderStatus() {
    const d = state.data;
    const line = $("status-line");
    const parts = [];
    if (d.latest_observation) parts.push(`Τελευταίες τιμές: ${fmtDT.format(new Date(d.latest_observation))} (${ago(d.latest_observation)})`);
    if (d.last_run && d.last_run.status !== "ok") {
      parts.push(d.last_run.status === "empty" ? "Η τελευταία συλλογή δεν βρήκε πτήσεις" : "Η τελευταία συλλογή απέτυχε");
    }
    line.textContent = parts.join(" · ") || "Δεν υπάρχουν ακόμη δεδομένα";
    if (d.last_run && d.last_run.error) line.title = d.last_run.error;
  }

  // ------------------------------------------------------------------ main
  function renderAll() {
    renderFilters();
    renderAI();
    renderDates();
    renderList();
    renderDetail();
    writeHash();
  }

  function renderEmpty(msg) {
    $("status-line").textContent = msg;
    document.querySelector(".filters").hidden = true;
    $("date-strip").hidden = true;
    const err = state.data && state.data.last_run && state.data.last_run.error;
    document.querySelector(".layout").replaceChildren(el("div", { class: "card empty", style: "grid-column:1/-1" },
      err ? el("p", { style: "color:var(--up-text)", text: `Τελευταία συλλογή: ${err}` }) : null,
      el("p", { text: "Δεν υπάρχουν ακόμη δεδομένα τιμών. Η πρώτη συλλογή γίνεται αυτόματα μέσα στις επόμενες 6 ώρες (ή τρέξε το workflow χειροκίνητα από το GitHub → Actions)." }),
      el("p", {}, el("a", { href: "?demo=1", text: "Δες πώς θα μοιάζει με demo δεδομένα →" }))));
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch (_) { /* private mode */ }
    if (saved) document.documentElement.dataset.theme = saved;
    $("theme-btn").addEventListener("click", () => {
      const isDark = document.documentElement.dataset.theme
        ? document.documentElement.dataset.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = isDark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (_) { /* ignore */ }
      if (state.data && state.flight) renderDetail();
    });
  }

  async function init() {
    initTheme();
    if (DEMO) $("demo-banner").hidden = false;
    readHash();
    let data;
    try {
      const res = await fetch(`data/${DEMO ? "demo" : "data"}.json?v=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch (e) {
      renderEmpty("Δεν βρέθηκε αρχείο δεδομένων ακόμη.");
      return;
    }
    state.data = data;
    renderStatus();
    renderModel();
    if (!Object.keys(data.flights || {}).length) { renderEmpty("Δεν υπάρχουν ακόμη δεδομένα."); return; }
    if (!data.config.routes.includes(state.route)) state.route = data.config.routes[0];
    renderAll();
    let rt;
    addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { renderList(); renderDetail(); }, 120); });
  }

  init();
})();
