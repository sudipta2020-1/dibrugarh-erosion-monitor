// Live IoT sensor stations at high-risk bank erosion sites.
//
// Stations sit at the very high and high risk bank erosion sites. Each station:
// river level, rain gauge, soil moisture at 30/60/100 cm, a piezometer (pore-water
// pressure inside the bank), battery, and a line of tilt nodes set back 5, 10, 20
// and 40 m from the bank edge.
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
  const RISK_STYLE = { "Very high": "background:#9b2c2c;color:#fff", "High": "background:#dd6b20;color:#fff" };

  // What each sensor is, how it is installed and how its readings are used
  const SENSORS = [
    { icon: "↗", name: "Tilt nodes", what: "Ground tilt in degrees",
      device: "MEMS inclinometer (for example SCA3300 or ADXL355) in a sealed tube on a 1.5 m steel stake",
      how: "Four nodes per station, staked 5, 10, 20 and 40 m back from the bank edge. Each node reads every 15 minutes and sends by LoRa radio. A crack opening behind the edge tilts the stake before the block falls.",
      rule: "Tilt rise of 3° or more in 24 h: warning. Tilt of 10° or more: critical. A tilted node that stops reporting for 2 h: possible bank failure." },
    { icon: "◎", name: "Piezometer", what: "Pore-water pressure inside the bank, in kPa",
      device: "Vibrating-wire piezometer with a data logger",
      how: "Placed in a borehole about 3 m deep and 10 m behind the edge, sealed with bentonite. It shows how much water is held in the bank. The reading is compared with the pressure the river level alone would give.",
      rule: "Pressure 10 kPa or more above the river-level pressure: warning. This happens when the river falls faster than the bank drains, the usual moment of collapse. Rise of 5 kPa or more in 24 h: watch." },
    { icon: "≋", name: "Soil moisture probes", what: "Volumetric water content in %",
      device: "Capacitive or TDR soil moisture probes",
      how: "Three probes at 30, 60 and 100 cm depth near the piezometer. They show rain soaking into the bank and how deep it has reached.",
      rule: "45% or more at 30 cm: watch (bank soil near saturation)." },
    { icon: "☂", name: "Rain gauge", what: "Rainfall in mm",
      device: "Tipping-bucket rain gauge, 0.2 mm per tip",
      how: "Mounted on the gateway pole, clear of trees. Rain adds weight to the bank and raises soil moisture and pore pressure.",
      rule: "64.5 mm or more in 24 h (IMD heavy rain): warning." },
    { icon: "≈", name: "River level sensor", what: "Water level in metres",
      device: "Non-contact radar level sensor",
      how: "Fixed to a pole, jetty or bridge over the water, pointing down. It measures the distance to the water surface every 15 minutes. Where a CWC gauge is close, its level can be used as a check.",
      rule: "Fall of more than 0.5 m in 6 h: warning (rapid drawdown). Rise of more than 1 m in 6 h: watch. Above the station warning level: warning." },
    { icon: "⌁", name: "Gateway and power", what: "Data link and battery voltage",
      device: "Solar-powered LoRa gateway with a 4G or NB-IoT link",
      how: "Mounted above the highest flood level. It collects the node readings and sends them to ThingSpeak or a server every 15 minutes. Nodes at the edge are cheap and expected to be lost with the bank.",
      rule: "Battery below 3.5 V: information (check the solar panel)." },
  ];

  function showSensorInfo(stId) {
    let ov = document.getElementById("sensorInfo");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "sensorInfo"; ov.className = "si-overlay"; ov.setAttribute("role", "dialog"); ov.setAttribute("aria-modal", "true");
      document.body.appendChild(ov);
      ov.addEventListener("click", e => { if (e.target === ov || e.target.closest(".si-close")) ov.hidden = true; });
      document.addEventListener("keydown", e => { if (e.key === "Escape") ov.hidden = true; });
    }
    const st = CFG.stations.find(s => s.id === stId);
    const D = st && DATA[st.id], d = D && D.data;
    const now = d ? {
      "Tilt nodes": st.node_setbacks_m.map(m => { const j = lastIdx(d.tilt[m]); return `${m} m: ${j < 0 ? "–" : f1(d.tilt[m][j]) + "°"}`; }).join(", "),
      "Piezometer": d.pore_kpa ? `${f1(d.pore_kpa[lastIdx(d.pore_kpa)], 1)} kPa` : "–",
      "Soil moisture probes": `30 cm ${f1(d.soil_30[lastIdx(d.soil_30)], 0)}%, 60 cm ${f1(d.soil_60[lastIdx(d.soil_60)], 0)}%, 100 cm ${f1(d.soil_100[lastIdx(d.soil_100)], 0)}%`,
      "Rain gauge": `${f1(d.rain_mm.slice(-stepsFor(24, d)).reduce((a, b) => a + (b || 0), 0), 0)} mm in 24 h`,
      "River level sensor": `${f1(d.level_m[lastIdx(d.level_m)], 2)} m (warning ${st.level_warning_m} m)`,
      "Gateway and power": `${f1(d.battery_v[lastIdx(d.battery_v)], 2)} V`,
    } : {};
    ov.innerHTML = `<div class="si-box">
      <button class="si-close" aria-label="Close">×</button>
      <p class="eyebrow">Sensor station design</p>
      <h2>${st ? esc(st.name) : "Sensors used at each station"}</h2>
      ${st ? `<p class="si-meta"><span class="urg" style="${RISK_STYLE[st.risk] || ""}">${esc(st.risk || "")} risk</span> Site ${esc(st.site_id)} · ${esc(st.site_class || "")}${st.people_500m ? ` · about ${st.people_500m.toLocaleString("en-IN")} people within 500 m` : ""}</p>` :
        `<p class="si-meta">Stations are placed only at very high risk (immediate action) and high risk (before monsoon) bank erosion sites.</p>`}
      <div class="si-grid">${SENSORS.map(s => `<div class="si-item">
        <div class="si-h"><span class="si-ic">${s.icon}</span><b>${s.name}</b><span class="note" style="margin:0">${s.what}</span></div>
        ${now[s.name] ? `<div class="si-now">Now: <b>${esc(now[s.name])}</b></div>` : ""}
        <p><b>Sensor:</b> ${s.device}.</p><p><b>How it is used:</b> ${s.how}</p><p><b>Alert rule:</b> ${s.rule}</p></div>`).join("")}</div>
      <p class="note">Readings every 15 minutes. ${D && D.src.startsWith("simulated") ? "Values shown are simulated until hardware is installed." : ""}</p>
    </div>`;
    ov.hidden = false;
    ov.querySelector(".si-close").focus();
  }
  window.showSensorInfo = showSensorInfo;

  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const f1 = (v, d = 1) => (v == null || !isFinite(v)) ? "–" : Number(v).toFixed(d);
  const tlabel = t => new Date(t).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

  // ---------------------------------------------------------------- simulator
  function rng(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const blockRand = (seed, b) => rng(seed * 100003 + b)();

  function simulate(st, idx, now) {
    const dt = STEP_MIN * 60000, n = Math.round(CFG.history_days * 24 * 60 / STEP_MIN);
    const t0 = Math.floor(now / dt) * dt - (n - 1) * dt, seed = 17 + idx * 31;
    const out = { t: [], level_m: [], rain_mm: [], soil_30: [], soil_60: [], soil_100: [], pore_kpa: [], battery_v: [], tilt: {} };
    st.node_setbacks_m.forEach(s => out.tilt[s] = []);
    let store = 0, s30 = 0, s60 = 0, s100 = 0, head = null;
    const tip = st.piezo_tip_m ?? st.level_warning_m - 5;
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
      // Pore-water head in the bank: fills quickly when the river rises, drains slowly
      if (head === null) head = level;
      head += (level - head) * (level > head ? 0.08 : 0.012) + 0.004 * rain;
      out.t.push(t);
      out.level_m.push(+level.toFixed(3));
      out.pore_kpa.push(+(9.81 * (head - tip)).toFixed(1));
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
    const out = { t: [], level_m: [], rain_mm: [], soil_30: [], soil_60: [], soil_100: [], pore_kpa: [], battery_v: [], tilt: {} };
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
    const ip = lastIdx(d.pore_kpa || []);
    if (ip >= 0 && i >= 0) {
      const tip = st.piezo_tip_m ?? st.level_warning_m - 5, excess = d.pore_kpa[ip] - 9.81 * (d.level_m[i] - tip);
      const refP = d.pore_kpa.slice(Math.max(0, ip - k24), ip + 1).filter(x => x != null), riseP = d.pore_kpa[ip] - Math.min(...refP);
      if (excess >= (th.pore_excess_kpa ?? 10)) add("warning", "High water pressure inside the bank", `Pore-water pressure ${f1(d.pore_kpa[ip])} kPa is ${f1(excess)} kPa above the river level. The bank is draining more slowly than the river is falling, which weakens it.`);
      else if (riseP >= (th.pore_rise_kpa_24h ?? 5)) add("watch", "Pore-water pressure rising", `Up ${f1(riseP)} kPa in 24 h.`);
    }
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
      <div class="st-risk">${st.risk ? `<span class="urg" style="${RISK_STYLE[st.risk] || ""}">${esc(st.risk)} risk</span>` : ""} <span>${esc(st.site_class || "")}</span><span class="si-link" data-info="${st.id}" role="button" tabindex="0">Sensors ⓘ</span></div>
      <div class="st-vals"><span>Level <b>${f1(d.level_m[i], 2)} m</b></span><span>Rain 24 h <b>${f1(r24, 0)} mm</b></span><span>Soil 30 cm <b>${f1(d.soil_30[lastIdx(d.soil_30)], 0)}%</b></span><span>Pore pressure <b>${d.pore_kpa ? f1(d.pore_kpa[lastIdx(d.pore_kpa)], 0) : "–"} kPa</b></span></div>
      <div class="st-nodes">Tilt nodes (m from edge): ${nodes}</div>
      <div class="note" style="margin:4px 0 0">Site ${esc(st.site_id)} · updated ${i >= 0 ? tlabel(d.t[i]) : "–"}</div>
    </button>`;
  }

  function renderList() {
    const el = document.getElementById("sensorList");
    el.innerHTML = CFG.stations.map(stationCard).join("");
    el.querySelectorAll(".st-card").forEach(b => b.onclick = e => {
      const info = e.target.closest(".si-link"); if (info) { showSensorInfo(info.dataset.info); return; }
      SEL = b.dataset.id; renderList(); renderDetail(); });
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
    if (document.getElementById("chPore") && d.pore_kpa) {
      const tip = st.piezo_tip_m ?? st.level_warning_m - 5;
      CHARTS.push(lineChart("chPore", labels, [
        { type: "line", label: "Pore-water pressure in the bank (kPa)", data: d.pore_kpa, borderColor: "#6b46c1", backgroundColor: "#6b46c1", ...pt },
        { type: "line", label: "Pressure from river level alone (kPa)", data: d.level_m.map(v => v == null ? null : +(9.81 * (v - tip)).toFixed(1)), borderColor: "#2b6cb0", borderDash: [5, 4], backgroundColor: "#2b6cb0", ...pt },
      ], "Pressure (kPa)"));
    }
  }

  function renderMap() {
    if (typeof MAP === "undefined" || !MAP) return;
    if (LAYER) MAP.removeLayer(LAYER);
    LAYER = L.layerGroup(CFG.stations.map(st => {
      const s = statusOf(DATA[st.id].alerts);
      return L.marker([st.lat, st.lon], { icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
        html: `<div class="st-pin" style="background:${STATUS_COLOR[s]}">S</div>` }) })
        .bindTooltip(`${st.name}: ${statusText(s)}`, { direction: "top" })
        .bindPopup(() => {
          const D = DATA[st.id], d = D.data, al = D.alerts.filter(a => a.sev !== "info");
          return `<div class="st-pop"><b>${esc(st.name)}</b><br><span class="urg" style="${RISK_STYLE[st.risk] || ""}">${esc(st.risk || "")} risk</span> ${esc(st.site_class || "")} · site ${esc(st.site_id)}
            <table><tr><td>River level</td><td><b>${f1(d.level_m[lastIdx(d.level_m)], 2)} m</b></td></tr>
            <tr><td>Rain, 24 h</td><td><b>${f1(d.rain_mm.slice(-stepsFor(24, d)).reduce((a, b) => a + (b || 0), 0), 0)} mm</b></td></tr>
            <tr><td>Soil moisture 30 cm</td><td><b>${f1(d.soil_30[lastIdx(d.soil_30)], 0)}%</b></td></tr>
            <tr><td>Pore pressure</td><td><b>${d.pore_kpa ? f1(d.pore_kpa[lastIdx(d.pore_kpa)], 1) : "–"} kPa</b></td></tr>
            <tr><td>Tilt (${st.node_setbacks_m.join("/")} m)</td><td><b>${st.node_setbacks_m.map(m => { const j = lastIdx(d.tilt[m]); return j < 0 ? "–" : f1(d.tilt[m][j]) + "°"; }).join(" / ")}</b></td></tr></table>
            ${al.length ? `<div class="st-pop-al">${esc(al[0].title)}</div>` : ""}
            <button class="si-btn" onclick="showSensorInfo('${st.id}')">Sensors used and how they work</button></div>`;
        }, { maxWidth: 280 })
        .on("click", () => { SEL = st.id; renderList(); renderDetail(); });
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
    // Extra controls: pore-pressure chart and the sensor information button
    const soil = document.getElementById("chSoil");
    if (soil && !document.getElementById("chPore")) {
      const box = document.createElement("div"); box.className = "chart-box"; box.style.cssText = "height:150px;margin-top:10px";
      box.innerHTML = '<canvas id="chPore"></canvas>'; soil.parentNode.after(box);
      const p = box.nextElementSibling;
      if (p && p.classList.contains("note")) p.textContent = "Each station has a radar river level sensor, a rain gauge, soil moisture probes at 30, 60 and 100 cm, a piezometer about 3 m deep for the water pressure inside the bank, and tilt nodes on stakes 5, 10, 20 and 40 m back from the edge. When the river falls faster than the bank drains, the pressure inside the bank stays high and the bank is most likely to fail.";
    }
    const head = sec.querySelector(".card-head");
    const h2 = head && head.querySelector("h2");
    if (h2) h2.textContent = "Live sensor stations at very high and high risk sites (pilot)";
    if (head && !head.querySelector(".si-btn")) {
      const b = document.createElement("button"); b.className = "si-btn"; b.type = "button"; b.textContent = "Sensors used and how they work";
      b.onclick = () => showSensorInfo(null); head.insertBefore(b, head.lastElementChild);
    }
    SEL = CFG.stations[0] && CFG.stations[0].id;
    await refresh();
    setInterval(refresh, (CFG.refresh_s || 60) * 1000);
  };
})();
