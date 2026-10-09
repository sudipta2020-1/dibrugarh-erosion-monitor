// Reference labelling for the accuracy assessment. Labels are kept in this
// browser while working and exported as CSV for data/validation_labels.csv.
const VDIR = "data/validation/";
const OPTIONS = [
  ["erosion_mainland", "1", "Bank erosion of stable land", "Settled, farmed or forested bank is now water"],
  ["erosion_char", "2", "Char or sandbar lost", "A sandbar inside the channel is now water"],
  ["accretion", "3", "Accretion", "Water is now land (sand, grass or crops)"],
  ["stable_land", "4", "Stable land", "Land in both images (crop changes included)"],
  ["stable_water", "5", "Stable water", "Water in both images"],
  ["unsure", "0", "Unsure", "Cloud, shadow or mixed pixel"],
];
const KEY = "dib-erosion-labels-v1";
let PTS = [], META = {}, LAB = {}, I = 0, MAP, PIX, BOX;

const store = {
  load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } },
  save() { try { localStorage.setItem(KEY, JSON.stringify(LAB)); } catch { /* storage unavailable */ } },
};

function csvParse(t) {
  const lines = t.trim().split(/\r?\n/);
  const head = splitRow(lines[0]);
  return lines.slice(1).map(l => { const v = splitRow(l); const o = {}; head.forEach((h, i) => o[h.trim()] = (v[i] || "").trim()); return o; });
}
function splitRow(l) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < l.length; i++) {
    const c = l[i];
    if (q) { if (c === '"' && l[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
    else if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
  }
  out.push(cur); return out;
}
const esc = v => /[",\n]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : String(v);

async function main() {
  try {
    const [s, m] = await Promise.all([
      fetch(VDIR + "sample.csv", { cache: "no-cache" }).then(r => { if (!r.ok) throw 0; return r.text(); }),
      fetch(VDIR + "sample_meta.json", { cache: "no-cache" }).then(r => r.json()),
    ]);
    PTS = csvParse(s); META = m;
  } catch { document.getElementById("empty").hidden = false; return; }
  document.getElementById("work").hidden = false;
  LAB = store.load();
  const who = document.getElementById("who");
  who.value = LAB.__who || "";
  who.oninput = () => { LAB.__who = who.value; store.save(); };
  const p = a => `${a[0]} to ${a[1]}`;
  document.getElementById("capB").textContent = `Baseline: ${p(META.period_baseline)} (Sentinel-2)`;
  document.getElementById("capC").textContent = `Current: ${p(META.period_current)} (Sentinel-2)`;

  const opts = document.getElementById("opts");
  opts.innerHTML = OPTIONS.map(([v, k, t, d]) => `<button class="opt" data-v="${v}"><kbd>${k}</kbd><div><b>${t}</b><span>${d}</span></div></button>`).join("");
  opts.querySelectorAll(".opt").forEach(b => b.onclick = () => choose(b.dataset.v));
  document.getElementById("notes").oninput = e => { const id = PTS[I].id; LAB[id] = { ...(LAB[id] || {}), notes: e.target.value }; store.save(); };
  document.getElementById("prev").onclick = () => go(I - 1);
  document.getElementById("next").onclick = () => go(I + 1);
  document.getElementById("jump").onclick = () => { const j = PTS.findIndex(q => !(LAB[q.id] && LAB[q.id].reference)); go(j < 0 ? I : j); };
  document.getElementById("download").onclick = download;
  document.getElementById("importFile").onchange = importCsv;
  document.addEventListener("keydown", e => {
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    const o = OPTIONS.find(x => x[1] === e.key);
    if (o) choose(o[0]);
    if (e.key === "ArrowRight") go(I + 1);
    if (e.key === "ArrowLeft") go(I - 1);
  });

  MAP = L.map("vmap", { zoomControl: true, attributionControl: true });
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Imagery © Esri", maxZoom: 19 }).addTo(MAP);
  const first = PTS.findIndex(q => !(LAB[q.id] && LAB[q.id].reference));
  go(first < 0 ? 0 : first);
}

function boxAround(lat, lon, halfM) {
  const dLat = halfM / 111320, dLon = halfM / (111320 * Math.cos(lat * Math.PI / 180));
  return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
}

function go(j) {
  if (j < 0 || j >= PTS.length) return;
  I = j;
  const q = PTS[I], lat = +q.lat, lon = +q.lon, res = META.resolution_m || 20;
  document.getElementById("pid").textContent = `Point ${q.id}`;
  document.getElementById("pcoords").textContent = `${q.lat}, ${q.lon} · ${I + 1} of ${PTS.length}`;
  document.getElementById("plinks").innerHTML =
    `<a target="_blank" rel="noopener" href="https://www.google.com/maps/@${lat},${lon},700m/data=!3m1!1e3">Google Maps</a>`;
  document.getElementById("chipB").src = `${VDIR}chips/${q.id}_b.jpg`;
  document.getElementById("chipC").src = `${VDIR}chips/${q.id}_c.jpg`;
  [PIX, BOX].forEach(l => l && MAP.removeLayer(l));
  PIX = L.rectangle(boxAround(lat, lon, res / 2), { color: "#ffe600", weight: 2, fill: false }).addTo(MAP);
  BOX = L.rectangle(boxAround(lat, lon, res * 32), { color: "#fff", weight: 1, dashArray: "4 4", fill: false }).addTo(MAP);
  MAP.fitBounds(BOX.getBounds());          // same extent as the image chips
  const cur = LAB[q.id] || {};
  document.querySelectorAll(".opt").forEach(b => b.classList.toggle("on", b.dataset.v === cur.reference));
  document.getElementById("notes").value = cur.notes || "";
  progress();
}

function choose(v) {
  const id = PTS[I].id;
  LAB[id] = { ...(LAB[id] || {}), reference: v, at: new Date().toISOString() };
  store.save();
  document.querySelectorAll(".opt").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  progress();
  setTimeout(() => go(Math.min(I + 1, PTS.length - 1)), 180);
}

function progress() {
  const n = PTS.filter(q => LAB[q.id] && LAB[q.id].reference).length;
  document.getElementById("progressText").textContent = `${n} of ${PTS.length} points labelled`;
  document.getElementById("progressBar").style.width = `${(100 * n / PTS.length).toFixed(1)}%`;
}

function download() {
  const rows = [["id", "lat", "lon", "reference", "notes", "interpreter", "labelled_at"]];
  PTS.forEach(q => { const l = LAB[q.id]; if (l && l.reference) rows.push([q.id, q.lat, q.lon, l.reference, l.notes || "", LAB.__who || "", l.at || ""]); });
  const blob = new Blob([rows.map(r => r.map(esc).join(",")).join("\n") + "\n"], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "validation_labels.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function importCsv(e) {
  const f = e.target.files[0]; if (!f) return;
  f.text().then(t => {
    csvParse(t).forEach(r => { if (r.id && r.reference) LAB[r.id] = { reference: r.reference, notes: r.notes || "", at: r.labelled_at || "" }; });
    store.save(); go(I);
  });
  e.target.value = "";
}

main();
