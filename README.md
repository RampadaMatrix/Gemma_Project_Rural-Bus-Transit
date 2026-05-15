<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Gemma Transit Intelligence OS</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=Syne:wght@400;600;700;800&family=Literata:ital,opsz,wght@0,7..72,300;0,7..72,400;1,7..72,300&display=swap" rel="stylesheet"/>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --ink: #0d1117;
    --ink2: #1e2938;
    --paper: #f7f4ed;
    --paper2: #ede9df;
    --paper3: #e3ddd0;
    --accent: #d4530a;
    --accent2: #1a5c8e;
    --accent3: #0f6e56;
    --mono: 'DM Mono', monospace;
    --display: 'Syne', sans-serif;
    --body: 'Literata', Georgia, serif;
    --rule: 1px solid rgba(13,17,23,0.14);
  }

  html { scroll-behavior: smooth; }

  body {
    background: var(--paper);
    color: var(--ink);
    font-family: var(--body);
    font-size: 17px;
    line-height: 1.75;
    -webkit-font-smoothing: antialiased;
  }

  /* ── HERO ─────────────────────────────────────── */
  .hero {
    position: relative;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding: 4rem 5vw 5rem;
    overflow: hidden;
    background: var(--ink);
  }

  .hero-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
    background-size: 48px 48px;
  }

  .hero-route {
    position: absolute;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
  }

  .hero-route svg {
    width: 100%;
    height: 100%;
  }

  .hero-badge {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.4);
    margin-bottom: 2rem;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .hero-badge::before {
    content: '';
    display: inline-block;
    width: 32px;
    height: 1px;
    background: var(--accent);
  }

  .hero h1 {
    font-family: var(--display);
    font-size: clamp(3rem, 8vw, 7.5rem);
    font-weight: 800;
    line-height: 0.92;
    color: #fff;
    letter-spacing: -0.03em;
    max-width: 18ch;
    position: relative;
    z-index: 2;
  }

  .hero h1 em {
    font-style: normal;
    color: var(--accent);
  }

  .hero-sub {
    font-family: var(--body);
    font-style: italic;
    font-size: 1.2rem;
    color: rgba(255,255,255,0.5);
    margin-top: 2rem;
    max-width: 52ch;
    position: relative;
    z-index: 2;
  }

  .hero-meta {
    position: absolute;
    top: 4rem;
    right: 5vw;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
    z-index: 2;
  }

  .hero-tag {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.3);
  }

  .hero-stack {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
    margin-top: 4px;
  }

  .pill {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    padding: 4px 10px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 2px;
    color: rgba(255,255,255,0.45);
    white-space: nowrap;
  }

  .pill.lit {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(212,83,10,0.07);
  }

  /* ── MAIN CONTENT ─────────────────────────────── */
  .content {
    max-width: 900px;
    margin: 0 auto;
    padding: 0 5vw;
  }

  /* ── PROBLEM STRIP ────────────────────────────── */
  .problem-strip {
    background: var(--ink2);
    color: #fff;
    padding: 5rem 5vw;
  }

  .problem-strip .content { color: inherit; }

  .section-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 2.5rem;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .section-label::after {
    content: '';
    display: block;
    height: 1px;
    width: 60px;
    background: var(--accent);
    opacity: 0.6;
  }

  .problem-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3rem 5rem;
    margin-top: 2rem;
  }

  .problem-col h2 {
    font-family: var(--display);
    font-size: 2.4rem;
    font-weight: 700;
    line-height: 1.05;
    color: #fff;
    letter-spacing: -0.02em;
    margin-bottom: 1.5rem;
  }

  .problem-col p {
    color: rgba(255,255,255,0.55);
    font-style: italic;
    font-size: 1rem;
    line-height: 1.8;
    margin-bottom: 1rem;
  }

  .scatter-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 1.5rem;
  }

  .scatter-tag {
    font-family: var(--mono);
    font-size: 11px;
    padding: 6px 12px;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 2px;
    color: rgba(255,255,255,0.5);
  }

  .loss-list {
    margin-top: 1.5rem;
    list-style: none;
  }

  .loss-list li {
    font-family: var(--body);
    font-style: italic;
    color: rgba(255,255,255,0.65);
    padding: 0.75rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 0.95rem;
  }

  .loss-list li::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    flex-shrink: 0;
  }

  /* ── SECTION BASE ─────────────────────────────── */
  .section {
    padding: 6rem 5vw;
    border-bottom: var(--rule);
  }

  .section:nth-child(even) { background: var(--paper2); }

  .section-inner {
    max-width: 900px;
    margin: 0 auto;
  }

  .section-header {
    display: grid;
    grid-template-columns: 1fr 2fr;
    gap: 3rem;
    align-items: start;
    margin-bottom: 4rem;
  }

  .section-num {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    padding-top: 6px;
  }

  .section-title {
    font-family: var(--display);
    font-size: clamp(1.8rem, 3.5vw, 2.8rem);
    font-weight: 700;
    line-height: 1.1;
    letter-spacing: -0.025em;
    color: var(--ink);
  }

  .section-body {
    color: #3a3530;
    font-size: 1.05rem;
    max-width: 58ch;
    line-height: 1.8;
  }

  /* ── PIPELINE ─────────────────────────────────── */
  .pipeline {
    display: flex;
    align-items: center;
    gap: 0;
    margin: 3rem 0;
    overflow: hidden;
    border: var(--rule);
    border-radius: 4px;
  }

  .pipe-stage {
    flex: 1;
    padding: 1.4rem 1.2rem;
    position: relative;
    text-align: center;
    background: var(--paper);
    border-right: var(--rule);
    transition: background 0.15s;
  }

  .pipe-stage:last-child { border-right: none; }

  .pipe-stage:hover { background: var(--paper3); }

  .pipe-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: rgba(13,17,23,0.4);
    margin-bottom: 4px;
    display: block;
  }

  .pipe-name {
    font-family: var(--display);
    font-size: 1rem;
    font-weight: 600;
    color: var(--ink);
  }

  .pipe-stage.active .pipe-name { color: var(--accent); }
  .pipe-stage.active { background: rgba(212,83,10,0.04); }

  /* ── FEATURE CARDS ────────────────────────────── */
  .features-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5px;
    background: rgba(13,17,23,0.1);
    border: 1.5px solid rgba(13,17,23,0.1);
    overflow: hidden;
  }

  .feature-card {
    background: var(--paper);
    padding: 2.5rem 2rem;
    transition: background 0.15s;
  }

  .feature-card:hover { background: var(--paper2); }

  .feature-icon {
    font-family: var(--mono);
    font-size: 28px;
    color: var(--accent);
    margin-bottom: 1.2rem;
    line-height: 1;
  }

  .feature-title {
    font-family: var(--display);
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin-bottom: 0.75rem;
  }

  .feature-desc {
    font-size: 0.9rem;
    color: #5a504a;
    line-height: 1.7;
  }

  /* ── CODE BLOCK ───────────────────────────────── */
  .code-block {
    background: var(--ink);
    border-radius: 4px;
    padding: 1.5rem 2rem;
    margin: 1.5rem 0;
    overflow-x: auto;
  }

  .code-block pre {
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.9;
    color: rgba(255,255,255,0.7);
  }

  .code-block .comment { color: rgba(255,255,255,0.25); font-style: italic; }
  .code-block .kw { color: #e88b5e; }
  .code-block .str { color: #7ec8a0; }
  .code-block .arrow { color: rgba(255,255,255,0.4); }

  /* ── STATUS TABLE ─────────────────────────────── */
  .status-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3rem;
    margin-top: 2rem;
  }

  .status-col h3 {
    font-family: var(--display);
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink);
    margin-bottom: 1.5rem;
    padding-bottom: 0.75rem;
    border-bottom: 2px solid var(--ink);
  }

  .status-list {
    list-style: none;
  }

  .status-list li {
    font-size: 0.9rem;
    padding: 0.6rem 0;
    border-bottom: var(--rule);
    display: flex;
    align-items: center;
    gap: 10px;
    color: #3a3530;
    font-family: var(--mono);
    letter-spacing: 0.01em;
  }

  .status-list li .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .dot-done { background: var(--accent3); }
  .dot-wip { background: var(--accent); opacity: 0.6; }

  /* ── ARCH STRIP ───────────────────────────────── */
  .arch-strip {
    background: var(--ink);
    padding: 5rem 5vw;
    color: #fff;
  }

  .arch-inner {
    max-width: 900px;
    margin: 0 auto;
  }

  .arch-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 2px;
    margin-top: 3rem;
    background: rgba(255,255,255,0.06);
  }

  .arch-block {
    background: var(--ink);
    padding: 2rem 1.5rem;
    border: 1px solid rgba(255,255,255,0.05);
    transition: background 0.15s;
  }

  .arch-block:hover { background: rgba(255,255,255,0.03); }

  .arch-block-icon {
    font-family: var(--mono);
    font-size: 22px;
    color: var(--accent);
    margin-bottom: 1rem;
  }

  .arch-block-name {
    font-family: var(--display);
    font-size: 0.9rem;
    font-weight: 600;
    color: rgba(255,255,255,0.85);
    margin-bottom: 0.5rem;
  }

  .arch-block-desc {
    font-size: 0.8rem;
    color: rgba(255,255,255,0.35);
    line-height: 1.6;
    font-family: var(--mono);
  }

  /* ── FUTURE ───────────────────────────────────── */
  .future-wrap {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 2rem;
  }

  .future-item {
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.05em;
    padding: 8px 16px;
    border: 1px solid rgba(13,17,23,0.15);
    border-radius: 2px;
    color: #4a4540;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.15s;
  }

  .future-item:hover {
    background: var(--ink);
    color: #fff;
    border-color: transparent;
  }

  .future-item::before {
    content: '→';
    color: var(--accent);
    font-size: 10px;
  }

  /* ── PHILOSOPHY CALLOUT ───────────────────────── */
  .callout {
    border-left: 3px solid var(--accent);
    padding: 1.5rem 2rem;
    background: rgba(212,83,10,0.04);
    margin: 2.5rem 0;
    border-radius: 0 4px 4px 0;
  }

  .callout p {
    font-family: var(--body);
    font-style: italic;
    font-size: 1.1rem;
    line-height: 1.7;
    color: var(--ink2);
  }

  /* ── OFFLINE ROW ──────────────────────────────── */
  .offline-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4rem;
    align-items: start;
    margin-top: 2.5rem;
  }

  .offline-features {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .offline-feat {
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.06em;
    color: #3a3530;
    padding: 0.9rem 1.2rem;
    background: var(--paper2);
    border-left: 3px solid transparent;
    transition: all 0.1s;
  }

  .offline-feat:hover {
    border-left-color: var(--accent3);
    background: var(--paper3);
  }

  .offline-note {
    font-style: italic;
    color: #5a504a;
    font-size: 0.95rem;
    line-height: 1.8;
  }

  /* ── FOOTER ───────────────────────────────────── */
  .footer {
    background: var(--ink);
    padding: 5rem 5vw;
    color: rgba(255,255,255,0.3);
  }

  .footer-inner {
    max-width: 900px;
    margin: 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }

  .footer-tagline {
    font-family: var(--display);
    font-size: clamp(1.4rem, 3vw, 2.2rem);
    font-weight: 700;
    color: rgba(255,255,255,0.85);
    max-width: 22ch;
    line-height: 1.2;
    letter-spacing: -0.02em;
  }

  .footer-tagline em {
    color: var(--accent);
    font-style: normal;
  }

  .footer-meta {
    text-align: right;
  }

  .footer-meta p {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 6px;
  }

  /* ── DIVIDER DECOR ────────────────────────────── */
  .divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(212,83,10,0.4), transparent);
    margin: 0;
  }

  @media (max-width: 720px) {
    .problem-grid, .features-grid, .status-grid, .arch-grid, .offline-row, .section-header {
      grid-template-columns: 1fr;
    }
    .arch-grid { grid-template-columns: 1fr 1fr; }
    .hero-meta { display: none; }
    .footer-inner { flex-direction: column; gap: 2rem; text-align: left; }
    .footer-meta { text-align: left; }
  }
</style>
</head>
<body>

<!-- HERO -->
<section class="hero">
  <div class="hero-grid"></div>
  <div class="hero-route">
    <svg viewBox="0 0 1400 900" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <g opacity="0.18" fill="none" stroke="rgba(212,83,10,0.9)" stroke-width="1.2">
        <path d="M-50 620 L200 480 L420 510 L600 380 L820 360 L1050 280 L1300 200 L1500 240" stroke-dasharray="5,8"/>
        <path d="M-50 750 L150 680 L350 640 L520 590 L700 520 L900 470 L1100 420 L1350 380 L1500 390" stroke-dasharray="5,8"/>
      </g>
      <g fill="rgba(212,83,10,0.7)">
        <circle cx="200" cy="480" r="3.5"/>
        <circle cx="420" cy="510" r="3.5"/>
        <circle cx="600" cy="380" r="3.5"/>
        <circle cx="820" cy="360" r="3.5"/>
        <circle cx="1050" cy="280" r="3.5"/>
        <circle cx="1300" cy="200" r="3.5"/>
      </g>
      <g fill="rgba(255,255,255,0.08)">
        <circle cx="200" cy="480" r="14"/>
        <circle cx="600" cy="380" r="14"/>
        <circle cx="1050" cy="280" r="14"/>
      </g>
    </svg>
  </div>

  <div class="hero-meta">
    <span class="hero-tag">Built with</span>
    <div class="hero-stack">
      <span class="pill lit">Gemma 4 31B</span>
      <span class="pill">LangGraph</span>
      <span class="pill">Python</span>
      <span class="pill">RAPTOR</span>
    </div>
  </div>

  <div style="position:relative; z-index:2;">
    <div class="hero-badge">AI-Powered Rural Transit Intelligence</div>
    <h1>Gemma <em>Transit</em> Intelligence OS</h1>
    <p class="hero-sub">Structuring, validating, and operationalizing rural transport systems that were never digitally organized.</p>
  </div>
</section>

<!-- PROBLEM -->
<section class="problem-strip">
  <div class="problem-grid" style="max-width:900px; margin:0 auto;">
    <div class="problem-col">
      <div class="section-label">The Problem</div>
      <h2>Transit data trapped in analog fog</h2>
      <p>Large parts of rural India depend on fragmented, undocumented transport. Information lives in handwritten timetables, WhatsApp threads, and local memory.</p>
      <div class="scatter-list">
        <span class="scatter-tag">Handwritten timetables</span>
        <span class="scatter-tag">Facebook posts</span>
        <span class="scatter-tag">WhatsApp messages</span>
        <span class="scatter-tag">Roadside schedules</span>
        <span class="scatter-tag">Verbal timings</span>
        <span class="scatter-tag">Local memory</span>
      </div>
    </div>
    <div class="problem-col">
      <div class="section-label">The Cost</div>
      <h2>Missing a bus means missing life</h2>
      <p>Most regions have no GTFS feeds, no searchable transit infrastructure. Missing a bus means losing access to:</p>
      <ul class="loss-list">
        <li>Work and income</li>
        <li>Education and school</li>
        <li>Healthcare and hospitals</li>
        <li>Critical daily connectivity</li>
      </ul>
    </div>
  </div>
</section>

<div class="divider"></div>

<!-- ARCHITECTURE -->
<section class="arch-strip">
  <div class="arch-inner">
    <div class="section-label">Core Architecture</div>
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 2rem; align-items:start;">
      <div>
        <h2 style="font-family:var(--display); font-size:clamp(1.8rem,3vw,2.5rem); font-weight:700; color:#fff; letter-spacing:-0.025em; line-height:1.1;">Multi-stage orchestration<br/>from raw signal to route graph</h2>
      </div>
      <div style="color:rgba(255,255,255,0.45); font-size:0.95rem; line-height:1.8; padding-top:0.3rem; font-style:italic;">
        The system chains AI extraction, route intelligence, HITL validation, and RAPTOR-ready transit generation into a single coherent pipeline.
      </div>
    </div>
    <div class="arch-grid">
      <div class="arch-block">
        <div class="arch-block-icon">⬡</div>
        <div class="arch-block-name">Gemma 4 31B IT</div>
        <div class="arch-block-desc">AI extraction &amp; schema generation from raw timetable images</div>
      </div>
      <div class="arch-block">
        <div class="arch-block-icon">◈</div>
        <div class="arch-block-name">LangGraph</div>
        <div class="arch-block-desc">Multi-step orchestration workers &amp; pipeline state management</div>
      </div>
      <div class="arch-block">
        <div class="arch-block-icon">◎</div>
        <div class="arch-block-name">RAPTOR Engine</div>
        <div class="arch-block-desc">Journey solving &amp; transfer chain computation for rural routes</div>
      </div>
      <div class="arch-block">
        <div class="arch-block-icon">◇</div>
        <div class="arch-block-name">HITL System</div>
        <div class="arch-block-desc">Human-in-the-loop correction &amp; duplicate prevention logic</div>
      </div>
    </div>
  </div>
</section>

<!-- PIPELINE -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">01 — Secure Pipeline</div>
      <div>
        <h2 class="section-title">Transit data flows through staged validation</h2>
        <p class="section-body" style="margin-top:1rem;">Every record moves through four gates before becoming discoverable. Duplicate prevention and orchestration safeguards maintain consistency at scale.</p>
      </div>
    </div>
    <div class="pipeline">
      <div class="pipe-stage">
        <span class="pipe-label">Stage 1</span>
        <span class="pipe-name">Raw</span>
      </div>
      <div class="pipe-stage active">
        <span class="pipe-label">Stage 2</span>
        <span class="pipe-name">Validated</span>
      </div>
      <div class="pipe-stage">
        <span class="pipe-label">Stage 3</span>
        <span class="pipe-name">Secured</span>
      </div>
      <div class="pipe-stage">
        <span class="pipe-label">Stage 4</span>
        <span class="pipe-name">Discoverable</span>
      </div>
    </div>
    <div class="code-block" style="margin-top:2rem;">
      <pre><span class="comment"># Example — secure transit query</span>
<span class="kw">@secure</span> give me details of <span class="str">WB33C6656</span>

<span class="comment">→ retrieves secured route records</span>
<span class="comment">→ summarizes bus metadata</span>
<span class="comment">→ exposes operational route details</span></pre>
    </div>
  </div>
</section>

<!-- FEATURES -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">02 — Features</div>
      <div>
        <h2 class="section-title">Four pillars of transit intelligence</h2>
      </div>
    </div>
    <div class="features-grid">
      <div class="feature-card">
        <div class="feature-icon">⊞</div>
        <div class="feature-title">AI Timetable Extraction</div>
        <p class="feature-desc">Upload messy timetable screenshots. The system identifies routes, extracts timings, detects stop sequences, and structures raw transport information automatically.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">⊹</div>
        <div class="feature-title">Rural Bus Discovery</div>
        <p class="feature-desc">Natural-language journey planning: <em style="color:var(--accent);">"I want to go from Chipida to Ranibandh."</em> Identifies buses, computes transfer chains, estimates windows.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">⊗</div>
        <div class="feature-title">Route Polyline Intelligence</div>
        <p class="feature-desc">Automatically generates route skeletons, builds transit polylines, and prepares discoverable routing structures from validated stop sequences.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">⊕</div>
        <div class="feature-title">Human-in-the-Loop Validation</div>
        <p class="feature-desc">AI extraction + algorithmic inference + lightweight human correction. More reliable than fully autonomous hallucination-prone transit generation.</p>
      </div>
    </div>
  </div>
</section>

<!-- PROJECT STRUCTURE -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">03 — Structure</div>
      <div>
        <h2 class="section-title">Project layout</h2>
      </div>
    </div>
    <div class="code-block">
      <pre><span class="kw">HITL_Pipeline_new/</span>
  <span class="comment">Human-in-the-loop validation system</span>

<span class="kw">Polyline_Drawing_Pipeline/</span>
  <span class="comment">Route plotting and polyline generation</span>

<span class="kw">ZGemma_files/</span>
  <span class="comment">LangGraph orchestration and AI workers</span>

<span class="kw">scripts/</span>
  <span class="comment">Utility scripts</span>

<span class="kw">purulia_pipeline_orchestrator.py</span>
  <span class="comment">Main orchestration controller</span></pre>
    </div>
  </div>
</section>

<!-- STATUS -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">04 — Status</div>
      <div>
        <h2 class="section-title">What's built, what's becoming</h2>
      </div>
    </div>
    <div class="status-grid">
      <div>
        <div class="status-col">
          <h3 style="font-family:var(--display); font-size:1rem; font-weight:600; letter-spacing:0.05em; text-transform:uppercase; color:var(--ink); margin-bottom:1.5rem; padding-bottom:0.75rem; border-bottom: 2px solid var(--accent3);">Implemented</h3>
        </div>
        <ul class="status-list">
          <li><span class="dot dot-done"></span>AI timetable extraction</li>
          <li><span class="dot dot-done"></span>Secure transit staging</li>
          <li><span class="dot dot-done"></span>Route discovery</li>
          <li><span class="dot dot-done"></span>HITL correction workflows</li>
          <li><span class="dot dot-done"></span>Polyline generation</li>
          <li><span class="dot dot-done"></span>Route visualization</li>
          <li><span class="dot dot-done"></span>RAPTOR-ready outputs</li>
          <li><span class="dot dot-done"></span>Operational command center UI</li>
        </ul>
      </div>
      <div>
        <div class="status-col">
          <h3 style="font-family:var(--display); font-size:1rem; font-weight:600; letter-spacing:0.05em; text-transform:uppercase; color:var(--ink); margin-bottom:1.5rem; padding-bottom:0.75rem; border-bottom: 2px solid var(--accent);">In Progress</h3>
        </div>
        <ul class="status-list">
          <li><span class="dot dot-wip"></span>Large-scale route scaling</li>
          <li><span class="dot dot-wip"></span>Routing optimization</li>
          <li><span class="dot dot-wip"></span>Expanded timetable ingestion</li>
          <li><span class="dot dot-wip"></span>Advanced transport intelligence</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<!-- OFFLINE -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">05 — Offline-First</div>
      <div>
        <h2 class="section-title">Useful even when the signal drops</h2>
      </div>
    </div>
    <div class="offline-row">
      <div>
        <p class="section-body" style="margin-bottom:1.5rem;">Once the transport network is structured and secured, the system operates largely offline. The AI model is only required during initial extraction.</p>
        <div class="callout">
          <p>How AI can help build transport intelligence systems that remain useful even in low-connectivity environments.</p>
        </div>
      </div>
      <div class="offline-features">
        <div class="offline-feat">Bus discovery</div>
        <div class="offline-feat">Route search</div>
        <div class="offline-feat">Transfer analysis</div>
        <div class="offline-feat">RAPTOR-based journey solving</div>
        <div class="offline-feat">Timetable lookup</div>
        <div class="offline-feat">Route visualization</div>
        <div class="offline-feat">Secured transit querying</div>
      </div>
    </div>
  </div>
</section>

<!-- FUTURE -->
<section class="section">
  <div class="section-inner">
    <div class="section-header">
      <div class="section-num">06 — Future</div>
      <div>
        <h2 class="section-title">Possibilities ahead</h2>
      </div>
    </div>
    <div class="future-wrap">
      <div class="future-item">Multilingual transit querying</div>
      <div class="future-item">Statewide rural transport graphs</div>
      <div class="future-item">AI-assisted GTFS generation</div>
      <div class="future-item">Offline rural route intelligence</div>
      <div class="future-item">Accessibility-focused transit</div>
      <div class="future-item">Live telemetry integration</div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer class="footer">
  <div class="footer-inner">
    <p class="footer-tagline">Transit intelligence should not be limited to <em>major cities.</em></p>
    <div class="footer-meta">
      <p>Independent Experimental Project</p>
      <p style="color:rgba(255,255,255,0.15);">Gemma Transit Intelligence OS</p>
    </div>
  </div>
</footer>

</body>
</html>
