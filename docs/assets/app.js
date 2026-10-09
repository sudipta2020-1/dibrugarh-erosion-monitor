// Dibrugarh soil erosion dashboard. Reads the files written by pipeline/publish.py.
const DATA = "data/";
const CLASS_COLOR = {
  "Bank erosion (land to water)": "#d7191c", "Accretion (water to land)": "#2c7bb6",
  "Vegetation loss": "#fdae61", "New bare soil": "#8c510a",
  "Very high": "#d73027", "High": "#fc8d59",
};
const RAMPS = {
  YlOrRd: "linear-gradient(90deg,#ffffcc,#fed976,#fd8d3c,#e31a1c,#800026)",
  RdYlGn: "linear-gradient(90deg,#a50026,#f46d43,#fee08b,#a6d96a,#006837)",
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
  header(summary); kpis(summary); mapView(layers, aoi, hot); riskChart(summary); trendChart(history);
  table(hot); downloads(summary);
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

function kpis(s) {
  const r = s.risk_class_area_ha, c = s.change_area_ha;
  const total = Object.values(r).reduce((a, b) => a + b, 0) || 1;
  const hi = (r["High"] || 0) + (r["Very high"] || 0);
  const ch = k => c[Object.keys(c).find(x => x.startsWith(k))] || 0;
  const items = [
    ["District area", fmt(s.district_area_ha / 100), "km²", "#9a9a90"],
    ["High + very high risk", fmt(hi), `ha · ${fmt(100 * hi / total, 1)}% of land`, "#d73027"],
    ["Bank erosion", fmt(ch("Bank erosion")), "ha of land lost to water", "#d7191c"],
    ["Accretion", fmt(ch("Accretion")), "ha of new land", "#2c7bb6"],
    ["Vegetation loss", fmt(ch("Vegetation loss")), "ha", "#fdae61"],
    ["Mean soil loss", fmt(s.mean_soil_loss_t_ha_yr, 1), "t/ha/yr (relative)", "#8c510a"],
  ];
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
  const order = ["risk", "change", "soilloss", "ndvi", "truecolour"];
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
    ["chg_bank_erosion_ha", "Bank erosion", "#d7191c"], ["chg_accretion_ha", "Accretion", "#2c7bb6"],
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
  if (!hot.features.length) { tb.innerHTML = `<tr><td colspan="6">No priority sites in this run.</td></tr>`; return; }
  tb.innerHTML = hot.features.map(f => f.properties).sort((a, b) => a.rank - b.rank).map(p => `
    <tr data-lat="${p.lat}" data-lon="${p.lon}">
      <td>${p.rank}</td>
      <td><span class="tag"><i style="background:${CLASS_COLOR[p.class] || "#333"}"></i>${shortName(p.class)}</span></td>
      <td class="num">${fmt(p.area_ha, 2)}</td>
      <td class="num">${p.mean_soil_loss_t_ha_yr != null && !isNaN(p.mean_soil_loss_t_ha_yr) ? fmt(p.mean_soil_loss_t_ha_yr, 1) : "–"}</td>
      <td class="num">${fmt(p.priority_score, 2)}</td>
      <td><a target="_blank" rel="noopener" href="https://www.google.com/maps?q=${p.lat},${p.lon}" onclick="event.stopPropagation()">${p.lat}, ${p.lon}</a></td>
    </tr>`).join("");
  tb.querySelectorAll("tr").forEach(tr => tr.onclick = () => {
    MAP.flyTo([+tr.dataset.lat, +tr.dataset.lon], 14, { duration: 0.8 });
    document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function downloads(s) {
  const links = [["hotspots.csv", "Priority sites (CSV)"], ["hotspots.geojson", "Priority sites (GeoJSON)"], ["history.csv", "Run history (CSV)"]]
    .map(([f, n]) => `<a href="${DATA}${f}" download>${n}</a>`);
  if (s.repository) links.push(`<a target="_blank" rel="noopener" href="https://github.com/${s.repository}/releases/latest">Full-resolution GeoTIFFs</a>`);
  document.getElementById("downloads").innerHTML = links.join("");
}

main();
