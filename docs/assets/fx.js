// Colour and motion layer: animated header waves, count-up numbers, scroll reveal.
(function () {
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 1. Animated waves and a passing satellite in the dashboard header
  var top = document.querySelector("header.top");
  if (top && !top.querySelector(".fx-waves")) {
    var wave = function (cls, y, fill) {
      return '<svg class="' + cls + '" viewBox="0 0 1200 56" preserveAspectRatio="none"><path fill="' + fill +
        '" d="M0 ' + y + ' C150 ' + (y - 14) + ' 300 ' + (y + 14) + ' 450 ' + y + ' S750 ' + (y - 14) + ' 900 ' + y +
        ' S1050 ' + (y + 14) + ' 1200 ' + y + ' L1200 56 L0 56 Z"/></svg>';
    };
    var w = document.createElement("div");
    w.className = "fx-waves";
    w.innerHTML = wave("w1", 30, "#ffffff") + wave("w2", 38, "#bfe6f2");
    top.appendChild(w);
    var s = document.createElement("div");
    s.className = "fx-sat";
    s.innerHTML = '<svg viewBox="0 0 46 16"><rect x="17" y="3" width="12" height="10" rx="2" fill="#fff"/>' +
      '<rect x="0" y="5" width="15" height="6" fill="#9fd3f0"/><rect x="31" y="5" width="15" height="6" fill="#9fd3f0"/></svg>';
    top.appendChild(s);
    var h1 = top.querySelector("h1");
    if (h1 && h1.parentNode) {
      var badge = document.createElement("div");
      badge.className = "fx-tag";
      badge.innerHTML = "<i></i>Satellite update every week";
      h1.parentNode.appendChild(badge);
    }
  }

  // 2. Count-up for plain numbers in KPI cards and district summaries
  function countUp(el) {
    if (reduce || el.dataset.fxDone) return;
    var txt = el.textContent.trim();
    if (!/^\d[\d,]*(\.\d+)?$/.test(txt)) return;
    el.dataset.fxDone = "1";
    var target = parseFloat(txt.replace(/,/g, ""));
    var dec = (txt.split(".")[1] || "").length;
    var loc = /\d,\d\d,\d{3}/.test(txt) ? "en-IN" : "en-US";
    var t0 = null, dur = 1200;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * e).toLocaleString(loc, { minimumFractionDigits: dec, maximumFractionDigits: dec });
      if (p < 1) requestAnimationFrame(step); else el.textContent = txt;
    }
    requestAnimationFrame(step);
  }
  function scanNumbers(root) {
    (root || document).querySelectorAll(".kpi .value, .kv b").forEach(countUp);
  }

  // 3. Reveal cards as they scroll into view
  var io = null;
  if (!reduce && "IntersectionObserver" in window) {
    document.documentElement.classList.add("fx-on");
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("fx-in"); scanNumbers(en.target); io.unobserve(en.target); }
      });
    }, { threshold: 0.08 });
    setTimeout(function () { document.querySelectorAll(".fx-r").forEach(function (e) { e.classList.add("fx-in"); }); }, 4000);
  }
  function reveal(root) {
    (root || document).querySelectorAll(".card, .kpi, .step, .hero").forEach(function (el, i) {
      if (el.classList.contains("fx-r") || el.closest(".leaflet-container")) return;
      el.classList.add("fx-r");
      el.style.transitionDelay = (el.classList.contains("kpi") ? (i % 10) * 60 : 0) + "ms";
      if (io) io.observe(el); else scanNumbers(el);
    });
  }
  reveal();
  scanNumbers();

  // Numbers and cards are filled in after the data loads, so watch for them
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { reveal(n.parentNode || n); } });
      var t = m.target.nodeType === 1 ? m.target : m.target.parentNode;
      if (t && t.matches && (t.matches(".kpi .value, .kv b") || t.matches(".kpis, .kv"))) {
        var box = t.closest(".fx-r");
        if (!box || box.classList.contains("fx-in") || !io) scanNumbers(t.parentNode || t);
      }
    });
  }).observe(document.body, { childList: true, subtree: true });
})();

// 4. Method page: show each calculation step as a compact tile; the full text,
//    formulas and latest values open in a pop-up, so the page fits its window.
(function () {
  var steps = document.querySelectorAll(".step");
  if (!steps.length) return;
  var ov = document.createElement("div");
  ov.className = "fx-modal"; ov.hidden = true; ov.setAttribute("role", "dialog"); ov.setAttribute("aria-modal", "true");
  ov.innerHTML = '<div class="fx-modal-box"><button class="fx-x" aria-label="Close">×</button><div class="fx-modal-body"></div></div>';
  document.body.appendChild(ov);
  var body = ov.querySelector(".fx-modal-body");
  function close() { ov.hidden = true; }
  ov.addEventListener("click", function (e) { if (e.target === ov || e.target.closest(".fx-x")) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  steps.forEach(function (st) {
    var inner = st.children[1], h3 = inner && inner.querySelector("h3"), p = inner && inner.querySelector("p");
    if (!inner || !h3) return;
    var txt = p ? p.textContent.trim() : "";
    var first = (txt.match(/^.*?[.!?](\s|$)/) || [txt])[0].trim();
    if (first.length > 170) first = first.slice(0, 167).replace(/\s+\S*$/, "") + "…";
    var tile = document.createElement("div");
    tile.className = "fx-tile";
    tile.innerHTML = "<p></p>";
    tile.querySelector("p").textContent = first;
    var btn = document.createElement("button");
    btn.type = "button"; btn.className = "fx-more"; btn.textContent = "See how it is calculated →";
    tile.appendChild(btn);
    inner.classList.add("fx-full");
    inner.parentNode.insertBefore(tile, inner);
    tile.insertBefore(h3, tile.firstChild);
    function open() {
      body.innerHTML = "";
      var num = st.querySelector(".num").cloneNode(true);
      var head = document.createElement("div"); head.className = "fx-mhead";
      head.appendChild(num); head.appendChild(h3.cloneNode(true));
      body.appendChild(head);
      body.appendChild(inner.cloneNode(true)).classList.remove("fx-full");
      body.style.setProperty("--sc", getComputedStyle(st).getPropertyValue("--sc"));
      // The page may sit in a tall embed box, so place the pop-up next to the tile, not at the top
      var box = ov.querySelector(".fx-modal-box"), docH = document.documentElement.scrollHeight;
      ov.style.height = docH + "px"; ov.hidden = false;
      var y = tile.getBoundingClientRect().top + window.scrollY - 140;
      box.style.marginTop = Math.max(16, Math.min(y, docH - box.offsetHeight - 16)) + "px";
      ov.querySelector(".fx-x").focus({ preventScroll: true });
    }
    btn.addEventListener("click", open);
    h3.style.cursor = "pointer"; h3.addEventListener("click", open);
  });
  // References fold into a short list that can be opened
  var refs = document.querySelector(".refs");
  if (refs && !refs.closest("details")) {
    var d = document.createElement("details"); d.className = "fx-refs";
    d.innerHTML = "<summary>Show all " + refs.children.length + " references</summary>";
    refs.parentNode.insertBefore(d, refs); d.appendChild(refs);
  }
})();

// 5. Full-screen option. Google Sites embeds do not allow true full screen, so
//    inside the site the button opens the page in its own tab, where a second
//    click makes it full screen. The dashboard map can also be expanded.
(function () {
  var embedded = window.self !== window.top;
  var canFS = !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);
  var isFS = function () { return !!(document.fullscreenElement || document.webkitFullscreenElement); };
  function enter(el) { (el.requestFullscreen || el.webkitRequestFullscreen).call(el); }
  function exit() { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }

  var btn = document.createElement("button");
  btn.type = "button"; btn.className = "fx-fs";
  function label() {
    btn.innerHTML = isFS() ? "✕ Exit full screen" : (embedded || !canFS) ? "⛶ Open full screen ↗" : "⛶ Full screen";
    btn.title = embedded ? "Opens this page in its own tab; click again there for full screen" : "";
  }
  btn.addEventListener("click", function () {
    if (isFS()) { exit(); return; }
    if (embedded || !canFS) { window.open(location.href.split("#")[0].split("?")[0] + "?fs=1", "_blank", "noopener"); return; }
    enter(document.documentElement);
  });
  document.addEventListener("fullscreenchange", label);
  document.addEventListener("webkitfullscreenchange", label);
  label();
  var h1 = document.querySelector("header.top h1");
  var host = (h1 && h1.parentNode) || document.querySelector(".hero-text");
  if (host) host.appendChild(btn); else { btn.classList.add("fx-fs-float"); document.body.appendChild(btn); }
  if (/[?&]fs=1/.test(location.search) && canFS) btn.classList.add("fx-fs-hint");

  // Expand the dashboard map to fill the screen
  var card = document.querySelector(".map-card"), ctr = card && card.querySelector(".controls");
  if (ctr) {
    var mb = document.createElement("button");
    mb.type = "button"; mb.className = "fx-mapmax"; mb.textContent = "⛶ Expand map";
    var resize = function () { setTimeout(function () { if (window.MAP && MAP.invalidateSize) MAP.invalidateSize(); }, 250); };
    var toggle = function (on) {
      card.classList.toggle("fx-max", on);
      document.documentElement.classList.toggle("fx-noscroll", on);
      mb.textContent = on ? "✕ Close map" : "⛶ Expand map";
      if (!embedded && canFS) { if (on && !isFS()) { try { enter(card); } catch (e) {} } else if (!on && isFS()) exit(); }
      resize();
    };
    mb.addEventListener("click", function () { toggle(!card.classList.contains("fx-max")); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && card.classList.contains("fx-max")) toggle(false); });
    document.addEventListener("fullscreenchange", function () { if (!isFS() && card.classList.contains("fx-max")) toggle(false); });
    ctr.appendChild(mb);
  }
})();

// 6. Report and data downloads: two buttons on the right, under the page header.
//    District pages show their own district; the Home and method pages show both.
(function () {
  var top = document.querySelector("header.top, header.hero");
  if (!top || document.querySelector(".dl-bar")) return;
  var OWNER = "sudipta2020-1";
  var D = { Dibrugarh: "dibrugarh-erosion-monitor", Majuli: "majuli-erosion-monitor" };
  var seg = location.pathname.split("/").filter(Boolean);
  var here = null;
  Object.keys(D).forEach(function (k) { if (seg.indexOf(D[k]) >= 0) here = k; });
  if (!here) here = /Majuli/.test(document.title) ? "Majuli" : "Dibrugarh";
  var page = seg.length ? seg[seg.length - 1] : "";
  var both = /^(method|novel)\.html$/.test(page) || (page === "" && here === "Dibrugarh" && /Method/.test(document.title));
  var list = both ? ["Dibrugarh", "Majuli"] : [here];
  var origin = /github\.io$/.test(location.hostname) ? "https://" + OWNER + ".github.io" : location.origin;
  var base = function (d) { return origin + "/" + D[d] + "/"; };
  var rel = function (d, tag, f) { return "https://github.com/" + OWNER + "/" + D[d] + "/releases/" + (tag === "latest" ? "latest/download/" : "download/" + tag + "/") + f; };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var getJ = function (u) { return fetch(u, { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); };
  var ddmm = function (s) { var p = String(s).slice(0, 10).split("-"); return p.length === 3 ? p[2] + "-" + p[1] + "-" + p[0] : s; };

  var css = document.createElement("style");
  css.textContent =
    ".dl-bar{position:relative;display:flex;justify-content:flex-end;gap:8px;margin:-4px 0 12px;flex-wrap:wrap}header.hero+.dl-bar{margin:10px 0 4px}" +
    ".dl-btn{font:inherit;font-size:13.5px;font-weight:600;display:inline-flex;align-items:center;gap:7px;padding:7px 14px;border-radius:8px;cursor:pointer;" +
    "border:1px solid #0f6e8c;background:#fff;color:#0f6e8c;box-shadow:0 1px 2px rgba(0,0,0,.06)}" +
    ".dl-btn.pri{background:#0f6e8c;color:#fff}.dl-btn:hover{filter:brightness(.95)}.dl-btn svg{width:16px;height:16px}" +
    ".dl-panel{position:absolute;right:0;top:calc(100% + 6px);z-index:1200;width:min(600px,calc(100vw - 32px));max-height:72vh;overflow:auto;" +
    "background:#fff;border:1px solid #d9d4c7;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.16);padding:12px 14px;font-size:13.5px}" +
    ".dl-panel h3{margin:10px 0 4px;font-size:14px;color:#1d1d1b}.dl-panel h3:first-child{margin-top:0}" +
    ".dl-panel h4{margin:8px 0 2px;font-size:12px;color:#6b6a62;text-transform:uppercase;letter-spacing:.04em;font-weight:600}" +
    ".dl-panel ul{list-style:none;margin:0;padding:0}.dl-panel li{display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid #f0ede6}" +
    ".dl-panel li a{color:#0f5d73;text-decoration:none;font-weight:600}.dl-panel li a:hover{text-decoration:underline}" +
    ".dl-panel .fm{flex:none;font-size:11.5px;color:#6b6a62;white-space:nowrap}.dl-panel .fm b{display:inline-block;padding:0 6px;border-radius:8px;background:#eef3f5;color:#0f5d73;font-weight:600;margin-right:4px}" +
    ".dl-panel .nt{font-size:12px;color:#6b6a62;margin:6px 0 0}.dl-tabs{display:flex;gap:6px;margin-bottom:8px}" +
    ".dl-tabs button{font:inherit;font-size:12.5px;padding:3px 10px;border-radius:12px;border:1px solid #d9d4c7;background:#fff;cursor:pointer}" +
    ".dl-tabs button.on{background:#0f6e8c;border-color:#0f6e8c;color:#fff}" +
    "@media (max-width:640px){.dl-bar{justify-content:stretch}.dl-btn{flex:1;justify-content:center}}";
  document.head.appendChild(css);

  var icon = {
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h6"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0-5-5m5 5 5-5M4 21h16"/></svg>'
  };
  var bar = document.createElement("div");
  bar.className = "dl-bar";
  bar.innerHTML = '<button type="button" class="dl-btn" data-p="rep" aria-expanded="false">' + icon.doc + 'Report (PDF)</button>' +
    '<button type="button" class="dl-btn pri" data-p="dat" aria-expanded="false">' + icon.down + 'Download data</button>' +
    '<div class="dl-panel" hidden role="dialog" aria-label="Downloads"></div>';
  top.parentNode.insertBefore(bar, top.nextSibling);
  var panel = bar.querySelector(".dl-panel");
  var cache = {};
  var load = function (d) {
    if (!cache[d]) cache[d] = Promise.all([getJ(base(d) + "reports/index.json"), getJ(base(d) + "data/swift/downloads.json")]);
    return cache[d];
  };
  var li = function (href, label, fmt, extra) {
    return '<li><a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(label) + '</a><span class="fm"><b>' + esc(fmt) + "</b>" + esc(extra || "") + "</span></li>";
  };
  var L20 = [["soil_loss_t_ha_yr.tif", "Annual soil loss, t/ha/yr (RUSLE)"], ["erosion_risk_class.tif", "Erosion risk class (RUSLE)"],
    ["change_class.tif", "Land change classes"], ["ai_bank_erosion_probability.tif", "AI bank-erosion probability"],
    ["bank_retreat_m.tif", "Bank retreat, m"], ["water_frequency_current.tif", "Water frequency, current window"],
    ["water_frequency_baseline.tif", "Water frequency, baseline window"]];

  function repHtml(d, idx) {
    var b = base(d) + "reports/";
    if (!idx || !idx.reports || !idx.reports.length)
      return "<h3>" + d + " District</h3><p class=\"nt\">The first fortnightly report will appear here after the next weekly run.</p>";
    var h = "<h3>" + d + " District · latest report " + ddmm(idx.latest) + "</h3><ul>" +
      li(b + "latest_en.pdf", "Latest report in English", "PDF") + li(b + "latest_as.pdf", "Latest report in Assamese (অসমীয়া)", "PDF") + "</ul>";
    var old = idx.reports.slice(1, 7);
    if (old.length) {
      h += "<h4>Earlier reports</h4><ul>" + old.map(function (r) {
        return '<li><span>' + ddmm(r.date) + '</span><span class="fm"><a href="' + esc(b + r.files.en) + '" target="_blank" rel="noopener">English</a> · <a href="' +
          esc(b + r.files.as) + '" target="_blank" rel="noopener">অসমীয়া</a></span></li>';
      }).join("") + "</ul>";
    }
    return h;
  }
  function datHtml(d, man) {
    var h = "<h3>" + d + " District</h3>";
    h += "<h4>SWiFT-Bank, 10 m (river corridor)</h4>";
    if (man && man.files && man.files.length) {
      h += "<ul>" + man.files.filter(function (f) { return f.group !== "sites"; }).map(function (f) {
        return li(rel(d, man.tag, f.name), f.label, f.format, f.size_mb >= 0.01 ? " " + f.size_mb + " MB" : "");
      }).join("") + "</ul>";
    } else {
      h += '<p class="nt">GeoTIFF and Shapefile files of SWiFT-Bank will appear here after the next weekly run.</p>';
    }
    h += "<h4>Priority sites</h4><ul>";
    var s = man && man.files && man.files.filter(function (f) { return f.group === "sites"; })[0];
    if (s) h += li(rel(d, man.tag, s.name), "Priority sites for field verification", "Shapefile", " " + s.size_mb + " MB");
    h += li(base(d) + "swift/data/hotspots.csv", "Priority sites for field verification", "CSV") + "</ul>";
    h += "<h4>District layers, 20 m (whole district)</h4><ul>" + L20.map(function (x) { return li(rel(d, "latest", x[0]), x[1], "GeoTIFF"); }).join("") +
      li("https://github.com/" + OWNER + "/" + D[d] + "/releases/latest", "All 20 m files of the latest run", "Page") + "</ul>";
    if (man && man.run) h += '<p class="nt">SWiFT-Bank files from the run of ' + esc(man.run) + ". Coordinate system " + esc(man.crs || "EPSG:32646") + " (UTM 46N). See the read-me file for the meaning of each value.</p>";
    return h;
  }

  var open = null, cur = list[0];
  function render(which) {
    var tabs = list.length > 1 ? '<div class="dl-tabs">' + list.map(function (d) { return '<button type="button" data-d="' + d + '" class="' + (d === cur ? "on" : "") + '">' + d + "</button>"; }).join("") + "</div>" : "";
    panel.innerHTML = tabs + '<p class="nt">Loading…</p>';
    load(cur).then(function (r) {
      if (open !== which) return;
      panel.innerHTML = tabs + (which === "rep" ? repHtml(cur, r[0]) + '<p class="nt">Reports are made every two weeks from the latest satellite data, in English and Assamese.</p>' : datHtml(cur, r[1]));
    });
  }
  function show(which) {
    open = open === which ? null : which;
    bar.querySelectorAll(".dl-btn").forEach(function (b) { b.setAttribute("aria-expanded", String(b.dataset.p === open)); });
    panel.hidden = !open;
    if (open) render(open);
  }
  bar.addEventListener("click", function (e) {
    var b = e.target.closest(".dl-btn");
    if (b) { show(b.dataset.p); return; }
    var t = e.target.closest(".dl-tabs button");
    if (t) { cur = t.dataset.d; render(open); }
  });
  document.addEventListener("click", function (e) { if (open && !bar.contains(e.target)) show(open); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && open) show(open); });
})();
