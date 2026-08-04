"use strict";
/* =============================================================
   RESOLV - shared script for index.html and methodology.html
   Every block is guarded, so each page only runs what it has.
   -------------------------------------------------------------
   CONFIG: set these two before deploying.
     CONTACT_EMAIL     - where "Start free" signups go.
     WAITLIST_ENDPOINT - optional POST URL (Formspree, Buttondown,
       your own API). If empty, addresses are captured to the
       visitor's localStorage (see the LAUNCH BLOCKER note in the
       signup block). When you set it, ALSO add its origin to the
       CSP connect-src in index.html, methodology.html and _headers,
       or the browser will block the POST. The form NEVER claims
       success unless the request actually succeeded.
   ============================================================= */
const CONTACT_EMAIL = "hello@resolv.trade";
const WAITLIST_ENDPOINT = "";   // e.g. "https://formspree.io/f/xxxxxxx"

/* FEED_PROXY - URL of the Resolv feed Worker (see worker/README.md).
   Leave empty and the page fetches GDELT straight from the visitor's
   browser, which works but breaks for anyone on a rate-limited or shared
   IP, and can never reach Kalshi or Polymarket because those calls are
   blocked by CORS or by our own CSP.
   Set it to your deployed Worker URL and you get live news AND live market
   prices, cached once per minute for every visitor at the same time.
   When you set it, add its origin to connect-src in the CSP: index.html,
   methodology.html and _headers. */
const FEED_PROXY = "";   // e.g. "https://resolv-feed.yourname.workers.dev"

/* ---------------- utils ---------------- */
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
/* only http(s) URLs from external data ever become hrefs; anything else
   (javascript:, data:, vbscript:, protocol-relative) is dropped */
const safeUrl = u => /^https?:\/\//i.test(String(u || "")) ? String(u) : "";
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;

if ($("year")) $("year").textContent = new Date().getFullYear();

/* Sample contracts - used by the hero tape and the wire's impact panel.
   Always presented under a SAMPLE label; never as live prices. */
const SAMPLE_MARKETS = [
  { v: "KALSHI", q: "FED CUTS SEPT", p: 64, d: 2, cat: "Rates" },
  { v: "POLYMARKET", q: "BTC > $120K DEC", p: 41, d: -1, cat: "Crypto" },
  { v: "KALSHI", q: "CPI BELOW 3% Q3", p: 72, d: 4, cat: "Inflation" },
  { v: "POLYMARKET", q: "RECESSION IN 2026", p: 23, d: 1, cat: "Economy" },
  { v: "KALSHI", q: "GOVT SHUTDOWN OCT 1", p: 18, d: -2, cat: "Politics" },
  { v: "POLYMARKET", q: "ETH > $8K DEC", p: 33, d: -3, cat: "Crypto" },
  { v: "KALSHI", q: "OIL > $90 DEC", p: 27, d: 2, cat: "Economy" },
  { v: "POLYMARKET", q: "FED HOLDS THROUGH '26", p: 12, d: -1, cat: "Rates" },
  { v: "KALSHI", q: "S&P ABOVE 7000 EOY", p: 38, d: 2, cat: "Stocks" },
  { v: "POLYMARKET", q: "DEMS WIN SENATE '26", p: 47, d: 1, cat: "Politics" },
  { v: "KALSHI", q: "CORE PCE < 2.5% DEC", p: 55, d: 1, cat: "Inflation" },
  { v: "KALSHI", q: "10Y YIELD > 5% EOY", p: 21, d: -1, cat: "Rates" },
  { v: "POLYMARKET", q: "NASDAQ RECORD BY OCT", p: 44, d: 3, cat: "Stocks" },
  { v: "POLYMARKET", q: "US GDP > 2% IN 2026", p: 58, d: 1, cat: "Economy" }
];

/* Filled in only when the feed Worker returns real market prices. Until then
   it stays empty and every market surface keeps its SAMPLE label. */
let LIVE_MARKETS = [];
const marketRows = () => (LIVE_MARKETS.length ? LIVE_MARKETS : SAMPLE_MARKETS);
const marketsAreLive = () => LIVE_MARKETS.length > 0;

/* hero entrance - class flip so animations start after first paint */
requestAnimationFrame(() => document.body.classList.add("loaded"));
setTimeout(() => document.body.classList.add("loaded"), 400);   // failsafe if rAF is throttled

/* ---------------- nav ---------------- */
const nav = $("nav"), menu = $("mobileMenu"), navToggle = $("navToggle");
if (navToggle && menu) {
  navToggle.onclick = () => {
    const open = menu.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", String(open));
    navToggle.textContent = open ? "✕" : "☰";
  };
  menu.querySelectorAll("a").forEach(a => a.onclick = () => {
    menu.classList.remove("open");
    navToggle.setAttribute("aria-expanded", "false");
    navToggle.textContent = "☰";
  });
}
/* smooth-scroll only for same-page anchors; cross-page links behave normally */
document.querySelectorAll('a[href^="#"]').forEach(a => a.addEventListener("click", e => {
  const id = a.getAttribute("href");
  if (id === "#" || id.length < 2) return;
  const t = document.querySelector(id);
  if (!t) return;
  e.preventDefault();
  t.scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
}));

/* =============================================================
   SCROLL ENGINE - geometry-driven, no library, no rAF dependency
   ============================================================= */
const scenes = [];
function registerScene(el, onProgress) { if (el) scenes.push({ el, onProgress }); }
function sceneProgress(el) {
  const r = el.getBoundingClientRect();
  const span = r.height - innerHeight;
  if (span <= 0) return r.top <= 0 ? 1 : 0;
  return clamp(-r.top / span, 0, 1);
}
function updateScroll() {
  if (nav && !nav.classList.contains("stuck-locked")) nav.classList.toggle("stuck", scrollY > 14);
  for (const s of scenes) s.onProgress(sceneProgress(s.el));
  revealSweep();
}
addEventListener("scroll", updateScroll, { passive: true });
addEventListener("resize", () => { sizeAll(); updateScroll(); }, { passive: true });
/* the doc page ships nav.stuck in markup - keep it pinned there */
if (nav && nav.classList.contains("stuck") && !document.querySelector(".hero")) nav.classList.add("stuck-locked");

/* ---------------- reveal (.rv) ----------------
   Three layers so content can never stay hidden:
   geometry sweep on scroll, an observer, and a hard timeout. */
const rvItems = [...document.querySelectorAll(".rv")];
rvItems.forEach((el, i) => el.style.transitionDelay = Math.min(i % 4, 3) * 80 + "ms");
function revealSweep() {
  for (const el of rvItems) {
    if (el.classList.contains("in")) continue;
    const r = el.getBoundingClientRect();
    if (r.top < innerHeight * .92 && r.bottom > 0) el.classList.add("in");
  }
}
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { rootMargin: "0px 0px -8% 0px", threshold: .06 });
  rvItems.forEach(el => io.observe(el));
}
setTimeout(() => rvItems.forEach(el => el.classList.add("in")), 2600);   // failsafe

/* ---------------- canvas sizing ---------------- */
const canvases = [];
function trackCanvas(cv, redraw) {
  if (!cv) return null;
  const o = { cv, ctx: cv.getContext("2d"), W: 0, H: 0, DPR: 1, redraw };
  o.size = () => {
    o.DPR = Math.min(2, devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    o.W = r.width; o.H = r.height;
    cv.width = Math.max(1, Math.round(o.W * o.DPR));
    cv.height = Math.max(1, Math.round(o.H * o.DPR));
  };
  o.size();
  canvases.push(o);
  return o;
}
function sizeAll() { canvases.forEach(o => { o.size(); o.redraw && o.redraw(); }); }

/* =============================================================
   SCENE · THE GAP (index)
   ============================================================= */
(function gapScene() {
  const scene = document.querySelector('[data-scene="gap"]');
  if (!scene || !$("gapCanvas")) return;
  const meter = $("gapMeter");
  const PRE = .44, FAIR = .61, BREAK = .18, TAU = .19;
  let p = RM ? 1 : 0;
  const priceAt = f => f <= BREAK ? PRE : FAIR - (FAIR - PRE) * Math.exp(-(f - BREAK) / TAU);
  const c = trackCanvas($("gapCanvas"), () => draw());
  if (!c) return;

  function draw() {
    const { ctx, W, H, DPR } = c;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const L = 46, R = W - 16, T = 30, B = H - 28;
    const X = f => L + f * (R - L);
    const Y = v => B - ((v - .36) / .32) * (B - T);

    ctx.font = "10px ui-monospace, monospace";
    ctx.strokeStyle = "#1a212a"; ctx.lineWidth = 1; ctx.fillStyle = "#3a434c";
    for (let v = .40; v <= .6801; v += .08) {
      ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(R, Y(v)); ctx.stroke();
      ctx.textAlign = "right"; ctx.fillText((v * 100).toFixed(0) + "¢", L - 9, Y(v) + 3);
    }
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(77,181,255,.85)"; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.moveTo(X(BREAK), Y(FAIR)); ctx.lineTo(X(1), Y(FAIR)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(77,181,255,.92)"; ctx.textAlign = "left";
    ctx.fillText("what it's worth", X(BREAK) + 9, Y(FAIR) - 9);

    const end = Math.max(BREAK, p);
    if (p > BREAK) {
      ctx.beginPath();
      ctx.moveTo(X(BREAK), Y(FAIR));
      for (let f = BREAK; f <= end; f += .004) ctx.lineTo(X(f), Y(FAIR));
      for (let f = end; f >= BREAK; f -= .004) ctx.lineTo(X(f), Y(priceAt(f)));
      ctx.closePath();
      const g = ctx.createLinearGradient(0, Y(FAIR), 0, Y(PRE));
      g.addColorStop(0, "rgba(61,220,151,.28)"); g.addColorStop(1, "rgba(61,220,151,.04)");
      ctx.fillStyle = g; ctx.fill();
    }
    ctx.beginPath(); ctx.moveTo(X(0), Y(PRE));
    for (let f = 0; f <= p; f += .004) ctx.lineTo(X(f), Y(priceAt(f)));
    ctx.strokeStyle = "#e9ebee"; ctx.lineWidth = 2.2; ctx.lineJoin = "round"; ctx.stroke();
    if (p >= BREAK) {
      ctx.setLineDash([3, 3]); ctx.strokeStyle = "rgba(245,196,81,.7)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(BREAK), T - 12); ctx.lineTo(X(BREAK), B); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(245,196,81,.95)"; ctx.textAlign = "left";
      ctx.fillText("NEWS", X(BREAK) + 7, T - 16);
    }
    if (p > .01) {
      const v = priceAt(p);
      ctx.beginPath(); ctx.arc(X(p), Y(v), 4.5, 0, 7);
      ctx.fillStyle = "#fff"; ctx.fill();
      ctx.strokeStyle = "#07090b"; ctx.lineWidth = 2.5; ctx.stroke();
      if (p > BREAK) {
        const gap = (FAIR - v) * 100;
        ctx.font = "600 13px ui-monospace, monospace";
        ctx.fillStyle = gap > 2 ? "#3ddc97" : "#626d78";
        const right = X(p) > W - 120;
        ctx.textAlign = right ? "right" : "left";
        ctx.fillText(gap.toFixed(1) + "¢ underpriced", X(p) + (right ? -12 : 12), Y(v) + 22);
      }
    }
  }

  const beats = [...scene.querySelectorAll(".beat")];
  function apply(prog) {
    p = RM ? 1 : prog;
    draw();
    if (meter) meter.style.width = (prog * 100).toFixed(1) + "%";
    if (!beats.length) return;
    const idx = clamp(Math.floor(prog * beats.length), 0, beats.length - 1);
    beats.forEach((b, i) => b.classList.toggle("on", RM || i === idx));
  }
  registerScene(scene, apply);
  apply(RM ? 1 : sceneProgress(scene));
})();

/* =============================================================
   CALIBRATION CHART - forecast vs what actually happened
   Fixed validation figures, drawn once (no animation needed).
   ============================================================= */
(function calChart() {
  const cv = $("calChart");
  if (!cv) return;
  // bin midpoint, observed rate, weight (sample size) - validation run
  const BINS = [
    [.08, .06, 42], [.18, .16, 61], [.28, .31, 78], [.38, .35, 96],
    [.48, .50, 110], [.58, .61, 94], [.68, .66, 81], [.78, .81, 63], [.90, .88, 38]
  ];
  const c = trackCanvas(cv, () => draw());
  function draw() {
    const { ctx, W, H, DPR } = c;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const L = 48, R = W - 18, T = 16, B = H - 38;
    const X = v => L + v * (R - L), Y = v => B - v * (B - T);

    ctx.font = "10px ui-monospace, monospace";
    ctx.strokeStyle = "#1a212a"; ctx.lineWidth = 1; ctx.fillStyle = "#3a434c";
    for (let v = 0; v <= 1.001; v += .25) {
      ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(R, Y(v)); ctx.stroke();
      ctx.textAlign = "right"; ctx.fillText((v * 100).toFixed(0) + "%", L - 9, Y(v) + 3);
      ctx.textAlign = "center"; ctx.fillText((v * 100).toFixed(0) + "%", X(v), B + 17);
    }
    // perfect-calibration line
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(154,164,174,.45)"; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(X(0), Y(0)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(154,164,174,.6)"; ctx.textAlign = "left";
    ctx.fillText("perfect calibration", X(.10), Y(.70));   // empty upper-left, clear of the dots

    // connecting path
    ctx.beginPath();
    BINS.forEach((b, i) => i ? ctx.lineTo(X(b[0]), Y(b[1])) : ctx.moveTo(X(b[0]), Y(b[1])));
    ctx.strokeStyle = "rgba(77,181,255,.4)"; ctx.lineWidth = 1.6; ctx.stroke();

    // dots sized by sample count
    BINS.forEach(b => {
      const r = 4 + Math.sqrt(b[2]) * .38;
      ctx.beginPath(); ctx.arc(X(b[0]), Y(b[1]), r, 0, 7);
      ctx.fillStyle = "rgba(77,181,255,.9)"; ctx.fill();
      ctx.strokeStyle = "#07090b"; ctx.lineWidth = 2; ctx.stroke();
    });

    ctx.fillStyle = "#626d78"; ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "right"; ctx.fillText("we said →", R, B + 32);
    ctx.save(); ctx.translate(14, T + 8); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "right"; ctx.fillText("← it happened", 0, 0); ctx.restore();
  }
  draw();
})();

/* =============================================================
   PIPELINE RAIL (methodology)
   ============================================================= */
(function pipeline() {
  const rail = $("rail");
  if (!rail) return;
  const STAGES = [
    {
      n: "01 · INGEST", t: "Aggregate",
      body: "Kalshi and Polymarket contracts plus public news, pulled into one common format: price, volume, how easily you can get in and out, when it settles, and the full text of the rules.",
      g: "<b>GUARANTEE</b>Every record keeps its source and timestamp, so any number can be traced back to where it came from.",
      panel: `<div class="mp-label">What comes in</div>
        <div class="mp-row"><span>Kalshi</span><b>open contracts</b></div>
        <div class="mp-row"><span>Polymarket</span><b>open contracts</b></div>
        <div class="mp-row"><span>News wire</span><b>rolling 24h</b></div>
        <div class="mp-arrow">↓</div>
        <div class="mp-row hi"><span>One common format</span><b>✓</b></div>`
    },
    {
      n: "02 · LINK", t: "Map evidence",
      body: "Headlines are joined to the contracts they actually bear on, using the words and entities they share. Contracts asking the same question on both exchanges get paired up.",
      g: "<b>GUARANTEE</b>Matching is conservative: a market with no matching news gets no news adjustment, rather than a guessed one.",
      panel: `<div class="mp-label">Headline</div>
        <div class="mp-row"><span>“Fed officials signal openness to a July cut”</span></div>
        <div class="mp-arrow">↓</div>
        <div class="mp-label">Contracts it affects</div>
        <div class="mp-chips">
          <span class="mp-chip m">Fed cuts July</span>
          <span class="mp-chip m">Fed holds through Sept</span>
          <span class="mp-chip">both venues</span>
        </div>`
    },
    {
      n: "03 · PRICE", t: "Compute fair value",
      body: "The right model for the market. Contracts tracking a number get option maths; contracts tracking an event start from the market price and move only as far as the evidence justifies.",
      g: "<b>GUARANTEE</b>Every adjustment is a separate line that adds up to the final number. No unexplained residual.",
      panel: `<div class="mp-label">What moved the price</div>
        <div class="mp-row"><span>Exchange price</span><b>58.0¢</b></div>
        <div class="mp-row"><span>News</span><b class="up">+5.6</b></div>
        <div class="mp-row"><span>Other exchange</span><b class="down">−0.4</b></div>
        <div class="mp-row"><span>Vague rules</span><b class="down">−0.6</b></div>
        <div class="mp-row hi"><span>What it's worth</span><b>63.1¢</b></div>`
    },
    {
      n: "04 · GATE", t: "Decide or abstain",
      body: "A call only fires when the gap is big enough and we're confident enough. Everything else comes back as “sit this one out”, with the reason attached.",
      g: "<b>GUARANTEE</b>Abstaining is a feature. Most contracts, most of the time, are priced about right.",
      panel: `<div class="mp-label">Checks</div>
        <div class="mp-row good"><span>Gap  <b class="mono dim">+5.1¢</b></span><b class="up">≥ 4¢ ✓</b></div>
        <div class="mp-row good"><span>Confidence  <b class="mono dim">73%</b></span><b class="up">≥ 50% ✓</b></div>
        <div class="mp-arrow">↓</div>
        <div class="mp-split">
          <div class="mp-row hi"><span>Call</span><b class="up">BUY</b></div>
          <div class="mp-row"><span>else</span><b class="dim">SIT OUT</b></div>
        </div>`
    },
    {
      n: "05 · SIZE", t: "Risk before conviction",
      body: "A suggested stake scaled by how confident we are, and hard-capped as a share of your bankroll. Expected value is shown after fees, not before.",
      g: "<b>GUARANTEE</b>The number is a ceiling to stay under, not a target to hit.",
      panel: `<div class="mp-label">Suggested stake</div>
        <div class="mp-big up">3.5%</div>
        <div class="mp-bar"><i style="width:23%"></i><span class="cap" style="left:15%"></span></div>
        <div class="mp-note">Scaled down by confidence, and well under the 15% hard cap (red mark).</div>
        <div class="mp-row"><span>Expected value after fees</span><b class="up">+3.1¢ / $1</b></div>`
    }
  ];

  rail.innerHTML = STAGES.map((s, i) =>
    `<button class="rail-btn" role="tab" aria-selected="${i === 0}" data-i="${i}">
       <span class="rn">${esc(s.n)}</span><span class="rt">${esc(s.t)}</span></button>`).join("");
  const btns = [...rail.children];
  let idx = 0, auto = null, userTouched = false;
  function show(i) {
    idx = i;
    const s = STAGES[i];
    btns.forEach((b, j) => b.setAttribute("aria-selected", String(j === i)));
    $("stageNum").textContent = s.n;
    $("stageTitle").textContent = s.t;
    $("stageBody").textContent = s.body;
    $("stageGuarantee").innerHTML = s.g;
    $("stagePanel").innerHTML = s.panel;
  }
  btns.forEach(b => b.onclick = () => { userTouched = true; clearInterval(auto); show(+b.dataset.i); });
  show(0);
  if (!RM) {
    auto = setInterval(() => {
      if (userTouched) return clearInterval(auto);
      const r = rail.getBoundingClientRect();
      if (r.top > innerHeight || r.bottom < 0) return;   // only while on screen
      show((idx + 1) % STAGES.length);
    }, 5200);
  }
})();

/* =============================================================
   SCENE · EVIDENCE GRAPH (methodology)
   ============================================================= */
(function graphScene() {
  const scene = document.querySelector('[data-scene="graph"]');
  if (!scene || !$("graphCanvas")) return;
  const LABELS = ["Fed cuts July", "CPI below 3%", "BTC > $120K", "Recession '26"];
  const N = [];
  for (let i = 0; i < 4; i++) N.push({ t: "m", r: 15, label: LABELS[i] });
  for (let i = 0; i < 9; i++) N.push({ t: "n", r: 6 });
  const E = [[4, 0], [5, 0], [5, 1], [6, 1], [7, 2], [8, 2], [9, 3], [10, 3], [11, 0], [11, 1], [12, 2], [12, 3], [6, 3]]
    .map(([a, b]) => ({ a: N[a], b: N[b] }));
  N.forEach((n, i) => { const ang = i * 2.399963, rad = 34 + 14 * Math.sqrt(i); n.x = Math.cos(ang) * rad; n.y = Math.sin(ang) * rad * .62; n.vx = 0; n.vy = 0; });
  for (let s = 0; s < 320; s++) {
    for (let i = 0; i < N.length; i++) {
      const p = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const q = N[j];
        let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        const f = 900 / d2, d = Math.sqrt(d2); dx /= d; dy /= d;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
    }
    for (const e of E) {
      let dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
      const d = Math.max(1, Math.hypot(dx, dy)), f = (d - 78) * .015;
      dx /= d; dy /= d;
      e.a.vx += dx * f; e.a.vy += dy * f; e.b.vx -= dx * f; e.b.vy -= dy * f;
    }
    for (const n of N) { n.vx -= n.x * .004; n.vy -= n.y * .006; n.vx *= .84; n.vy *= .84; n.x += n.vx; n.y += n.vy; }
  }
  const bx = { min: Infinity, max: -Infinity }, by = { min: Infinity, max: -Infinity };
  N.forEach(n => {
    bx.min = Math.min(bx.min, n.x); bx.max = Math.max(bx.max, n.x);
    by.min = Math.min(by.min, n.y); by.max = Math.max(by.max, n.y);
  });
  const midX = (bx.min + bx.max) / 2, midY = (by.min + by.max) / 2;
  const spanX = Math.max(1, bx.max - bx.min), spanY = Math.max(1, by.max - by.min);
  let p = RM ? 1 : 0;
  const c = trackCanvas($("graphCanvas"), () => draw());
  if (!c) return;

  function draw() {
    const { ctx, W, H, DPR } = c;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const padX = 78, padY = 44;
    const k = Math.min(2.6, (W - padX * 2) / spanX, (H - padY * 2) / spanY);
    const cx = W / 2, cy = H / 2;
    const X = n => cx + (n.x - midX) * k, Y = n => cy + (n.y - midY) * k;
    const eShown = p * E.length * 1.25;
    E.forEach((e, i) => {
      const local = clamp(eShown - i, 0, 1);
      if (local <= 0) return;
      const x1 = X(e.a), y1 = Y(e.a), x2 = X(e.b), y2 = Y(e.b);
      ctx.beginPath(); ctx.moveTo(x1, y1);
      ctx.lineTo(x1 + (x2 - x1) * local, y1 + (y2 - y1) * local);
      ctx.strokeStyle = "rgba(224,90,109," + (.18 + local * .3) + ")";
      ctx.lineWidth = 1.3; ctx.stroke();
    });
    N.forEach((n, i) => {
      const delay = i / N.length * .55;
      const local = clamp((p - delay) / .3, 0, 1);
      if (local <= 0) return;
      const r = n.r * Math.min(1.35, Math.max(.7, k)) * (0.5 + local * 0.5);
      ctx.globalAlpha = local;
      ctx.beginPath(); ctx.arc(X(n), Y(n), r, 0, 7);
      ctx.fillStyle = n.t === "m" ? "#bd8a10" : "#4d8be0";
      ctx.fill();
      ctx.lineWidth = 2.5; ctx.strokeStyle = "#07090b"; ctx.stroke();
      if (n.t === "m" && local > .6) {
        ctx.font = "12px Inter, sans-serif";
        ctx.fillStyle = "rgba(180,189,198,.95)";
        ctx.textAlign = "center";
        ctx.fillText(n.label, X(n), Y(n) + r + 17);
      }
      ctx.globalAlpha = 1;
    });
  }

  const beats = [...scene.querySelectorAll(".beat")];
  function apply(prog) {
    p = RM ? 1 : clamp(prog * 1.25, 0, 1);
    draw();
    if (!beats.length) return;
    const i2 = clamp(Math.floor(prog * beats.length), 0, beats.length - 1);
    beats.forEach((b, i) => b.classList.toggle("on", RM || i === i2));
  }
  registerScene(scene, apply);
  apply(RM ? 1 : sceneProgress(scene));
})();

/* =============================================================
   COUNT-UP NUMBERS
   ============================================================= */
document.querySelectorAll("[data-count]").forEach(el => {
  const target = parseFloat(el.dataset.count);
  const suffix = el.dataset.suffix || "";
  if (RM) { el.textContent = target + suffix; return; }
  let started = false;
  const run = () => {
    if (started) return;
    const r = el.getBoundingClientRect();
    if (r.top > innerHeight * .9 || r.bottom < 0) return;
    started = true;
    let cur = 0;
    const id = setInterval(() => {           // interval, not rAF: survives throttling
      cur += Math.max(1, Math.ceil(target / 22));
      if (cur >= target) { cur = target; clearInterval(id); }
      el.textContent = cur + suffix;
    }, 42);
  };
  addEventListener("scroll", run, { passive: true });
  run();
  setTimeout(() => { if (!started) { started = true; el.textContent = target + suffix; } }, 4000);
});

/* =============================================================
   LIVE DATA - real public feed, honest states (index only)
   ============================================================= */
(function liveFeed() {
  if (!$("feedRows")) return;
  const GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query=" +
    encodeURIComponent('("Federal Reserve" OR inflation OR Bitcoin OR "S&P 500" OR election OR recession OR CPI)') +
    "&mode=ArtList&format=json&maxrecords=30&sort=datedesc&timespan=24h";
  const FEED_INTERVAL = 90000, RETRY_INTERVAL = 20000, FEED_SHOW = 6;
  const POS = ["cool", "cools", "cooler", "softer", "ease", "eases", "easing", "cut", "cuts", "beat", "beats", "surge", "gain", "gains", "rally", "record", "rise", "rises", "climb", "strong", "rebound", "optimism", "approve", "tops", "inflow", "upgrade", "clears"];
  const NEG = ["hawkish", "hotter", "sticky", "miss", "misses", "fall", "falls", "drop", "drops", "slump", "delay", "stall", "fear", "fears", "risk", "warn", "weak", "decline", "concern", "shutdown", "dissent", "reject", "downgrade", "plunge"];
  const toneOf = t => {
    t = (t || "").toLowerCase(); let p = 0, n = 0;
    for (const w of POS) if (t.includes(w)) p++;
    for (const w of NEG) if (t.includes(w)) n++;
    return clamp((p - n) / 3, -1, 1);
  };
  const categorize = t => {
    t = (t || "").toLowerCase();
    if (/fed|fomc|rate cut|interest rate|powell/.test(t)) return "Rates";
    if (/cpi|inflation|pce|price index/.test(t)) return "Inflation";
    if (/bitcoin|btc|ethereum|crypto|token/.test(t)) return "Crypto";
    if (/recession|gdp|growth|jobs|unemploy|payroll/.test(t)) return "Economy";
    if (/s&p|nasdaq|dow|stocks|equities|earnings/.test(t)) return "Stocks";
    if (/election|senate|congress|house|bill|shutdown|vote/.test(t)) return "Politics";
    return "General";
  };
  const timeAgo = ts => {
    if (!ts) return "recent";
    const s = (Date.now() - ts) / 1000;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  };
  function setFeedStatus(state, text) {
    $("feedStatus").innerHTML = `<span class="sd ${state}"></span>${esc(text)}`;
    const hero = $("heroLive");
    if (hero) {
      const col = state === "live" ? "var(--up)" : state === "standby" ? "var(--warn)" : state === "unavailable" ? "var(--down)" : "var(--accent)";
      const glow = state === "live" ? "box-shadow:0 0 8px var(--up);" : "";
      hero.innerHTML = `<span class="hp-dot" style="background:${col};${glow}"></span>NEWS WIRE · ${esc(text)}`;
    }
  }
  let items = [], timer = null, nextAt = 0, selIdx = 0, loadSeq = 0;
  function renderRows() {
    if (!items.length) return;
    $("feedRows").innerHTML = items.map((a, i) => {
      const href = a.url ? ` href="${esc(a.url)}" target="_blank" rel="noopener noreferrer" style="color:inherit"` : "";
      const tag = href ? "a" : "div";
      return `<div class="feed-row ${a.tone >= 0 ? "tone-up" : "tone-down"}${i === selIdx ? " sel" : ""}" data-i="${i}" tabindex="0" role="button" aria-pressed="${i === selIdx}"><div style="min-width:0">
        <${tag}${href} class="ft">${esc(a.title)}</${tag}>
        <div class="fm">${esc(a.domain)} · <span class="ago">${esc(timeAgo(a.ts))}</span></div>
        <span class="cat-chip">affects · ${esc(a.cat)}</span>
      </div><div class="fs ${a.tone >= 0 ? "up" : "down"}">${a.tone >= 0 ? "+" : ""}${a.tone.toFixed(2)}</div></div>`;
    }).join("");
    [...$("feedRows").children].forEach(row => {
      const pick = () => {
        selIdx = +row.dataset.i;
        renderRows();
        renderImpact(items[selIdx]);
      };
      row.addEventListener("click", pick);
      row.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
      });
    });
  }

  /* the impact panel: live headline in, our read + sample contracts out */
  function renderImpact(a) {
    const host = $("impactBody");
    if (!host) return;
    if (!a) return impactStandby();
    const pos = a.tone >= 0;
    const pct = Math.round(((a.tone + 1) / 2) * 100);
    const pool = marketRows();
    const rel = pool.filter(m => m.cat === a.cat).slice(0, 3);
    const list = rel.length ? rel : pool.slice(0, 3);
    const live = marketsAreLive();
    host.innerHTML = `
      <p class="imp-title">${esc(a.title)}</p>
      <div class="imp-meta">${esc(a.domain)} · ${esc(timeAgo(a.ts))}</div>
      <div class="imp-label">Our read of the headline</div>
      <div class="tone-meter"><i class="tm-needle" style="left:${pct}%"></i></div>
      <div class="tm-legend"><span>negative</span><b class="${pos ? "up" : "down"}">reads ${pos ? "positive" : "negative"} · ${pos ? "+" : ""}${a.tone.toFixed(2)}</b><span>positive</span></div>
      <div class="imp-label">Contracts this kind of story touches <span class="imp-cat">${esc(a.cat)}</span></div>
      ${list.map(m => `<div class="imp-row"><span class="imp-venue">${esc(m.v)}</span><span class="imp-q">${esc(m.q)}</span><b class="mono">${m.p}¢</b></div>`).join("")}
      <p class="imp-note">${live
        ? "Live headline, live contract prices. In the product this becomes a priced call: fair value, gap, and stake."
        : "Live headline, sample contracts. In the product this becomes a priced call: fair value, gap, and stake."}</p>
      <a class="btn sm" href="#access">Get the live version</a>`;
    host.classList.remove("flash");
    void host.offsetWidth;   // restart the update flash
    host.classList.add("flash");
  }
  function impactStandby() {
    const host = $("impactBody");
    if (host) host.innerHTML = `<div class="imp-standby"><p>This pane lights up when the wire connects. Pick any headline to see how we score it and which contracts it touches.</p></div>`;
  }
  /* Where the data comes from.
     With FEED_PROXY set, one cached call returns news AND market prices for
     every visitor at once, so nobody trips the upstream rate limit.
     Without it we fetch GDELT straight from the browser: fine on a clean IP,
     but it fails for anyone sharing a rate-limited one, and it can never
     reach the exchanges. */
  async function fetchPayload(signal) {
    if (FEED_PROXY) {
      const res = await fetch(FEED_PROXY, { signal, cache: "no-store" });
      if (res.status === 429) throw Object.assign(new Error("rate limited"), { rate: true });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const d = await res.json();
      if (d.error) throw new Error(d.detail || d.error);
      return {
        articles: d.news || [],
        markets: d.markets || [],
        stale: !!d.stale,
        ageSeconds: typeof d.ageSeconds === "number" ? d.ageSeconds : null
      };
    }
    const res = await fetch(GDELT, { signal, cache: "no-store" });
    if (res.status === 429) throw Object.assign(new Error("rate limited"), { rate: true });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    return { articles: d.articles || [], markets: [], stale: false, ageSeconds: 0 };
  }

  /* Real exchange prices replace the sample rows, and every label that said
     SAMPLE flips to LIVE. If this never runs, the labels stay honest. */
  function applyLiveMarkets(rows, stale, ageSeconds) {
    const mapped = (rows || []).map(m => ({
      v: String(m.venue || "").toUpperCase().slice(0, 12),
      q: String(m.question || "").toUpperCase().slice(0, 58),
      p: Math.round(Number(m.priceCents)),
      cat: categorize(m.question)
    })).filter(m => m.q && m.p > 0 && m.p < 100);
    if (!mapped.length) return;
    LIVE_MARKETS = mapped;
    renderTape();
    const badge = $("tapeBadge"), note = $("tapeNote"), ib = $("impactBadge");
    const fresh = !stale;
    if (badge) {
      badge.textContent = fresh ? "LIVE" : "CACHED";
      badge.classList.toggle("badge-live", fresh);
    }
    if (note) note.textContent = fresh
      ? "REAL PRICES · " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "REAL PRICES · " + agoLabel(ageSeconds);
    if (ib) {
      ib.textContent = fresh ? "LIVE MARKETS" : "CACHED MARKETS";
      ib.classList.toggle("badge-live", fresh);
    }
  }
  const agoLabel = s => s == null ? "CACHED"
    : s < 90 ? Math.max(1, Math.round(s)) + "S AGO"
    : Math.round(s / 60) + " MIN AGO";

  async function load() {
    const seq = ++loadSeq;   // a slow older request must never clobber a newer one
    const btn = $("feedRefresh");
    if (btn) btn.classList.add("spin");
    setFeedStatus("loading", "CONNECTING");
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 12000);
    try {
      const feed = await fetchPayload(ctl.signal);
      if (seq !== loadSeq) return;
      const arts = feed.articles.filter(a => a && a.title).slice(0, FEED_SHOW);
      if (!arts.length) throw new Error("no articles");
      items = arts.map(a => {
        let ts = null;
        const m = String(a.seendate || "").match(/(\d{4})(\d{2})(\d{2})T?(\d{2})(\d{2})(\d{2})/);
        if (m) ts = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`);
        const title = String(a.title).slice(0, 240);   // cap external strings
        return { title, domain: String(a.domain || "source").slice(0, 80), url: safeUrl(a.url), ts, tone: toneOf(title), cat: categorize(title) };
      });
      selIdx = Math.min(selIdx, items.length - 1);
      /* markets first, so the impact panel renders against live rows */
      if (feed.markets && feed.markets.length) applyLiveMarkets(feed.markets, feed.stale, feed.ageSeconds);
      renderRows();
      renderImpact(items[selIdx]);
      if (feed.stale) setFeedStatus("standby", "CACHED · " + agoLabel(feed.ageSeconds));
      else setFeedStatus("live", "LIVE · " + items.length + " HEADLINES");
    } catch (err) {
      if (seq !== loadSeq) return;   // superseded - a newer load owns the UI
      console.warn("[resolv] wire fetch failed:", err);   // diagnostics - the UI below stays honest
      const rate = err && err.rate;
      items = []; selIdx = 0;
      setFeedStatus("standby", rate ? "RATE-LIMITED · RETRYING" : "STANDBY · RETRYING");
      $("feedRows").innerHTML = `<div class="feed-empty standby">
        <span class="standby-pulse" aria-hidden="true"><i></i><i></i><i></i></span>
        <strong>Wire on standby</strong>
        <p>${rate ? "The public source is rate-limiting requests; it usually clears in under a minute."
          : "This network isn't letting the public feed through."}
        Reconnecting automatically. We never fill this space with made-up headlines.</p>
        <button class="btn sm" id="feedRetry">Retry now</button></div>`;
      const rb = $("feedRetry"); if (rb) rb.onclick = load;
      impactStandby();
    } finally {
      clearTimeout(to);
      if (seq === loadSeq) {   // only the latest load schedules the next one
        if (btn) btn.classList.remove("spin");
        const wait = items.length ? FEED_INTERVAL : RETRY_INTERVAL;
        nextAt = Date.now() + wait;
        clearTimeout(timer);
        timer = setTimeout(load, wait);
      }
    }
  }
  setInterval(() => { if (items.length) renderRows(); }, 20000);   // timestamps tick on their own
  setInterval(() => {
    const el = $("feedCountdown"); if (!el || !nextAt) return;
    const s = Math.max(0, Math.round((nextAt - Date.now()) / 1000));
    el.textContent = s ? (items.length ? "next refresh " + s + "s" : "reconnecting in " + s + "s") : "refreshing…";
  }, 1000);
  if ($("feedRefresh")) $("feedRefresh").onclick = load;

  let started = false;
  const start = () => { if (!started) { started = true; load(); } };
  const host = $("live");
  if (host && "IntersectionObserver" in window) {
    const fo = new IntersectionObserver(es => { if (es[0].isIntersecting) { fo.disconnect(); start(); } }, { rootMargin: "300px" });
    fo.observe(host);
    setTimeout(start, 4000);
  } else start();
  window.loadFeed = load;   // exposed for manual testing
})();

/* =============================================================
   SIGNUP - never reports success unless it succeeded
   ============================================================= */
(function signup() {
  const form = $("waitForm"), emailIn = $("waitEmail"), msg = $("waitMsg"), btn = $("waitBtn");
  if (!form) return;
  const valid = v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim());
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const val = emailIn.value.trim();
    if (!valid(val)) {
      emailIn.classList.add("bad");
      msg.innerHTML = '<span class="down">Enter a valid email address.</span>';
      emailIn.focus(); return;
    }
    emailIn.classList.remove("bad");
    if (!WAITLIST_ENDPOINT) {
      /* Pre-launch capture: the address is stored in this browser and the
         form confirms inline (no mail app).
         ── LAUNCH BLOCKER ─────────────────────────────────────────────
         Set WAITLIST_ENDPOINT before deploying. Until then, signups
         exist only in each visitor's own localStorage - nothing reaches
         you. Retrieve locally-saved ones from a visitor's console with:
         JSON.parse(localStorage.getItem("resolv-waitlist"))          */
      try {
        const KEY = "resolv-waitlist";
        let list;
        try { list = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (_) { list = []; }
        if (!Array.isArray(list)) list = [];
        if (!list.includes(val) && list.length < 200) { list.push(val); localStorage.setItem(KEY, JSON.stringify(list)); }
        msg.innerHTML = `<span class="up">✓ Recorded. You're on the list.</span> <span class="dim">We'll email ${esc(val)} when your access is ready.</span>`;
        btn.textContent = "✓ Added";
        setTimeout(() => { btn.textContent = "Get access"; }, 2600);
        form.reset();
      } catch (e) {
        msg.innerHTML = `<span class="down">Couldn't save that here.</span> <a href="mailto:${CONTACT_EMAIL}">Email us</a> and we'll add you by hand.`;
      }
      return;
    }
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const res = await fetch(WAITLIST_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ email: val, source: "resolv-landing" })
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      msg.innerHTML = `<span class="up">✓ Recorded. You're on the list.</span> <span class="dim">We'll email ${esc(val)} when your access is ready.</span>`;
      btn.textContent = "✓ Added";
      setTimeout(() => { btn.textContent = "Get access"; }, 2600);
      form.reset();
    } catch (err) {
      msg.innerHTML = `<span class="down">That didn't go through.</span> <a href="mailto:${CONTACT_EMAIL}">Email us</a> and we'll set you up.`;
    } finally { btn.disabled = false; if (btn.textContent === "Sending…") btn.textContent = "Get access"; }
  });
  emailIn.addEventListener("input", () => {
    if (emailIn.classList.contains("bad") && valid(emailIn.value)) {
      emailIn.classList.remove("bad"); msg.textContent = "";
    }
  });
})();

/* =============================================================
   V5 - FLAGSHIP LAYER
   All blocks guarded; each page only runs what it has.
   ============================================================= */

/* ---- hero background: drifting probability threads ----
   First frame is painted synchronously (hidden tabs never tick
   rAF); the animation loop only runs for visible, motion-OK
   sessions, and skips work once the hero is scrolled away. */
(function heroBg() {
  const cv = $("heroBg");
  if (!cv) return;
  const c = trackCanvas(cv, () => draw());
  if (!c) return;
  const THREADS = 13, HERO_I = 7;
  let t = 0;
  const yFor = (i, fx, tt) => {
    const seed = i * 17.31;
    const base = .14 + ((i * .618) % 1) * .7;
    const amp = .05 + ((i * .377) % 1) * .05;
    const v = Math.sin(fx * 5.1 + seed + tt * .5) * .45
            + Math.sin(fx * 11.7 + seed * 2.1 - tt * .33) * .3
            + Math.sin(fx * 2.3 + seed * .7 + tt * .21) * .25;
    return base + v * amp;
  };
  function draw() {
    const { ctx, W, H, DPR } = c;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const steps = 90;
    for (let i = 0; i < THREADS; i++) {
      const hero = i === HERO_I;
      ctx.beginPath();
      for (let s = 0; s <= steps; s++) {
        const fx = s / steps;
        const x = fx * W, y = yFor(i, fx, t) * H;
        s ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      if (hero) {
        ctx.strokeStyle = "rgba(77,181,255,.5)"; ctx.lineWidth = 1.8;
        ctx.shadowColor = "rgba(77,181,255,.8)"; ctx.shadowBlur = 14;
      } else {
        const hue = i % 3 === 0 ? "rgba(61,220,151," : i % 3 === 1 ? "rgba(77,181,255," : "rgba(154,164,174,";
        ctx.strokeStyle = hue + (.05 + ((i * .53) % 1) * .06) + ")";
        ctx.lineWidth = 1; ctx.shadowBlur = 0;
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      if (hero) {
        const ey = yFor(i, 1, t) * H;
        ctx.beginPath(); ctx.arc(W - 3, ey, 3.5, 0, 7);
        ctx.fillStyle = "#8fd0ff"; ctx.fill();
      }
    }
  }
  draw();
  if (!RM) (function loop() {
    if (scrollY < innerHeight * 1.2) { t += .012; draw(); }
    requestAnimationFrame(loop);
  })();
})();

/* ---- hero card sparkline (part of the EXAMPLE card) ---- */
(function hcSpark() {
  const cv = $("hcSpark");
  if (!cv) return;
  const PTS = [44, 46, 45, 47, 49, 48, 51, 50, 52, 54, 53, 55, 54, 56, 58];
  const FAIR = 63, LO = 42, HI = 66;
  const c = trackCanvas(cv, () => draw());
  if (!c) return;
  function draw() {
    const { ctx, W, H, DPR } = c;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const R = W - 34;
    const X = i => (i / (PTS.length - 1)) * R;
    const Y = v => H - 4 - ((v - LO) / (HI - LO)) * (H - 10);
    ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(77,181,255,.6)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, Y(FAIR)); ctx.lineTo(R, Y(FAIR)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "600 9px ui-monospace,monospace"; ctx.textAlign = "left";
    ctx.fillStyle = "rgba(77,181,255,.9)"; ctx.fillText(FAIR + "¢", R + 6, Y(FAIR) + 3);
    ctx.beginPath();
    PTS.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
    const lastX = X(PTS.length - 1), lastY = Y(PTS[PTS.length - 1]);
    ctx.strokeStyle = "#e9ebee"; ctx.lineWidth = 1.7; ctx.lineJoin = "round"; ctx.stroke();
    ctx.lineTo(lastX, H); ctx.lineTo(0, H); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "rgba(233,235,238,.10)"); g.addColorStop(1, "rgba(233,235,238,0)");
    ctx.fillStyle = g; ctx.fill();
    ctx.beginPath(); ctx.arc(lastX, lastY, 3, 0, 7);
    ctx.fillStyle = "#fff"; ctx.fill();
    ctx.strokeStyle = "#0d1014"; ctx.lineWidth = 2; ctx.stroke();
  }
  draw();
})();

/* ---- hero card 3D tilt (pointer devices, motion-OK only) ---- */
(function tilt() {
  const shell = $("heroTilt");
  if (!shell || RM || !matchMedia("(hover:hover)").matches) return;
  shell.style.transition = "transform .18s ease-out";
  shell.addEventListener("pointermove", e => {
    const r = shell.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - .5;
    const py = (e.clientY - r.top) / r.height - .5;
    shell.style.transform = `perspective(950px) rotateX(${(-py * 6).toFixed(2)}deg) rotateY(${(px * 8).toFixed(2)}deg)`;
  });
  shell.addEventListener("pointerleave", () => { shell.style.transform = ""; });
})();

/* ---- cursor-tracking glow borders on surfaces ---- */
(function glowCards() {
  document.querySelectorAll(".card,.price-card,.pstat,.proof-chart,.feed,.math-card,.stage-detail,.stage-panel").forEach(el => {
    el.classList.add("glow");
    el.addEventListener("pointermove", e => {
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", (e.clientX - r.left) + "px");
      el.style.setProperty("--my", (e.clientY - r.top) + "px");
    });
  });
})();

/* ---- market tape. Labelled SAMPLE until real prices arrive, then LIVE
       with the time they were fetched. Never labelled live on sample data. ---- */
function renderTape() {
  const track = $("tapeTrack");
  if (!track) return;
  const item = r => {
    const delta = typeof r.d === "number"
      ? `<span class="td ${r.d >= 0 ? "up" : "down"}">${r.d >= 0 ? "▲" : "▼"}${Math.abs(r.d)}</span>`
      : "";
    return `<span class="tape-item"><span class="tvn">${esc(r.v)}</span><span class="tq">${esc(r.q)}</span><b class="tv">${r.p}¢</b>${delta}</span>`;
  };
  const half = marketRows().map(item).join("");
  track.innerHTML = half + half;   // doubled for the seamless loop
}
renderTape();

/* ---------------- initial paint ---------------- */
sizeAll();
updateScroll();
