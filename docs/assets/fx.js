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
