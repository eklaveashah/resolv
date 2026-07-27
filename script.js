"use strict";
/* =============================================================
   RESOLV — landing page (marketing only; no terminal app here)
   -------------------------------------------------------------
   CONFIG: set these two before deploying.
     CONTACT_EMAIL   — where "Request access" submissions go.
     WAITLIST_ENDPOINT — optional POST URL (Formspree, Buttondown,
       your own API). If left empty, the form opens the visitor's
       email client addressed to CONTACT_EMAIL instead. The form
       NEVER claims success unless the request actually succeeded.
   ============================================================= */
const CONTACT_EMAIL = "hello@resolv.trade";
const WAITLIST_ENDPOINT = "";   // e.g. "https://formspree.io/f/xxxxxxx"

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;

$("year").textContent = new Date().getFullYear();
$("footMail").href = "mailto:" + CONTACT_EMAIL;

/* ---------------- nav ---------------- */
const nav = $("nav");
addEventListener("scroll", () => nav.classList.toggle("stuck", scrollY > 12), { passive: true });
const menu = $("mobileMenu"), navToggle = $("navToggle");
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

/* ---------------- scroll reveal ----------------
   Layered so content can never stay hidden:
     1. IntersectionObserver reveals sections as they scroll in.
     2. A rAF pass reveals anything already within the viewport.
     3. A hard failsafe reveals everything after 2.5s no matter what.  */
const revealAll = () => document.querySelectorAll(".rv").forEach(el => el.classList.add("in"));
if (!RM && "IntersectionObserver" in window) {
  const io = new IntersectionObserver(es => {
    es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
  }, { rootMargin: "0px 0px -8% 0px", threshold: .08 });
  const items = [...document.querySelectorAll(".rv")];
  items.forEach((el, i) => { el.style.transitionDelay = Math.min(i % 4, 3) * 70 + "ms"; io.observe(el); });
  // geometry fallback for anything on-screen the observer didn't report
  const sweep = () => items.forEach(el => {
    if (el.classList.contains("in")) return;
    const r = el.getBoundingClientRect();
    if (r.top < innerHeight * .94 && r.bottom > 0) { el.classList.add("in"); io.unobserve(el); }
  });
  requestAnimationFrame(sweep);
  addEventListener("scroll", sweep, { passive: true });
  addEventListener("resize", sweep, { passive: true });
  setTimeout(revealAll, 2500);   // failsafe: never leave the page blank
} else {
  revealAll();
}

/* =============================================================
   HERO — animated pricing pipeline
   Sample contracts, clearly badged SAMPLE. The animation shows the
   MODEL REASONING (factor by factor), not invented price ticks.
   ============================================================= */
const SAMPLES = [
  { q: "Fed cuts rates by 25bps at the July FOMC meeting", venue: "Kalshi", cat: "Rates",
    mkt: .58, steps: [["Exchange anchor", 0], ["Longshot de-bias", .005], ["News evidence", .056], ["Cross-venue pull", -.004], ["Ambiguity shrink", -.006]] },
  { q: "Bitcoin above $120,000 on August 1", venue: "Polymarket", cat: "Crypto",
    mkt: .44, steps: [["Exchange anchor", 0], ["Digital option N(d₂)", -.041], ["News evidence", .022], ["Cross-venue pull", -.006], ["Ambiguity shrink", .002]] },
  { q: "June CPI year-over-year below 3.0%", venue: "Kalshi", cat: "Inflation",
    mkt: .63, steps: [["Exchange anchor", 0], ["Longshot de-bias", .008], ["News evidence", .048], ["Cross-venue pull", .011], ["Ambiguity shrink", -.009]] },
  { q: "Federal AI regulation bill becomes law this year", venue: "Polymarket", cat: "Policy",
    mkt: .17, steps: [["Exchange anchor", 0], ["Longshot de-bias", -.014], ["News evidence", -.032], ["Cross-venue pull", 0], ["Ambiguity shrink", .019]] }
];
const cents = p => (p * 100).toFixed(1) + "¢";
const ptsFmt = v => (v >= 0 ? "+" : "") + (v * 100).toFixed(1);
let sIdx = 0, stepTimer = null, cycleTimer = null;

function renderSample(idx) {
  const s = SAMPLES[idx];
  $("tpMarket").textContent = s.q;
  $("tpSub").innerHTML = `<span>${esc(s.venue)}</span><span>·</span><span>${esc(s.cat)}</span><span>·</span><span>binary contract</span>`;
  $("tpMkt").textContent = cents(s.mkt);
  $("tpSteps").innerHTML = s.steps.map((st, i) => {
    const running = s.steps.slice(0, i + 1).reduce((a, x) => a + x[1], s.mkt);
    const delta = i === 0 ? "" : `<span class="sdelta ${st[1] >= 0 ? "up" : "down"}">${ptsFmt(st[1])}</span>`;
    return `<div class="tp-step" data-i="${i}">
      <span class="sname"><span class="sdot"></span>${esc(st[0])}</span>
      ${delta || "<span></span>"}
      <span class="sprob">${cents(running)}</span></div>`;
  }).join("");
  const fair = s.steps.reduce((a, x) => a + x[1], s.mkt);
  const edge = fair - s.mkt;
  const sig = Math.abs(edge) >= .04 ? (edge > 0 ? "buy" : "sell") : "hold";
  $("tpFair").textContent = "—";
  const v = $("tpVerdict");
  v.textContent = "—"; v.className = "verdict-tag hold";

  const rows = [...$("tpSteps").children];
  clearTimeout(stepTimer);
  if (RM) {
    rows.forEach(r => r.classList.add("on"));
    $("tpFair").textContent = cents(fair);
    v.textContent = sig.toUpperCase(); v.className = "verdict-tag " + sig;
    return;
  }
  let i = 0;
  const advance = () => {
    if (i < rows.length) { rows[i].classList.add("on"); i++; stepTimer = setTimeout(advance, 460); }
    else {
      $("tpFair").textContent = cents(fair);
      v.textContent = sig.toUpperCase();
      v.className = "verdict-tag " + sig;
    }
  };
  advance();
}
function cycleSamples() {
  renderSample(sIdx);
  sIdx = (sIdx + 1) % SAMPLES.length;
  cycleTimer = setTimeout(cycleSamples, RM ? 100000 : 6200);
}
cycleSamples();
/* pause the cycle while the visitor is reading it */
const tprev = $("tprev");
tprev.addEventListener("pointerenter", () => clearTimeout(cycleTimer));
tprev.addEventListener("pointerleave", () => { clearTimeout(cycleTimer); cycleTimer = setTimeout(cycleSamples, 2200); });
if (!RM && matchMedia("(pointer:fine)").matches) {
  tprev.addEventListener("pointermove", e => {
    const r = tprev.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
    tprev.style.transform = `perspective(1200px) rotateY(${(x * 3.2).toFixed(2)}deg) rotateX(${(-y * 3.2).toFixed(2)}deg)`;
  });
  tprev.addEventListener("pointerleave", () => tprev.style.transform = "");
}

/* =============================================================
   PIPELINE TABS
   ============================================================= */
const PIPE = [
  { k: "01 · INGEST", t: "Aggregate", short: "Kalshi and Polymarket contracts plus public news, normalized into one schema.",
    body: 'Open contracts from both venues are pulled from public APIs and normalized to a common shape — implied probability, 24-hour volume, a liquidity proxy, expiry, and the full resolution text. News arrives from public feeds with source domain and timestamp attached. Every record keeps its provenance.' },
  { k: "02 · LINK", t: "Map evidence", short: "Headlines are joined to the specific contracts they bear on.",
    body: 'Each headline is matched to markets by shared informative terms and entity agreement, forming explicit <code>evidence</code> edges. Contracts asking the same question on different venues are paired as <code>cross-venue</code> twins. Matching is deterministic and conservative — an unmatched market takes no news adjustment rather than a guessed one.' },
  { k: "03 · PRICE", t: "Compute fair value", short: "The right model per market type, in additive log-odds space.",
    body: 'Threshold markets get the Black-Scholes digital <code>N(d₂)</code> blended with the market prior. Event markets run the Bayesian log-odds engine. Both then take evidence updates, a cross-venue pull, and an ambiguity shrink — all as additive terms, so the factors sum exactly to the output.' },
  { k: "04 · GATE", t: "Decide or abstain", short: "A verdict only fires when edge and confidence both clear their floors.",
    body: 'BUY or SELL requires the edge to clear a minimum threshold <em>and</em> confidence to clear a floor built from liquidity, evidence coverage, and ambiguity. Everything else returns HOLD with a stated reason. Abstention is a feature — most contracts, most of the time, are fairly priced.' },
  { k: "05 · SIZE", t: "Risk before conviction", short: "Fractional Kelly, scaled by confidence and hard-capped.",
    body: 'Position sizing uses the Kelly criterion at a fractional multiple, scaled down by model confidence and capped as a share of bankroll. Expected value is reported net of fees. The number is a ceiling to stay under, not a target to hit.' }
];
let pipeIdx = 0;
function renderPipe() {
  $("pipeTabs").innerHTML = PIPE.slice(0, 3).map((p, i) =>
    `<button class="pipe-btn" role="tab" aria-selected="${i === pipeIdx}" data-i="${i}">
       <span class="pn">${p.k}</span><b>${p.t}</b><p>${p.short}</p></button>`).join("");
  const extra = PIPE.slice(3).map((p, i) =>
    `<button class="pipe-btn" role="tab" aria-selected="${i + 3 === pipeIdx}" data-i="${i + 3}">
       <span class="pn">${p.k}</span><b>${p.t}</b><p>${p.short}</p></button>`).join("");
  $("pipeTabs").insertAdjacentHTML("beforeend", extra);
  const p = PIPE[pipeIdx];
  $("pipeDetail").innerHTML = `<div class="pd-title">${p.k} — ${p.t}</div><p>${p.body}</p>`;
  $("pipeTabs").querySelectorAll("[data-i]").forEach(b =>
    b.onclick = () => { pipeIdx = +b.dataset.i; renderPipe(); });
}
renderPipe();

/* =============================================================
   BASIC / PRO
   ============================================================= */
const MODES = {
  basic: {
    title: "Basic — the decision, not the dashboard",
    who: "FOR EVERYDAY TRADERS",
    rows: [
      ["Verdict and confidence", "BUY · 73%", true],
      ["Why, in plain English", "3 drivers", true],
      ["Suggested maximum risk", "3.5% of bankroll", true],
      ["Live news mapped to the market", "included", true],
      ["Watchlist and alerts", "included", true],
      ["Full factor attribution", "Pro", false],
      ["Evidence graph explorer", "Pro", false],
      ["Arbitrage scanner and portfolio risk", "Pro", false]
    ]
  },
  pro: {
    title: "Pro — every number behind the decision",
    who: "FOR ANALYSTS AND DESKS",
    rows: [
      ["Verdict and confidence", "BUY · 73%", true],
      ["Factor attribution", "5 terms, additive", true],
      ["Model estimate vs exchange", "63.1¢ vs 58.0¢", true],
      ["Evidence graph with citations", "included", true],
      ["Cross-venue arbitrage with fee math", "included", true],
      ["Portfolio exposure and VaR-style risk", "included", true],
      ["Monte Carlo and Kelly tooling", "included", true],
      ["Session export for audit", "JSON · CSV", true]
    ]
  }
};
function setMode(m) {
  const d = MODES[m];
  $("modeTitle").textContent = d.title;
  $("modeFor").textContent = d.who;
  $("modeRows").innerHTML = d.rows.map(r =>
    `<div class="mode-row ${r[2] ? "" : "off"}"><span>${esc(r[0])}</span>${
      r[2] ? `<b>${esc(r[1])}</b>` : `<span class="locked">${esc(r[1])}</span>`}</div>`).join("");
  $("mBasic").setAttribute("aria-pressed", String(m === "basic"));
  $("mPro").setAttribute("aria-pressed", String(m === "pro"));
}
$("mBasic").onclick = () => setMode("basic");
$("mPro").onclick = () => setMode("pro");
setMode("basic");

/* =============================================================
   MINI EVIDENCE GRAPH — canvas force simulation
   ============================================================= */
(function miniGraph() {
  const cv = $("miniGraph"); if (!cv) return;
  const ctx = cv.getContext("2d");
  const LABELS = ["Fed cuts July", "CPI below 3%", "BTC > $120K", "Recession 2026", "Shutdown risk"];
  const N = [];
  for (let i = 0; i < 5; i++) N.push({ t: "m", r: 13 + i % 3 * 3, label: LABELS[i] });
  for (let i = 0; i < 9; i++) N.push({ t: "n", r: 5.5 });
  for (let i = 0; i < 3; i++) N.push({ t: "e", r: 6 });
  const E = [
    [5, 0], [6, 0], [6, 1], [7, 1], [8, 2], [9, 2], [10, 3], [11, 3], [12, 4], [13, 4], [5, 1], [8, 4], [9, 0],
    [14, 0], [14, 1], [15, 2], [15, 4], [16, 3], [16, 0]
  ].map(([a, b]) => ({ a: N[a], b: N[b], w: .5 + Math.random() * .5 }));
  N.forEach((n, i) => {
    const ang = i * 2.399963, rad = 40 + 15 * Math.sqrt(i);
    n.x = Math.cos(ang) * rad; n.y = Math.sin(ang) * rad; n.vx = 0; n.vy = 0;
  });
  let W = 0, H = 0, DPR = 1, alpha = 1, pulseT = 0, pulseEdge = 0;
  function size() {
    DPR = Math.min(2, devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    W = r.width; H = r.height;
    cv.width = W * DPR; cv.height = H * DPR;
  }
  size(); addEventListener("resize", () => { size(); alpha = Math.max(alpha, .35); });
  function step() {
    for (let i = 0; i < N.length; i++) {
      const p = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const q = N[j];
        let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        if (d2 > 40000) continue;
        const f = 900 * alpha / d2, d = Math.sqrt(d2);
        dx /= d; dy /= d;
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      }
    }
    for (const e of E) {
      let dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
      const d = Math.max(1, Math.hypot(dx, dy)), f = (d - 74) * .014 * alpha;
      dx /= d; dy /= d;
      e.a.vx += dx * f; e.a.vy += dy * f; e.b.vx -= dx * f; e.b.vy -= dy * f;
    }
    for (const n of N) {
      n.vx -= n.x * .0022 * alpha; n.vy -= n.y * .0022 * alpha;
      n.vx *= .86; n.vy *= .86; n.x += n.vx; n.y += n.vy;
    }
    alpha = Math.max(.02, alpha * .994);
  }
  function draw() {
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2, k = Math.min(1, W / 460);
    const X = n => cx + n.x * k, Y = n => cy + n.y * k;
    E.forEach((e, i) => {
      ctx.beginPath(); ctx.moveTo(X(e.a), Y(e.a)); ctx.lineTo(X(e.b), Y(e.b));
      const hot = i === pulseEdge;
      ctx.strokeStyle = hot ? `rgba(255,255,255,${.25 + .5 * Math.sin(pulseT * 3) ** 2})`
        : `rgba(224,90,109,${.10 + e.w * .2})`;
      ctx.lineWidth = hot ? 2 : 1.1;
      ctx.stroke();
    });
    for (const n of N) {
      const r = n.r * k;
      ctx.beginPath();
      if (n.t === "e") {
        ctx.save(); ctx.translate(X(n), Y(n)); ctx.rotate(Math.PI / 4);
        ctx.rect(-r * .75, -r * .75, r * 1.5, r * 1.5); ctx.restore();
        ctx.fillStyle = "#77808c";
      } else {
        ctx.arc(X(n), Y(n), r, 0, 7);
        ctx.fillStyle = n.t === "m" ? "#bd8a10" : "#4d8be0";
      }
      ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#07090b"; ctx.stroke();
      if (n.t === "m" && n.label && k > .62) {
        ctx.font = "11px Inter, sans-serif";
        ctx.fillStyle = "rgba(154,164,174,.9)";
        ctx.textAlign = "center";
        ctx.fillText(n.label, X(n), Y(n) + r + 14);
      }
    }
  }
  let visible = true, frame = 0;
  const onScreen = () => { const r = cv.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
  for (let i = 0; i < 260; i++) step();
  draw();   // paint a settled frame up front, so a throttled rAF can't leave an empty box
  function loop() {
    if ((frame++ & 7) === 0) visible = onScreen();   // geometry, not IO — see decayChart
    if (visible) {
      if (!RM) { step(); pulseT += .016; if (pulseT % 2.4 < .016) pulseEdge = (pulseEdge + 1) % E.length; }
      draw();
    }
    requestAnimationFrame(loop);
  }
  loop();
})();

/* =============================================================
   EDGE DECAY — illustrative chart of how a mispricing closes
   Conceptual (and labelled ILLUSTRATIVE on the page): shows the
   gap between market price and fair value collapsing after news.
   ============================================================= */
(function decayChart() {
  const cv = $("decayChart"); if (!cv) return;
  const ctx = cv.getContext("2d");
  const PRE = .44, FAIR = .61, BREAK = .17, TAU = .17;   // fractions of width
  let W = 0, H = 0, DPR = 1, t = 0, visible = true;
  function size() {
    DPR = Math.min(2, devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    W = r.width; H = r.height;
    cv.width = W * DPR; cv.height = H * DPR;
  }
  size(); addEventListener("resize", size);
  const priceAt = f => f <= BREAK ? PRE : FAIR - (FAIR - PRE) * Math.exp(-(f - BREAK) / TAU);
  function draw() {
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const L = 46, R = W - 18, T = 64, B = H - 30;
    const X = f => L + f * (R - L);
    const Y = p => B - ((p - .35) / .35) * (B - T);      // 35¢..70¢ band
    // grid
    ctx.strokeStyle = "#1e252d"; ctx.lineWidth = 1;
    ctx.font = "10px ui-monospace, monospace"; ctx.fillStyle = "#424b54";
    for (let p = .4; p <= .7001; p += .1) {
      ctx.beginPath(); ctx.moveTo(L, Y(p)); ctx.lineTo(R, Y(p)); ctx.stroke();
      ctx.textAlign = "right"; ctx.fillText((p * 100).toFixed(0) + "¢", L - 8, Y(p) + 3);
    }
    ctx.textAlign = "center";
    ["0", "10m", "20m", "30m"].forEach((lb, i) => ctx.fillText(lb, X(i / 3), B + 18));
    // fair value (post-news) line
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(77,181,255,.85)"; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.moveTo(X(BREAK), Y(FAIR)); ctx.lineTo(X(1), Y(FAIR)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(77,181,255,.9)"; ctx.textAlign = "left";
    ctx.fillText("model fair value", X(BREAK) + 8, Y(FAIR) - 8);
    // shaded edge area up to t
    const end = Math.max(BREAK, t);
    ctx.beginPath(); ctx.moveTo(X(BREAK), Y(FAIR));
    for (let f = BREAK; f <= end; f += .004) ctx.lineTo(X(f), Y(FAIR));
    for (let f = end; f >= BREAK; f -= .004) ctx.lineTo(X(f), Y(priceAt(f)));
    ctx.closePath(); ctx.fillStyle = "rgba(61,220,151,.16)"; ctx.fill();
    // market price path up to t
    ctx.beginPath(); ctx.moveTo(X(0), Y(PRE));
    for (let f = 0; f <= t; f += .004) ctx.lineTo(X(f), Y(priceAt(f)));
    ctx.strokeStyle = "#e9ebee"; ctx.lineWidth = 2; ctx.stroke();
    // headline marker
    if (t >= BREAK) {
      ctx.setLineDash([3, 3]); ctx.strokeStyle = "rgba(245,196,81,.75)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(BREAK), T - 10); ctx.lineTo(X(BREAK), B); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(245,196,81,.95)"; ctx.textAlign = "left"; ctx.font = "10px ui-monospace, monospace";
      ctx.fillText("HEADLINE BREAKS", X(BREAK) + 6, T - 14);
    }
    // live edge readout at the leading point
    if (t > BREAK) {
      const p = priceAt(t), gap = (FAIR - p) * 100;
      ctx.beginPath(); ctx.arc(X(t), Y(p), 4, 0, 7);
      ctx.fillStyle = "#e9ebee"; ctx.fill();
      ctx.strokeStyle = "#07090b"; ctx.lineWidth = 2; ctx.stroke();
      ctx.font = "600 12px ui-monospace, monospace";
      ctx.fillStyle = gap > 3 ? "#3ddc97" : "#626d78";
      ctx.textAlign = X(t) > W - 120 ? "right" : "left";
      ctx.fillText("edge " + gap.toFixed(1) + "¢", X(t) + (X(t) > W - 120 ? -10 : 10), Y(p) + 20);
    }
    ctx.textAlign = "left"; ctx.font = "10px ui-monospace, monospace";
    ctx.fillStyle = "#626d78";
    ctx.fillText("market price", X(0) + 4, Y(PRE) - 10);
  }
  /* visibility from geometry, not IntersectionObserver: a misbehaving observer
     would freeze the animation at t=0 and render as a broken flat line. */
  const onScreen = () => {
    const r = cv.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;
  };
  /* Paint one complete frame synchronously. If rAF never runs (reduced motion,
     a throttled/background tab, an old browser), the reader still sees a finished,
     meaningful chart rather than an empty box. */
  t = 1; draw();
  if (RM) return;
  /* Phase comes from wall-clock time, not accumulated frames, so a throttled
     or backgrounded tab still paints a correct frame whenever it does render. */
  const T0 = performance.now(), SWEEP = 5200, PERIOD = 6800;
  let frame = 0;
  (function loop(now) {
    if ((frame++ & 7) === 0) visible = onScreen();   // recheck ~8x/sec
    if (visible) {
      t = Math.min(1, ((now - T0) % PERIOD) / SWEEP);
      draw();
    }
    requestAnimationFrame(loop);
  })(T0);
})();

/* =============================================================
   LIVE NEWS — real public feed, honest states
   ============================================================= */
const GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query=" +
  encodeURIComponent('("Federal Reserve" OR inflation OR Bitcoin OR "S&P 500" OR election OR recession OR Congress OR CPI)') +
  "&mode=ArtList&format=json&maxrecords=30&sort=datedesc&timespan=24h";
const FEED_INTERVAL = 90000;   // auto-refresh cadence
const FEED_SHOW = 8;
const POS = ["cool", "cools", "cooler", "softer", "ease", "eases", "easing", "cut", "cuts", "beat", "beats", "surge", "gain", "gains", "rally", "record", "rise", "rises", "climb", "strong", "rebound", "optimism", "approve", "tops", "inflow", "upgrade", "clears", "advances"];
const NEG = ["hawkish", "hotter", "sticky", "miss", "misses", "fall", "falls", "drop", "drops", "slump", "delay", "stall", "fear", "fears", "risk", "warn", "weak", "decline", "concern", "shutdown", "dissent", "reject", "downgrade", "plunge"];
function toneOf(t) {
  t = t.toLowerCase(); let p = 0, n = 0;
  for (const w of POS) if (t.includes(w)) p++;
  for (const w of NEG) if (t.includes(w)) n++;
  return Math.max(-1, Math.min(1, (p - n) / 3));
}
function timeAgo(ts) {
  if (!ts) return "recent";
  const s = (Date.now() - ts) / 1000;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}
/* which contract family a real headline would reprice — deterministic
   classification of the ACTUAL fetched headline, not a claim about markets */
function categorize(t) {
  t = (t || "").toLowerCase();
  if (/fed|fomc|rate cut|interest rate|powell/.test(t)) return "Rates";
  if (/cpi|inflation|pce|price index/.test(t)) return "Inflation";
  if (/bitcoin|btc|ethereum|crypto|token/.test(t)) return "Crypto";
  if (/recession|gdp|growth|jobs|unemploy|payroll/.test(t)) return "Macro";
  if (/s&p|nasdaq|dow|stocks|equities|earnings/.test(t)) return "Equities";
  if (/election|senate|congress|house|bill|shutdown|vote/.test(t)) return "Politics";
  return "General";
}
function setFeedStatus(state, text) {
  $("feedStatus").innerHTML = `<span class="sd ${state}"></span>${esc(text)}`;
  const hero = $("heroLive");
  if (hero) {
    const col = state === "live" ? "var(--up)" : state === "unavailable" ? "var(--down)" : "var(--accent)";
    const glow = state === "live" ? "box-shadow:0 0 8px var(--up);" : "";
    hero.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:${col};${glow}"></span>NEWS WIRE · ${esc(text)}`;
  }
}
let feedItems = [], feedTimer = null, nextFetchAt = 0;

function renderFeedRows() {
  if (!feedItems.length) return;
  $("feedRows").innerHTML = feedItems.map(a => {
    const href = a.url ? ` href="${esc(a.url)}" target="_blank" rel="noopener noreferrer" style="color:inherit"` : "";
    const tag = href ? "a" : "div";
    return `<div class="feed-row">
      <div style="min-width:0">
        <${tag}${href} class="ft">${esc(a.title)}</${tag}>
        <div class="fm">${esc(a.domain)} · <span class="ago">${esc(timeAgo(a.ts))}</span></div>
        <span class="cat-chip">would reprice · ${esc(a.cat)}</span>
      </div>
      <div class="fs ${a.tone >= 0 ? "up" : "down"}">${a.tone >= 0 ? "+" : ""}${a.tone.toFixed(2)}</div></div>`;
  }).join("");
}
async function loadFeed() {
  const btn = $("feedRefresh");
  if (btn) btn.classList.add("spin");
  setFeedStatus("loading", "CONNECTING");
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 9000);
  try {
    const res = await fetch(GDELT, { signal: ctl.signal, cache: "no-store" });
    if (res.status === 429) throw Object.assign(new Error("rate limited"), { rate: true });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const arts = (data.articles || []).filter(a => a.title).slice(0, FEED_SHOW);
    if (!arts.length) throw new Error("no articles returned");
    feedItems = arts.map(a => {
      let ts = null;
      const m = String(a.seendate || "").match(/(\d{4})(\d{2})(\d{2})T?(\d{2})(\d{2})(\d{2})/);
      if (m) ts = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`);
      return { title: a.title, domain: a.domain || "source", url: a.url || "",
               ts, tone: toneOf(a.title), cat: categorize(a.title) };
    });
    renderFeedRows();
    setFeedStatus("live", "LIVE · " + feedItems.length + " HEADLINES");
  } catch (err) {
    const rate = err && err.rate;
    const aborted = err && err.name === "AbortError";
    feedItems = [];
    setFeedStatus("unavailable", rate ? "RATE-LIMITED" : "UNAVAILABLE");
    $("feedRows").innerHTML = `<div class="feed-empty">
      <strong style="color:var(--soft)">The live news feed isn't reachable right now.</strong><br>
      ${rate ? "The public feed is rate-limiting requests."
        : aborted ? "The request timed out."
        : "The request was blocked by the network or the source is down."}<br>
      <span style="color:var(--faint)">Nothing is shown in its place — Resolv never substitutes invented headlines.</span><br>
      <button class="btn sm" id="feedRetry" style="margin-top:16px">Try again</button></div>`;
    const rb = $("feedRetry"); if (rb) rb.onclick = loadFeed;
  } finally {
    clearTimeout(timer);
    if (btn) btn.classList.remove("spin");
    nextFetchAt = Date.now() + FEED_INTERVAL;
    clearTimeout(feedTimer);
    feedTimer = setTimeout(loadFeed, FEED_INTERVAL);
  }
}
/* timestamps tick without refetching — a stale "2m ago" is itself a lie */
setInterval(() => { if (feedItems.length) renderFeedRows(); }, 20000);
setInterval(() => {
  const el = $("feedCountdown"); if (!el || !nextFetchAt) return;
  const s = Math.max(0, Math.round((nextFetchAt - Date.now()) / 1000));
  el.textContent = s ? "next refresh " + s + "s" : "refreshing…";
}, 1000);
$("feedRefresh").onclick = loadFeed;

/* Fetch when the section nears the viewport, with a timed failsafe so a
   non-firing observer can never leave the panel stuck on "connecting". */
let feedStarted = false;
const startFeed = () => { if (!feedStarted) { feedStarted = true; loadFeed(); } };
if ("IntersectionObserver" in window) {
  const fo = new IntersectionObserver(es => {
    if (es[0].isIntersecting) { fo.disconnect(); startFeed(); }
  }, { rootMargin: "200px" });
  fo.observe($("live"));
  setTimeout(startFeed, 4000);
} else { startFeed(); }

/* =============================================================
   WAITLIST FORM
   Never reports success unless the submission actually succeeded.
   ============================================================= */
const form = $("waitForm"), emailIn = $("waitEmail"), msg = $("waitMsg"), waitBtn = $("waitBtn");
const validEmail = v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim());
form.addEventListener("submit", async e => {
  e.preventDefault();
  const val = emailIn.value.trim();
  if (!validEmail(val)) {
    emailIn.classList.add("bad");
    msg.innerHTML = '<span class="down">Please enter a valid email address.</span>';
    emailIn.focus();
    return;
  }
  emailIn.classList.remove("bad");
  if (!WAITLIST_ENDPOINT) {
    // No backend configured — hand off to the visitor's mail client.
    const subject = encodeURIComponent("Resolv early access request");
    const body = encodeURIComponent(`Please add me to the Resolv private beta.\n\nEmail: ${val}\n`);
    window.location.href = `mailto:${CONTACT_EMAIL}?subject=${subject}&body=${body}`;
    msg.innerHTML = '<span class="dim">Opening your email app to finish the request…</span>';
    return;
  }
  waitBtn.disabled = true;
  waitBtn.textContent = "Sending…";
  try {
    const res = await fetch(WAITLIST_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ email: val, source: "resolv-landing" })
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    msg.innerHTML = '<span class="up">You\'re on the list. We\'ll be in touch about a beta seat.</span>';
    form.reset();
  } catch (err) {
    msg.innerHTML = `<span class="down">That didn't go through.</span> <a href="mailto:${CONTACT_EMAIL}">Email us directly</a> and we'll add you.`;
  } finally {
    waitBtn.disabled = false;
    waitBtn.textContent = "Request access";
  }
});
emailIn.addEventListener("input", () => {
  if (emailIn.classList.contains("bad") && validEmail(emailIn.value)) {
    emailIn.classList.remove("bad"); msg.textContent = "";
  }
});
