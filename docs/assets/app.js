// Dibrugarh soil erosion dashboard. Reads the files written by pipeline/publish.py.
const DATA = "data/";
const CLASS_COLOR = {
  "Bank erosion (stable land to water)": "#d7191c", "Char or sandbar lost (within river belt)": "#f59ec0",
  "Accretion (water to land)": "#2c7bb6", "New inland water (pond or flooding)": "#66c2a5",
  "Vegetation loss": "#fdae61", "New bare soil": "#8c510a",
  "Very high": "#d73027", "High": "#fc8d59",
  "Likely bank erosion (AI prediction)": "#7b3294",
};
const CONF_COLOR = { "High": "#1a7f37", "Medium": "#b7791f", "Low": "#c53030", "Not assessed": "#8a8a80", "Not validated": "#8a8a80" };
const URG_STYLE = { "Immediate": "background:#c53030;color:#fff", "Before monsoon": "background:#f6ad55;color:#3b2200",
  "Routine": "background:#e2e8f0;color:#2d3748", "Monitor": "background:#edf2f7;color:#718096" };
const FIELD_COLOR = { "Confirmed": "#1a7f37", "Not confirmed": "#c53030", "Unsure": "#b7791f" };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const HIST_COLOR = { "Land to permanent water": "#7a0177", "Land to seasonal water": "#dd3497",
  "Permanent water to land": "#225ea8", "Seasonal water to land": "#41b6c4" };
const RAMPS = {
  YlOrRd: "linear-gradient(90deg,#ffffcc,#fed976,#fd8d3c,#e31a1c,#800026)",
  RdYlGn: "linear-gradient(90deg,#a50026,#f46d43,#fee08b,#a6d96a,#006837)",
  viridis: "linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)",
};
const fmt = (v, d = 0) => (v == null || isNaN(v)) ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const shortName = s => s.replace(/ \(.*\)$/, "");

async function getJSON(f) { const r = await fetch(DATA + f, { cache: "no-cache" }); if (!r.ok) throw new Error(f); return r.json(); }
async function getText(f) { const r = await fetch(DATA + f, { cache: "no-cache" }); if (!r.ok) throw new Error(f); return r.text(); }
function parseCSV(t) {
  const [h, ...rows] = t.trim().split(/\r?\n/);
  const keys = h.split(",");
  return rows.map(r => { const v = r.split(","); const o = {}; keys.forEach((k, i) => o[k] = isNaN(v[i]) || v[i] === "" ? v[i] : Number(v[i])); return o; });
}

async function main() {
  let summary;
  try { summary = await getJSON("summary.json"); }
  catch {
    document.getElementById("meta").textContent = "";
    document.querySelector(".grid").innerHTML = `<div class="card empty">No monitoring run has been published yet. Start the "Erosion monitoring run" workflow from the repository's Actions tab; the dashboard fills in automatically when it finishes.</div>`;
    return;
  }
  const [layers, aoi, hot, histTxt] = await Promise.all([
    getJSON("layers.json"), getJSON("aoi.geojson"),
    getJSON("hotspots.geojson").catch(() => ({ type: "FeatureCollection", features: [] })),
    getText("history.csv").catch(() => ""),
  ]);
  const history = histTxt ? parseCSV(histTxt) : [];
  header(summary); kpis(summary); inputsNote(summary); accuracy(summary); mlCard(summary); mapView(layers, aoi, hot); riskChart(summary); trendChart(history);
  table(hot); downloads(summary); fieldCard(summary);
}

function header(s) {
  const p = (a) => `${a[0]} to ${a[1]}`;
  document.getElementById("meta").innerHTML =
    `Last update <b>${s.run_date}</b><br>Current window <b>${p(s.period_current)}</b> · Baseline <b>${p(s.period_baseline)}</b>`;
  if (s.confidence === "low") {
    const b = document.getElementById("banner");
    b.hidden = false;
    b.textContent = `Low confidence run: only ${fmt(s.clear_coverage_pct, 1)}% of the district had a cloud-free optical view in the current window (typical in the monsoon). Water change still uses radar, but vegetation and soil loss results are less reliable.`;
  }
}

function inputsNote(s) {
  const el = document.getElementById("inputsNote");
  const ri = s.rusle_inputs;
  if (!el || !ri) return;
  const rep = s.inputs_report || {};
  const imd = rep.rainfall_imd, cx = rep.rainfall_crosscheck;
  let t = `RUSLE inputs in this run: rainfall from ${ri.rainfall}`;
  if (imd && imd.years) t += ` (${imd.years[0]}–${imd.years[1]})`;
  t += `, district mean ${fmt(ri.mean_rainfall_mm)} mm/yr, mean R ${fmt(ri.mean_R)}`;
  if (cx) t += `; CHIRPS cross-check ${fmt(cx.chirps_mm)} mm (${cx.difference_pct > 0 ? "+" : ""}${cx.difference_pct}%)`;
  t += `. Soil erodibility from ${ri.soil_k}, mean K ${ri.mean_K}. Surface DEM smoothed over ${ri.dem_smoothing_m} m to remove canopy noise.`;
  if (/placeholder/.test(ri.rainfall + ri.soil_k)) t += " Values marked as placeholder mean the input layer has not been prepared yet, so soil loss is relative only.";
  t += " All results need field confirmation before conservation works are planned.";
  el.textContent = t;
}

function accuracy(s) {
  const el = document.getElementById("accBody");
  if (!el) return;
  const a = s.accuracy, v = s.validation;
  const per = x => `${x[0]} to ${x[1]}`;
  if (!a || a.status !== "ok") {
    if (!v) { el.innerHTML = `<p class="note">The validation sample is drawn by the next monitoring run.</p>`; return; }
    const lab = a && a.status === "too_few_labels" ? ` ${a.labelled} points labelled so far; at least 20 are needed.` : "";
    el.innerHTML = `<p>A stratified random sample of <b>${v.sample_points}</b> points was drawn for the window ${per(v.period_current)} (baseline ${per(v.period_baseline)}).${lab} Each point is labelled blind on the <a href="validate.html">labelling page</a>. Once the labels are added to the repository as <code>data/validation_labels.csv</code>, a run with end date <b>${v.period_current[1]}</b> reports map accuracy and corrected areas with 95% confidence intervals here.</p>`;
    return;
  }
  const pct = (x, d = 0) => x == null ? "–" : `${(100 * x).toFixed(d)}%`;
  const rows = Object.entries(a.per_class).map(([k, r]) => `<tr><td>${k}</td>
    <td class="num">${pct(r.users_accuracy)} ± ${pct(r.users_ci95)}</td>
    <td class="num">${pct(r.producers_accuracy)} ± ${pct(r.producers_ci95)}</td>
    <td class="num">${fmt(r.mapped_area_ha)}</td>
    <td class="num">${fmt(r.estimated_area_ha)} ± ${fmt(r.estimated_area_ci95_ha)}</td></tr>`).join("");
  el.innerHTML = `<p>Overall accuracy <b>${pct(a.overall_accuracy, 1)} ± ${pct(a.overall_ci95, 1)}</b> from ${a.labelled} labelled points, for the window ${per(a.period_current)}. User's accuracy is the share of mapped area that is correct; producer's accuracy is the share of the true area that the map found. Estimated areas correct the mapped areas for the errors found in the sample.</p>
  <div class="table-wrap"><table><thead><tr><th>Class</th><th class="num">User's accuracy</th><th class="num">Producer's accuracy</th><th class="num">Mapped (ha)</th><th class="num">Estimated (ha, 95% CI)</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function mlCard(s) {
  const el = document.getElementById("mlBody");
  if (!el) return;
  const m = s.ml_model;
  if (!m) { el.innerHTML = `<p class="note">The model is trained by the next monitoring run.</p>`; return; }
  const pct = x => x == null ? "–" : `${(100 * x).toFixed(0)}%`;
  const rows = Object.entries(m.metrics).map(([k, r]) => `<tr><td>${k}</td>
    <td class="num">${r.roc_auc.toFixed(2)}</td><td class="num">${r.pr_auc.toFixed(2)}</td>
    <td class="num">${pct(r.captured_top10pct)}</td><td class="num">${pct(r.captured_top20pct)}</td></tr>`).join("");
  el.innerHTML = `<p>The model learns where the river took land between the two windows, from conditions at the start (${fmt(m.training_pixels)} sample pixels, ${pct(m.positive_share)} with bank loss). Scores are out-of-sample: ${m.cv}. ROC AUC of 0.5 means no skill. "Captured" is the share of the land actually lost that falls within the 10% or 20% of land the method ranks highest, which is what matters when field teams can visit only a few sites.</p>
    <div class="table-wrap"><table><thead><tr><th>Method</th><th class="num">ROC AUC</th><th class="num">PR AUC</th><th class="num">Captured, top 10%</th><th class="num">Captured, top 20%</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="note">RUSLE estimates sheet and rill erosion from rain on slopes; it is included to show that it does not describe riverbank loss, which needs its own model.</p>`;
  const imp = (m.feature_importance || []).slice(0, 8);
  if (imp.length) new Chart(document.getElementById("impChart"), {
    type: "bar",
    data: { labels: imp.map(x => x[0]), datasets: [{ data: imp.map(x => x[1]), backgroundColor: "#7b3294", borderRadius: 3 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => `${(100 * c.raw).toFixed(1)}% of importance` } } },
      scales: { x: { title: { display: true, text: "Share of importance (RF and XGBoost mean)" }, grid: { color: "#eee" } }, y: { grid: { display: false } } } },
  });
}

function kpis(s) {
  const r = s.risk_class_area_ha, c = s.change_area_ha;
  const total = Object.values(r).reduce((a, b) => a + b, 0) || 1;
  const hi = (r["High"] || 0) + (r["Very high"] || 0);
  const ch = k => c[Object.keys(c).find(x => x.startsWith(k))] || 0;
  const items = [
    ["District area", fmt(s.district_area_ha / 100), "km²", "#9a9a90"],
    ...(s.sites_by_urgency && s.sites_by_urgency["Immediate"] != null ? [["Sites for immediate action", fmt(s.sites_by_urgency["Immediate"]), `of ${fmt(s.hotspots_listed)} priority sites; ${fmt(s.sites_by_urgency["Before monsoon"])} more before the monsoon`, "#c53030"]] : []),
    ["High + very high risk", fmt(hi), `ha · ${fmt(100 * hi / total, 1)}% of land`, "#d73027"],
    ["Bank erosion", fmt(ch("Bank erosion")), "ha of stable land lost to water", "#d7191c"],
    ["Accretion", fmt(ch("Accretion")), "ha of new land", "#2c7bb6"],
    ...(Object.keys(c).some(x => x.startsWith("Char")) ? [["Char / sandbar loss", fmt(ch("Char")), "ha inside the river belt", "#f59ec0"]] : []),
    ["Vegetation loss", fmt(ch("Vegetation loss")), "ha", "#fdae61"],
    ["Mean soil loss", fmt(s.mean_soil_loss_t_ha_yr, 1), "t/ha/yr", "#8c510a"],
  ];
  const ex = s.exposure || {};
  if (ex.people_near_bank_erosion != null)
    items.splice(3, 0, ["People near active bank erosion", fmt(ex.people_near_bank_erosion), `within ${ex.buffer_m || 500} m (WorldPop 2020)`, "#b2182b"]);
  if (s.history_area_ha) {
    const h = s.history_area_ha;
    const lost = (h["Land to permanent water"] || 0) + (h["Land to seasonal water"] || 0);
    items.splice(3, 0, ["Land lost to river 1984–2021", fmt(lost), "ha (JRC Landsat record)", "#7a0177"]);
  }
  document.getElementById("kpis").innerHTML = items.map(([l, v, sub, col]) =>
    `<div class="kpi" style="--c:${col}"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");
}

let MAP, HOTLAYER;
function mapView(layers, aoi, hot) {
  MAP = L.map("map", { zoomSnap: 0.25 });
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Imagery © Esri", maxZoom: 18 }).addTo(MAP);
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 18 });
  L.control.layers({ "Satellite basemap": sat, "Street map": osm }, null, { position: "topright" }).addTo(MAP);

  const boundary = L.geoJSON(aoi, { style: { color: "#ffd400", weight: 2, fill: false } }).addTo(MAP);
  MAP.fitBounds(boundary.getBounds(), { padding: [10, 10] });

  const sel = document.getElementById("layerSelect"), op = document.getElementById("opacity");
  const overlays = {};
  const order = ["change", "ai", "confidence", "agreement", "history", "risk", "soilloss", "ndvi", "truecolour"];
  order.filter(k => layers[k]).forEach(k => {
    overlays[k] = L.imageOverlay(DATA + layers[k].file, layers[k].bounds, { opacity: op.value / 100 });
    sel.add(new Option(layers[k].title, k));
  });
  let active = null;
  function show(k) {
    if (active) MAP.removeLayer(active);
    active = overlays[k].addTo(MAP); active.setOpacity(op.value / 100);
    boundary.bringToFront(); if (HOTLAYER && MAP.hasLayer(HOTLAYER)) HOTLAYER.bringToFront();
    legend(layers[k]);
  }
  sel.onchange = () => show(sel.value);
  op.oninput = () => active && active.setOpacity(op.value / 100);
  show(sel.value);

  HOTLAYER = L.geoJSON(hot, {
    style: f => ({ color: CLASS_COLOR[f.properties.class] || "#333", weight: 2, fillOpacity: 0.15 }),
    onEachFeature: (f, l) => l.bindPopup(popup(f.properties)),
  });
  const markers = L.layerGroup(hot.features.map(f => {
    const p = f.properties;
    return L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 1.5, fillColor: CLASS_COLOR[p.class] || "#333", fillOpacity: 1 })
      .bindPopup(popup(p)).bindTooltip(`#${p.rank}`, { direction: "top" });
  }));
  HOTLAYER.addLayer(markers);
  HOTLAYER.addTo(MAP);
  document.getElementById("showHot").onchange = e => e.target.checked ? HOTLAYER.addTo(MAP) : MAP.removeLayer(HOTLAYER);
}

function popup(p) {
  return `<b>Rank ${p.rank}: ${shortName(p.class)}</b><br>${fmt(p.area_ha, 2)} ha` +
    (p.mean_soil_loss_t_ha_yr != null && !isNaN(p.mean_soil_loss_t_ha_yr) ? `<br>Soil loss ${fmt(p.mean_soil_loss_t_ha_yr, 1)} t/ha/yr` : "") +
    (p.max_retreat_m != null && !isNaN(p.max_retreat_m) ? `<br>Bank retreat up to ${fmt(p.max_retreat_m)} m (${fmt(p.retreat_m_per_yr, 1)} m/yr)` : "") +
    (p.mean_probability != null && !isNaN(p.mean_probability) ? `<br>AI probability ${fmt(100 * p.mean_probability)}%, model agreement ${fmt(100 * p.model_agreement)}%` : "") +
    (p.confidence ? `<br>Confidence: <b>${p.confidence}</b>` : "") +
    (p.population != null && !isNaN(p.population) ? `<br>Within 500 m: ${fmt(p.population)} people, ${fmt(p.built_ha, 1)} ha built-up, ${fmt(p.crop_ha, 1)} ha cropland, ${fmt(p.road_km, 1)} km road, ${fmt(p.facilities)} schools/health facilities` : "") +
    (p.nearest_place ? `<br>Nearest place: ${p.nearest_place} (${fmt(p.nearest_place_km, 1)} km)` : "") +
    (p.recommended_action ? `<div class="popup-action"><b>${esc(p.urgency)}</b> · ${esc(p.lead_agency)}<br>${esc(p.recommended_action)}</div>` : "") +
    (p.field_status ? `<br>Field check: <b>${esc(p.field_status)}</b> (${esc(p.field_date)})` : (p.site_id ? `<br>Site ID for the field form: <b>${esc(p.site_id)}</b>` : "")) +
    `<br>${p.lat}, ${p.lon}<br><a target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}">Directions</a>`;
}

function legend(l) {
  const el = document.getElementById("legend");
  if (l.legend) el.innerHTML = l.legend.map(([n, c]) => `<span><i class="sw" style="background:${c}"></i>${shortName(n)}</span>`).join("");
  else if (l.ramp) el.innerHTML = `<div><div>${l.title}</div><div class="ramp" style="background:${RAMPS[l.ramp.cmap]}"></div><div class="ramp-labels">${l.ramp.labels.map(x => `<span>${x}</span>`).join("")}</div></div>`;
  else el.innerHTML = `<span>${l.title}</span>`;
}

Chart.defaults.font.family = "'Source Sans 3', system-ui, sans-serif";
Chart.defaults.color = "#6b6a62";

function riskChart(s) {
  const labels = Object.keys(s.risk_class_area_ha);
  new Chart(document.getElementById("riskChart"), {
    type: "bar",
    data: { labels, datasets: [{ data: labels.map(k => s.risk_class_area_ha[k]),
      backgroundColor: ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"], borderRadius: 4 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => `${fmt(c.raw)} ha` } } },
      scales: { x: { title: { display: true, text: "Area (ha)" }, grid: { color: "#eee" } }, y: { grid: { display: false } } } },
  });
}

function trendChart(h) {
  const note = document.getElementById("trendNote");
  if (!h.length) { note.textContent = "The trend appears after the first run."; return; }
  const series = [
    ["chg_bank_erosion_ha", "Bank erosion", "#d7191c"], ["chg_char_or_sandbar_lost_ha", "Char / sandbar loss", "#f59ec0"],
    ["chg_accretion_ha", "Accretion", "#2c7bb6"],
    ["chg_vegetation_loss_ha", "Vegetation loss", "#fdae61"], ["risk_very_high_ha", "Very high risk", "#7f0000"],
  ];
  new Chart(document.getElementById("trendChart"), {
    type: "line",
    data: { labels: h.map(r => r.current_end), datasets: series.map(([k, n, c]) => ({
      label: n, data: h.map(r => r[k]), borderColor: c, backgroundColor: c, tension: 0.2, pointRadius: 3,
      segment: { borderDash: ctx => h[ctx.p1DataIndex]?.confidence === "low" ? [4, 4] : undefined } })) },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw)} ha` } } },
      scales: { y: { title: { display: true, text: "Area (ha)" }, grid: { color: "#eee" } },
        x: { title: { display: true, text: "End of monitoring window" }, grid: { display: false } } } },
  });
  note.textContent = h.length === 1
    ? "Only one run so far; each monthly run adds a point. Dashed segments mark low-confidence runs."
    : "Each point compares one monitoring window with the same dates in the baseline year. Dashed segments mark low-confidence runs.";
}

function table(hot) {
  const tb = document.querySelector("#hotTable tbody");
  if (!hot.features.length) { tb.innerHTML = `<tr><td colspan="11">No priority sites in this run.</td></tr>`; return; }
  tb.innerHTML = hot.features.map(f => f.properties).sort((a, b) => a.rank - b.rank).map(p => `
    <tr data-lat="${p.lat}" data-lon="${p.lon}">
      <td>${p.rank}</td>
      <td><span class="tag"><i style="background:${CLASS_COLOR[p.class] || "#333"}"></i>${shortName(p.class)}</span></td>
      <td><span class="tag"><i style="background:${CONF_COLOR[p.confidence] || "#8a8a80"}"></i>${p.confidence || "–"}</span></td>
      <td>${p.urgency ? `<span class="urg" style="${URG_STYLE[p.urgency] || ""}" title="${esc(p.lead_agency)}: ${esc(p.recommended_action)}">${esc(p.urgency)}</span>` : "–"}</td>
      <td class="num">${fmt(p.area_ha, 2)}</td>
      <td class="num">${p.retreat_m_per_yr != null && !isNaN(p.retreat_m_per_yr) ? fmt(p.retreat_m_per_yr, 1) : "–"}</td>
      <td class="num">${p.population != null && !isNaN(p.population) ? fmt(p.population) : "–"}</td>
      <td>${p.nearest_place ? `${p.nearest_place} (${fmt(p.nearest_place_km, 1)} km)` : "–"}</td>
      <td>${p.field_status ? `<span class="tag"><i style="background:${FIELD_COLOR[p.field_status] || "#8a8a80"}"></i>${esc(p.field_status)}</span>` : `<span class="note" style="margin:0">${esc(p.site_id || "–")}</span>`}</td>
      <td class="num">${fmt(p.priority_score, 2)}</td>
      <td><a target="_blank" rel="noopener" href="https://www.google.com/maps?q=${p.lat},${p.lon}" onclick="event.stopPropagation()">${p.lat}, ${p.lon}</a></td>
    </tr>`).join("");
  tb.querySelectorAll("tr").forEach(tr => tr.onclick = () => {
    MAP.flyTo([+tr.dataset.lat, +tr.dataset.lon], 14, { duration: 0.8 });
    document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function fieldCard(s) {
  const links = [["field/erosion_field_survey.xlsx", "Survey form (XLSForm)"], ["field/priority_sites.csv", "Site list for the form"]]
    .map(([f, n]) => `<a href="${DATA}${f}" download>${n}</a>`);
  document.getElementById("fieldLinks").innerHTML = links.join("");
  const el = document.getElementById("fieldBody");
  const f = s.field || {};
  const u = s.sites_by_urgency || {};
  const urg = Object.keys(URG_STYLE).map(k => `<dt><span class="urg" style="${URG_STYLE[k]}">${k}</span></dt><dd>${fmt(u[k] || 0)}</dd>`).join("");
  if (!f.records) {
    el.innerHTML = `<p class="note" style="margin:0 0 8px">Priority sites by recommended urgency</p><dl class="stat-list">${urg}</dl>
      <p class="note">No field records yet. When records are added, this panel shows how many visited sites were confirmed, which is the test of how useful the ranking is.</p>`;
    return;
  }
  const pct = x => x == null ? "–" : `${fmt(100 * x)}%`;
  const byType = Object.entries(f.by_type || {}).map(([t, v]) => `<dt>${esc(shortName(t))}</dt><dd>${v.confirmed} of ${v.visited}</dd>`).join("");
  el.innerHTML = `<dl class="stat-list">
      <dt>Field records</dt><dd>${fmt(f.records)}</dd>
      <dt>Priority sites visited</dt><dd>${fmt(f.sites_visited)}</dd>
      <dt>Confirmed in the field</dt><dd>${fmt(f.sites_confirmed)}</dd>
      <dt>Not confirmed (false alarms)</dt><dd>${fmt(f.sites_not_confirmed)}</dd>
      <dt>Confirmation rate</dt><dd>${pct(f.confirmation_rate)}</dd>
      <dt>Officer says map matches</dt><dd>${pct(f.map_agreement_rate)}</dd>
      <dt>New sites reported</dt><dd>${fmt(f.new_sites_reported)}</dd></dl>
    ${byType ? `<p class="note" style="margin:10px 0 4px">Confirmed by site type</p><dl class="stat-list">${byType}</dl>` : ""}
    <p class="note" style="margin:10px 0 4px">Priority sites by recommended urgency</p><dl class="stat-list">${urg}</dl>`;
}

function downloads(s) {
  const links = [["hotspots.csv", "Priority sites (CSV)"], ["hotspots.geojson", "Priority sites (GeoJSON)"], ["history.csv", "Run history (CSV)"]]
    .map(([f, n]) => `<a href="${DATA}${f}" download>${n}</a>`);
  links.push(`<a href="validate.html">Label validation points</a>`);
  if (s.repository) links.push(`<a target="_blank" rel="noopener" href="https://github.com/${s.repository}/releases/latest">Full-resolution GeoTIFFs</a>`);
  document.getElementById("downloads").innerHTML = links.join("");
}

main();
