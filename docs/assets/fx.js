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
