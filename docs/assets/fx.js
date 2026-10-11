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
  // The part of this page that is visible on screen. Inside a Google Sites embed the frame can be
  // larger than the screen or partly scrolled away, so pop-ups are fitted to what can be seen.
  window.fxVisible = window.fxVisible || function (cb) {
    var vw = document.documentElement.clientWidth, vh = window.innerHeight, full = { top: 0, bottom: vh, left: 0, right: vw }, done = false;
    var finish = function (v) { if (!done) { done = true; cb(v); } };
    try {
      var io = new IntersectionObserver(function (es) {
        io.disconnect();
        var r = es[0].intersectionRect;
        if (!es[0].isIntersecting || r.height < 120) finish(full);
        else finish({ top: Math.max(0, r.top), bottom: Math.min(vh, r.bottom), left: Math.max(0, r.left), right: Math.min(vw, r.right) });
      });
      io.observe(document.documentElement);
      setTimeout(function () { finish(full); }, 300);
    } catch (e) { finish(full); }
  };
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
    if (open) {
      render(open);
      window.fxVisible(function (v) {
        var t = panel.getBoundingClientRect().top;
        panel.style.maxHeight = Math.max(160, v.bottom - Math.max(t, v.top) - 12) + "px";
        if (t > v.bottom - 160 || t < v.top) panel.scrollIntoView({ block: "nearest" });
      });
    }
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

// 7. "How to read" help on the SWiFT-Bank district dashboards: an (i) button on each key
//    figure and card, a short explanation under the map legend for the chosen layer, and a
//    reading guide next to the download buttons.
(function () {
  if (!/\/swift\//.test(location.pathname) || !document.getElementById("layerSelect")) return;

  var KPI = {
    "District area": "Total area of the district, inside the boundary used for all maps.",
    "Sites for immediate action": "Bank erosion sites where the loss is statistically sure (High or Medium confidence) and at least one of these holds: 500 or more people live within 500 m, an embankment lies inside the site, or the bank is retreating 50 m or more a year. Inform the Water Resources Department before the monsoon. The small text gives the number of further sites to check before the monsoon.",
    "High + very high risk": "Land where the estimated soil loss from rain (RUSLE) is above 20 t/ha/yr: High is 20 to 40, Very high is above 40. This is sheet and rill erosion on slopes and bare land, not river bank loss.",
    "Land lost to river 1984–2021": "Land that became permanent or seasonal water in the JRC Landsat record (30 m, 1984 to 2021). It shows where the river has taken land over 37 years, including sand bars. Use it as background for the current change.",
    "People near active bank erosion": "People living within 500 m of bank erosion found in the current window, from WorldPop 2020 (100 m cells). It is an upper estimate, because a 100 m cell may be partly outside the 500 m zone.",
    "Bank erosion": "Stable land that turned into water between the two windows. Stable means it was never water from 1984 to 2021. In the river corridor this is the land lost found by SWiFT-Bank, counted inside mixed pixels, and every patch has p < 0.01.",
    "Accretion": "Water that turned into land: new deposits left by the river. Much of it is on sand bars and may be washed away again.",
    "Char / sandbar loss": "Land inside the river belt (water at some time since 1984) that turned into water. Mostly shifting chars and sand bars. It matters where people live or farm on chars, as in Majuli.",
    "Vegetation loss": "Land where the greenness index (NDVI) fell by more than 0.15 between the two windows. Common causes are harvest, tea pruning and clearing, so check the cause before acting.",
    "Mean soil loss": "Average estimated soil loss from rain (RUSLE) over all land in the district. Values below 5 t/ha/yr are in the Very low class."
  };

  var CARD = {
    "Map": "<p>Choose what to show with <b>Layer</b>; the text under the colour key explains the chosen layer. <b>Opacity</b> makes the layer lighter so the satellite picture shows through.</p><p>Numbered circles are the priority sites (number = rank, colour = type). Click a circle for its details, the suggested action and directions. The yellow line is the district boundary. Use the button at the top right of the map to switch between the satellite and street maps.</p><p><b>Tip:</b> zoom in on a site, then switch between <i>Change since baseline</i> and <i>Satellite image</i> to see what changed on the ground.</p>",
    "Area by erosion risk class": "<p>Each bar is the land area in one soil erosion risk class from RUSLE: Very low up to 5, Low 5 to 10, Moderate 10 to 20, High 20 to 40, Very high above 40 t/ha/yr.</p><p>Flat floodplains and paddy land fall in Very low, so that bar is always the largest. The High and Very high bars are the ones to act on: slopes and bare land that need cover, bunds or check dams.</p>",
    "Change over successive runs": "<p>Each point is one weekly run. A line shows how the area of one change type has developed.</p><p>The lines stay flat while no new clear image arrives (for example in the monsoon), and move when new dry-season images come in. A sudden jump in one run can be caused by clouds or haze, so check the satellite image before reading it as real change.</p>",
    "Priority sites for field verification": "<p>Sites are sorted by <b>Priority</b> = hazard × (0.3 + 0.7 × exposure). Hazard depends on the type of change, its area and how sure it is; exposure counts the people, buildings, cropland, roads, embankments, schools and health facilities within 500 m.</p><ul><li><b>Confidence</b>: High is 0.9 or more, Medium 0.75 to 0.9, Low below. For SWiFT-Bank sites it is 1 minus the p-value of the change test; for AI sites, how far the two models agree.</li><li><b>Action</b>: urgency (Immediate, Before monsoon, Routine, Monitor) and the lead department.</li><li><b>Bank retreat</b>: how far the bank moved, in metres per year, measured between the sub-pixel bank lines.</li><li><b>Field check</b>: the result of the field form, once staff have visited.</li></ul><p>Start from the top and with the Immediate sites. Click a row to see the site on the map.</p>",
    "Live sensor stations": "<p>These readings are <b>simulated</b> until real stations are installed. Each station measures river level, rain, soil moisture and the tilt of stakes 5 to 40 m back from the bank.</p><p>Warning signs: the river rising fast, soil near saturation, and a stake that tilts and then stops reporting, which usually means the bank edge has reached it. Active warnings are listed under <i>Active alerts</i>.</p>",
    "Field verification": "<p>This card shows how many priority sites have been visited with the field form and how many were confirmed, by site type. A high share of confirmed sites means the maps can be trusted for planning; sites marked <i>Not confirmed</i> help tune the method.</p>",
    "AI bank-erosion model compared with RUSLE": "<p>The table scores each method on land it did not see during training. <b>ROC AUC</b>: 0.5 means no skill and 1.0 perfect. <b>PR AUC</b>: how well the top-ranked land matches the land actually lost. <b>Captured</b>: the share of real loss found in the 10 % or 20 % of land the method ranks highest, which is what matters when only a few sites can be visited.</p><p>The bar chart shows which inputs the model uses most. It shows what the model relies on, not proof of cause.</p>",
    "Accuracy assessment": "<p><b>User's accuracy</b>: of the area the map calls a class, the share that is right. <b>Producer's accuracy</b>: of the true area of a class, the share the map found. Estimated areas correct the mapped areas for the errors in the sample.</p><p>Until the sample points are labelled, this card shows the sample only. Tests of SWiFT-Bank against independent labelled data are on the method comparison page.</p>"
  };

  var LAYER = {
    change: "<b>What changed</b> between the baseline window and the current window. <b>Red</b>: stable land lost to the river (bank erosion). <b>Pink</b>: char or sand bar lost inside the river belt. <b>Dark blue</b>: new land left by the river. <b>Green</b>: new ponds or flooded fields, not erosion. <b>Orange</b>: vegetation loss. <b>Brown</b>: new bare soil. Within 1 km of the river, water change comes from SWiFT-Bank at 10 m and is statistically tested; elsewhere the 20 m layers are used. Red next to villages, roads or embankments matters most. Confirm every site in the field.",
    ai: "<b>Where the river is likely to take land next</b>, over a period about as long as the gap between the two windows. Dark red is very high susceptibility. It covers land within 3 km of the river. It is a likelihood, not a date or a certainty. Trust it most where the <i>AI model agreement</i> layer is also high; the model's tested skill is in the AI card below.",
    confidence: "<b>How sure the change test is</b>, shown only where change was found. Yellow (near 1.0) means very sure; dark purple (near 0.5) means weak evidence. In the river corridor the value is 1 minus the p-value of the SWiFT-Bank test. Sites marked High have 0.9 or more. Use it to decide which changes to visit first.",
    agreement: "<b>How closely the two AI models agree</b> (Random Forest and XGBoost), from 0.5 (they disagree) to 1.0 (they agree). Yellow means agreement; purple means disagreement. Treat AI predictions in purple areas with care.",
    history: "<b>Long-term river movement, 1984 to 2021</b>, from 37 years of Landsat images (JRC Global Surface Water). <b>Purple</b>: land that became permanent water. <b>Pink</b>: land that became seasonal water. <b>Dark blue</b>: permanent water that became land. <b>Light blue</b>: seasonal water that became land. Banks that moved in the past often keep moving; compare with the current change layer.",
    risk: "<b>Soil erosion risk from rain</b> (RUSLE classes, t/ha/yr): Very low up to 5, Low 5 to 10, Moderate 10 to 20, High 20 to 40, Very high above 40. High values are on slopes with little cover and on bare land. This is not river bank erosion. Use it to find land that needs grass cover, cover crops, contour bunds or check dams.",
    soilloss: "<b>Estimated soil lost to rain each year</b> (t/ha/yr), shown on a log scale from 0.1 to 100. Losses above about 10 t/ha/yr are generally more than the soil can rebuild. Flat paddy land and floodplains show low values; slopes, bare fields and cleared land show high values.",
    ndvi: "<b>Greenness of vegetation</b> in the current window (NDVI, from -0.2 to 0.9). Red: water, sand, bare soil or built-up land. Yellow: sparse cover or dry crops. Green: dense crops, tea gardens and forest. The images are from the dry season, so many fields are bare after harvest.",
    truecolour: "<b>A natural-colour picture</b> made from the clear dates of the current window. Use it to see what is on the ground: the river (dark), sand bars (light grey), fields, tea gardens and villages. Switch to it at the same zoom to see what a change patch looks like."
  };

  var GUIDE = "<p><b>How to read this page</b></p><ol><li><b>Key figures</b> at the top give the totals for the district. Click (i) on any card for its meaning.</li><li><b>Map</b>: start with <i>Change since baseline</i>. Red next to settlements, roads or embankments is the main warning. The text under the colour key explains each layer.</li><li><b>Priority sites</b>: read the table from the top. Immediate sites come first; click a row to see it on the map.</li><li><b>Confidence</b>: check it before acting. Every site still needs a field check.</li><li><b>Report and data</b>: use the two buttons for the two-weekly report (English and Assamese) and the map files.</li></ol>";

  var css = document.createElement("style");
  css.textContent =
    ".xp-btn{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;margin-left:6px;padding:0;border-radius:50%;" +
    "border:1px solid #0f6e8c;background:#fff;color:#0f6e8c;font:italic 700 11px/1 Georgia,serif;cursor:pointer;vertical-align:2px;flex:none}" +
    ".xp-btn:hover,.xp-btn[aria-expanded=true]{background:#0f6e8c;color:#fff}" +
    ".kpi .label{display:flex;align-items:flex-start;justify-content:space-between;gap:4px}.kpi .xp-btn{margin-left:4px}" +
    ".xp-pop{position:fixed;z-index:1300;width:min(400px,calc(100vw - 24px));overflow:auto;background:#fff;border:1px solid #d9d4c7;border-radius:10px;" +
    "box-shadow:0 10px 30px rgba(0,0,0,.16);padding:12px 14px;font-size:13.5px;line-height:1.5;color:#1d1d1b}" +
    ".xp-pop p{margin:0 0 8px}.xp-pop p:last-child{margin:0}.xp-pop ul,.xp-pop ol{margin:4px 0 8px;padding-left:18px}.xp-pop li{margin-bottom:4px}" +
    ".xp-pop .xp-h{font-weight:700;margin-bottom:6px;color:#0f5d73}" +
    ".xp-layer{margin-top:8px;padding:8px 12px;background:#f3f7f8;border-left:3px solid #1f8a8a;border-radius:6px;font-size:13px;line-height:1.5;color:#2b2b28}" +
    ".xp-layer .xp-t{font-weight:700;color:#0f5d73;margin-right:4px}" +
    ".dl-bar .xp-guide{margin-right:auto}";
  document.head.appendChild(css);

  var pop = document.createElement("div");
  pop.className = "xp-pop"; pop.hidden = true; pop.setAttribute("role", "dialog");
  document.body.appendChild(pop);
  var openBtn = null;
  function close() { pop.hidden = true; if (openBtn) openBtn.setAttribute("aria-expanded", "false"); openBtn = null; }
  function open(btn, title, html) {
    if (openBtn === btn) { close(); return; }
    close();
    pop.innerHTML = "<div class='xp-h'></div>" + html;
    pop.firstChild.textContent = title;
    pop.style.visibility = "hidden"; pop.style.maxHeight = ""; pop.style.left = "0px"; pop.style.top = "0px";
    pop.hidden = false;
    btn.setAttribute("aria-expanded", "true"); openBtn = btn;
    (window.fxVisible || function (cb) { cb({ top: 0, bottom: window.innerHeight, left: 0, right: document.documentElement.clientWidth }); })(function (v) {
      if (openBtn !== btn) return;
      var r = btn.getBoundingClientRect(), gap = 8, m = 12;
      pop.style.maxHeight = Math.max(120, v.bottom - v.top - 2 * m) + "px";
      var w = pop.offsetWidth, h = pop.offsetHeight;
      var left = Math.max(v.left + m, Math.min(r.left - 12, v.right - w - m));
      var below = v.bottom - r.bottom - gap - m, above = r.top - v.top - gap - m, top;
      if (h <= below) top = r.bottom + gap;
      else if (h <= above) top = r.top - gap - h;
      else top = Math.max(v.top + m, v.bottom - h - m);
      pop.style.left = left + "px"; pop.style.top = top + "px"; pop.style.visibility = "";
    });
  }
  document.addEventListener("click", function (e) { if (openBtn && !pop.contains(e.target) && e.target !== openBtn) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  window.addEventListener("scroll", close, { passive: true });
  window.addEventListener("resize", close);
  function button(title, html) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "xp-btn"; b.textContent = "i";
    b.setAttribute("aria-label", "How to read: " + title); b.setAttribute("aria-expanded", "false"); b.title = "How to read this";
    b.addEventListener("click", function (e) { e.stopPropagation(); open(b, title, html); });
    return b;
  }

  // Cards
  function cardButtons() {
    document.querySelectorAll(".card h2").forEach(function (h) {
      var t = h.textContent.trim();
      Object.keys(CARD).forEach(function (k) { if (t.indexOf(k) === 0 && !h.querySelector(".xp-btn")) h.appendChild(button(k, CARD[k])); });
    });
  }
  cardButtons();

  // Layer explanation under the legend
  var sel = document.getElementById("layerSelect"), leg = document.getElementById("legend");
  var box = document.createElement("div"); box.className = "xp-layer";
  leg.parentNode.insertBefore(box, leg.nextSibling);
  function layerText() {
    var k = sel.value, txt = LAYER[k];
    box.hidden = !txt;
    if (txt) box.innerHTML = "<span class='xp-t'>Reading this layer:</span>" + txt;
  }
  sel.addEventListener("change", layerText);

  // Reading guide next to the download buttons
  function guideButton() {
    var bar = document.querySelector(".dl-bar");
    if (!bar || bar.querySelector(".xp-guide")) return !!bar;
    var g = document.createElement("button");
    g.type = "button"; g.className = "dl-btn xp-guide";
    g.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>How to read this page';
    g.addEventListener("click", function (e) { e.stopPropagation(); open(g, "Reading guide", GUIDE); });
    bar.insertBefore(g, bar.firstChild);
    return true;
  }

  // Key figures and the layer list are filled after the data loads
  var tries = 0;
  var timer = setInterval(function () {
    tries++;
    var cards = document.querySelectorAll("#kpis .kpi .label");
    cards.forEach(function (l) {
      if (l.querySelector(".xp-btn")) return;
      var t = l.textContent.trim();
      if (KPI[t]) { var s = document.createElement("span"); s.textContent = t; l.textContent = ""; l.appendChild(s); l.appendChild(button(t, "<p>" + KPI[t] + "</p>")); }
    });
    if (sel.options.length && !box.dataset.done) { box.dataset.done = "1"; layerText(); }
    cardButtons();
    var g = guideButton();
    if (tries > 120) clearInterval(timer);
  }, 500);
})();
