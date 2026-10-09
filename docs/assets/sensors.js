// Live IoT sensor stations at high-risk bank erosion sites.
//
// Each station: river level, rain gauge, soil moisture at 30/60/100 cm, battery,
// and a line of tilt nodes set back 5, 10, 20 and 40 m from the bank edge.
// Data come from ThingSpeak channels (mode "thingspeak") or, before hardware is
// installed, from a deterministic simulator (mode "simulated") so that the panel,
// charts and alert rules can be demonstrated. Simulated data are always labelled.
(function () {
  const CFG_URL = "data/sensors/stations.json";
  const STEP_MIN = 15;
  const SEV = { critical: 3, warning: 2, watch: 1, info: 0 };
  const SEV_STYLE = { critical: "background:#c53030;color:#fff", warning: "background:#f6ad55;color:#3b2200",
    watch: "background:#fefcbf;color:#744210", info: "background:#edf2f7;color:#4a5568" };
  const STATUS_COLOR = { critical: "#c53030", warning: "#dd6b20", watch: "#d69e2e", info: "#2f855a", normal: "#2f855a" };
  let CFG, DATA = {}, SEL = null, LAYER, CHARTS = [];

  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const f1 = (v, d = 1) => (v == null || !isFinite(v)) ? "–" : Number(v).toFixed(d);
  const tlabel = t => new Date(t).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

  // ---------------------------------------------------------------- simulator
  function rng(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const blockRand = (seed, b) => rng(seed * 100003 + b)();

  function simulate(st, idx, now) {
    const dt = STEP_MIN * 60000, n = Math.round(CFG.history_days * 24 * 60 / STEP_MIN);
    const t0 = Math.floor(now / dt) * dt - (n - 1) * dt, seed = 17 + idx * 31;
    const out = { t: [], level_m: [], rain_mm: [], soil_30: [], soil_60: [], soil_100: [], battery_v: [], tilt: {} };
    st.node_setbacks_m.forEach(s => out.tilt[s] = []);
    let store = 0, s30 = 0, s60 = 0, s100 = 0;
    const base0 = [24, 29, 33];
    for (let k = 0; k < n; k++) {
      const t = t0 + k * dt, h = t / 3600000, hAgo = (now - t) / 3600000;
      // Rain: random 3-hour storm blocks, plus a scripted heavy-rain event at station 3
      const b = Math.floor(h / 3), r = blockRand(seed, b);
      let rain = r > 0.93 ? (blockRand(seed + 7, b) * 10) * Math.sin(Math.PI * ((h % 3) / 3)) / 4 : 0;
      if (idx === 2 && hAgo > 14 && hAgo < 20) rain += 3.2;
      rain = Math.max(0, rain);
      // River stage: slow post-monsoon recession, catchment response, small daily cycle
      store = store * Math.exp(-0.25 / 20) + 0.012 * rain;
      let level = st.level_warning_m - 1.6 - 0.04 * ((t - t0) / 86400000) + store + 0.03 * Math.sin(2 * Math.PI * h / 24);
      // Station 1: a flood pulse that peaks near the warning level, then falls fast
      if (idx === 0 && hAgo < 60) level += hAgo > 18 ? 1.5 * (60 - hAgo) / 42 : 1.5 - (18 - hAgo) * 0.13;
      // Soil moisture responds to rain with lags that grow with depth
      s30 = s30 * Math.exp(-0.25 / 24) + 0.9 * rain;
      s60 = s60 * Math.exp(-0.25 / 48) + 0.45 * rain;
      s100 = s100 * Math.exp(-0.25 / 96) + 0.2 * rain;
      out.t.push(t);
      out.level_m.push(+level.toFixed(3));
      out.rain_mm.push(+rain.toFixed(2));
      out.soil_30.push(+Math.min(48, base0[0] + s30).toFixed(1));
      out.soil_60.push(+Math.min(48, base0[1] + s60).toFixed(1));
      out.soil_100.push(+Math.min(48, base0[2] + s100).toFixed(1));
      const bv = 3.95 + 0.12 * Math.max(0, Math.sin(2 * Math.PI * (h - 6) / 24)) - (idx === 3 ? 0.55 + 0.002 * (t - t0) / 3600000 : 0);
      out.battery_v.push(+bv.toFixed(2));
      // Tilt nodes: small noise; at station 1 the 5 m node tilts and is then lost, the 10 m node starts to creep
      st.node_setbacks_m.forEach((s, j) => {
        const noise = (blockRand(seed + 50 + j, k) - 0.5) * 0.15;
        let v = 0.6 + 0.3 * j + noise;
        if (idx === 0 && s === 5) { if (hAgo < 6) v = null; else if (hAgo < 30) v += (30 - hAgo) * 0.5; }
        if (idx === 0 && s === 10 && hAgo < 10) v += (10 - hAgo) * 0.17;
        out.tilt[s].push(v == null ? null : +v.toFixed(2));
      });
    }
    return out;
  }

  // -------------------------------------------------------------- ThingSpeak
  async function thingspeak(st) {
    const ts = st.thingspeak || {}, days = CFG.history_days;
    const get = async (ch, key) => {
      const u = `https://api.thingspeak.com/channels/${ch}/feeds.json?days=${days}` + (key ? `&api_key=${key}` : "");
      const r = await fetch(u); if (!r.ok) throw new Error("ThingSpeak " + r.status); return (await r.json()).feeds || [];
    };
    const main = ts.main_channel ? await get(ts.main_channel, ts.main_read_key) : [];
    const tilt = ts.tilt_channel ? await get(ts.tilt_channel, ts.tilt_read_key) : [];
    const out = { t: [], level_m: [], rain_mm: [], soil_30: [], soil_60: [], soil_100: [], battery_v: [], tilt: {} };
    const mf = CFG.thingspeak.main_fields, tf = CFG.thingspeak.tilt_fields;
    main.forEach(r => { out.t.push(Date.parse(r.created_at)); Object.entries(mf).forEach(([f, k]) => out[k].push(r[f] == null ? null : +r[f])); });
    // Tilt nodes report on their own clock: put each reading at the nearest main timestamp
    st.node_setbacks_m.forEach(s => out.tilt[s] = out.t.map(() => null));
    tilt.forEach(r => {
      const t = Date.parse(r.created_at); let i = out.t.findIndex(x => x >= t); if (i < 0) i = out.t.length - 1;
      Object.entries(tf).forEach(([f, k]) => { const s = +k.split("_")[1]; if (out.tilt[s] && r[f] != null) out.tilt[s][i] = +r[f]; });
    });
    return out;
  }

  // ------------------------------------------------------------------ alerts
  function lastIdx(arr) { for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null && isFinite(arr[i])) return i; return -1; }
  function stepsFor(h, d) { // number of samples in h hours, from the median spacing
    if (d.t.length < 2) return 1;
    const sp = (d.t[d.t.length - 1] - d.t[0]) / (d.t.length - 1);
    return Math.max(1, Math.round(h * 3600000 / sp));
  }

  function evaluate(st, d, now) {
    const th = CFG.thresholds, A = [];
    const add = (sev, title, text) => A.push({ sev, title, text, station: st.id });
    const n = d.t.length; if (!n) return [{ sev: "warning", title: "No data", text: "The station has not reported.", station: st.id }];
    const i = lastIdx(d.level_m), k6 = stepsFor(6, d);
    if (i >= k6) {
      const ch = d.level_m[i] - d.level_m[i - k6];
      if (ch <= -th.level_fall_m_6h) add("warning", "Rapid fall in river level", `Level fell ${f1(-ch, 2)} m in 6 h. Banks are most likely to collapse as the river falls quickly after high water.`);
      if (ch >= th.level_rise_m_6h) add("watch", "River rising fast", `Level rose ${f1(ch, 2)} m in 6 h.`);
    }
    if (i >= 0 && d.level_m[i] >= st.level_warning_m) add("warning", "Above warning level", `Level ${f1(d.level_m[i], 2)} m (warning ${st.level_warning_m} m).`);
    const k24 = stepsFor(24, d), r24 = d.rain_mm.slice(-k24).reduce((a, b) => a + (b || 0), 0);
    if (r24 >= th.rain_24h_mm) add("warning", "Heavy rain", `${f1(r24, 0)} mm in the last 24 h (IMD heavy rain: 64.5 mm or more).`);
    const sm = d.soil_30[lastIdx(d.soil_30)];
    if (sm >= th.soil_moisture_pct) add("watch", "Bank soil near saturation", `Soil moisture ${f1(sm, 0)}% at 30 cm.`);
    st.node_setbacks_m.forEach(s => {
      const v = d.tilt[s], j = lastIdx(v);
      if (j < 0) { add("warning", `Tilt node at ${s} m not reporting`, "No readings in the window."); return; }
      const silentH = (now - d.t[j]) / 3600000;
      const ref = v.slice(Math.max(0, j - k24), j + 1).filter(x => x != null);
      const change = ref.length ? v[j] - Math.min(...ref) : 0;
      if (silentH > th.node_silent_h) {
        add(change >= th.tilt_change_deg_24h || v[j] >= th.tilt_critical_deg ? "critical" : "info",
          change >= th.tilt_change_deg_24h || v[j] >= th.tilt_critical_deg ? `Possible bank failure at ${s} m` : `Tilt node at ${s} m silent`,
          `Last reading ${f1(silentH, 0)} h ago at ${f1(v[j])}° tilt` + (change >= th.tilt_change_deg_24h ? ` after tilting ${f1(change)}°. The bank edge has probably reached this node; inspect today.` : ". Check the node."));
      } else if (v[j] >= th.tilt_critical_deg) add("critical", `Node at ${s} m strongly tilted`, `${f1(v[j])}° tilt.`);
      else if (change >= th.tilt_change_deg_24h) add("warning", `Ground movement at ${s} m`, `Tilt increased ${f1(change)}° in 24 h.`);
      else if (change >= 1) add("watch", `Slow movement at ${s} m`, `Tilt increased ${f1(change)}° in 24 h.`);
    });
    const bv = d.battery_v[lastIdx(d.battery_v)];
    if (bv < th.battery_low_v) add("info", "Battery low", `${f1(bv, 2)} V; check the solar panel.`);
    return A.sort((a, b) => SEV[b.sev] - SEV[a.sev]);
  }

  const statusOf = A => A.length ? A[0].sev : "normal";
  const statusText = s => ({ critical: "Alert", warning: "Warning", watch: "Watch", info: "Normal", normal: "Normal" }[s]);

  // ------------------------------------------------------------------ render
  function stationCard(st) {
    const D = DATA[st.id], d = D.data, s = statusOf(D.alerts), i = lastIdx(d.level_m);
    const k24 = stepsFor(24, d), r24 = d.rain_mm.slice(-k24).reduce((a, b) => a + (b || 0), 0);
    const nodes = st.node_setbacks_m.map(m => { const j = lastIdx(d.tilt[m]); const silent = j < 0 || (Date.now() - d.t[j]) / 3600000 > CFG.thresholds.node_silent_h; return `<span title="${m} m from the edge" class="node ${silent ? "lost" : ""}">${m}</span>`; }).join("");
    return `<button class="st-card ${SEL === st.id ? "on" : ""}" data-id="${st.id}" style="--c:${STATUS_COLOR[s]}">
      <div class="st-head"><b>${esc(st.name)}</b><span class="urg" style="${SEV_STYLE[s] || "background:#c6f6d5;color:#22543d"}">${statusText(s)}</span></div>
      <div class="st-vals"><span>Level <b>${f1(d.level_m[i], 2)} m</b></span><span>Rain 24 h <b>${f1(r24, 0)} mm</b></span><span>Soil 30 cm <b>${f1(d.soil_30[lastIdx(d.soil_30)], 0)}%</b></span></div>
      <div class="st-nodes">Tilt nodes (m from edge): ${nodes}</div>
      <div class="note" style="margin:4px 0 0">Site ${esc(st.site_id)} · updated ${i >= 0 ? tlabel(d.t[i]) : "–"}</div>
    </button>`;
  }

  function renderList() {
    const el = document.getElementById("sensorList");
    el.innerHTML = CFG.stations.map(stationCard).join("");
    el.querySelectorAll(".st-card").forEach(b => b.onclick = () => { SEL = b.dataset.id; renderList(); renderDetail(); });
    const all = CFG.stations.flatMap(st => DATA[st.id].alerts.map(a => ({ ...a, name: st.name })))
      .filter(a => a.sev !== "info" || a.title === "Battery low").sort((a, b) => SEV[b.sev] - SEV[a.sev]);
    document.getElementById("sensorAlerts").innerHTML = all.length
      ? all.map(a => `<li><span class="urg" style="${SEV_STYLE[a.sev]}">${a.sev}</span> <b>${esc(a.name)}:</b> ${esc(a.title)}. <span class="note" style="margin:0">${esc(a.text)}</span></li>`).join("")
      : `<li class="note">No active alerts.</li>`;
  }

  function lineChart(id, labels, sets, yTitle, extra = {}) {
    const ctx = document.getElementById(id);
    return new Chart(ctx, {
      data: { labels, datasets: sets },
      options: { animation: false, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } } },
        scales: { x: { ticks: { maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
          y: { title: { display: true, text: yTitle }, grid: { color: "#eee" } }, ...extra } },
    });
  }

  function renderDetail() {
    CHARTS.forEach(c => c.destroy()); CHARTS = [];
    const st = CFG.stations.find(s => s.id === SEL); if (!st) return;
    const d = DATA[st.id].data, labels = d.t.map(tlabel);
    document.getElementById("sensorTitle").textContent = `${st.name} (site ${st.site_id})`;
    const pt = { pointRadius: 0, borderWidth: 1.6, tension: 0.2, spanGaps: false };
    CHARTS.push(lineChart("chLevel", labels, [
      { type: "line", label: "River level (m)", data: d.level_m, borderColor: "#2b6cb0", backgroundColor: "#2b6cb0", yAxisID: "y", ...pt },
      { type: "line", label: "Warning level", data: d.t.map(() => st.level_warning_m), borderColor: "#c53030", borderDash: [5, 4], yAxisID: "y", ...pt },
      { type: "bar", label: "Rain (mm per 15 min)", data: d.rain_mm, backgroundColor: "#90cdf4", yAxisID: "y1" },
    ], "Level (m)", { y1: { position: "right", title: { display: true, text: "Rain (mm)" }, grid: { display: false }, min: 0 } }));
    const pal = ["#c53030", "#dd6b20", "#2f855a", "#4a5568"];
    CHARTS.push(lineChart("chTilt", labels, st.node_setbacks_m.map((s, j) => (
      { type: "line", label: `${s} m from edge`, data: d.tilt[s], borderColor: pal[j % 4], backgroundColor: pal[j % 4], ...pt })), "Tilt (degrees)"));
    CHARTS.push(lineChart("chSoil", labels, [["soil_30", "30 cm", "#744210"], ["soil_60", "60 cm", "#b7791f"], ["soil_100", "100 cm", "#d69e2e"]].map(([k, n, c]) => (
      { type: "line", label: n, data: d[k], borderColor: c, backgroundColor: c, ...pt })), "Soil moisture (%)"));
  }

  function renderMap() {
    if (typeof MAP === "undefined" || !MAP) return;
    if (LAYER) MAP.removeLayer(LAYER);
    LAYER = L.layerGroup(CFG.stations.map(st => {
      const s = statusOf(DATA[st.id].alerts);
      return L.marker([st.lat, st.lon], { icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
        html: `<div class="st-pin" style="background:${STATUS_COLOR[s]}">S</div>` }) })
        .bindTooltip(`${st.name}: ${statusText(s)}`, { direction: "top" })
        .on("click", () => { SEL = st.id; renderList(); renderDetail(); document.getElementById("sensorSection").scrollIntoView({ behavior: "smooth" }); });
    }));
    const cb = document.getElementById("showSensors");
    if (!cb || cb.checked) LAYER.addTo(MAP);
    if (cb) cb.onchange = e => e.target.checked ? LAYER.addTo(MAP) : MAP.removeLayer(LAYER);
  }

  async function refresh() {
    const now = Date.now();
    for (const [idx, st] of CFG.stations.entries()) {
      let data, src = CFG.mode;
      try { data = CFG.mode === "thingspeak" ? await thingspeak(st) : simulate(st, idx, now); }
      catch (e) { data = simulate(st, idx, now); src = "simulated (ThingSpeak unavailable)"; }
      DATA[st.id] = { data, src, alerts: evaluate(st, data, now) };
    }
    const sim = Object.values(DATA).some(x => x.src.startsWith("simulated"));
    document.getElementById("sensorBanner").hidden = !sim;
    document.getElementById("sensorStamp").textContent = `Refreshed ${tlabel(now)} IST · every ${CFG.refresh_s} s`;
    renderList(); renderDetail(); renderMap();
  }

  window.initSensors = async function () {
    const sec = document.getElementById("sensorSection"); if (!sec) return;
    try { CFG = await (await fetch(CFG_URL, { cache: "no-cache" })).json(); }
    catch { sec.hidden = true; return; }
    document.getElementById("sensorNote").textContent = CFG.note || "";
    SEL = CFG.stations[0] && CFG.stations[0].id;
    await refresh();
    setInterval(refresh, (CFG.refresh_s || 60) * 1000);
  };
})();
