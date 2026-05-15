<div align="center">

```
╔══════════════════════════════════════════════════════════════╗
║          GEMMA Rural Transit Intelligence                        ║
║          AI-Powered Rural Bus Intelligence System            ║
╚══════════════════════════════════════════════════════════════╝
```

[![Built with Gemma 4 31B](https://img.shields.io/badge/Gemma_4_31B-IT-FF6D3A?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev/gemma)
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-1A5C8E?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![RAPTOR](https://img.shields.io/badge/RAPTOR-Ready-0F6E56?style=flat-square)](https://github.com)
[![HITL](https://img.shields.io/badge/Human--in--the--Loop-Validated-6B4FBB?style=flat-square)](https://github.com)

</div>

---

<br/>

## ◈ &nbsp;The Problem

> *In large parts of rural India, bus information exists only in handwritten timetables, Facebook posts, WhatsApp messages, and local memory — never searchable, never structured, never digital.*

**Transit data is scattered across:**

| Source | Accessibility |
|--------|--------------|
| Handwritten timetables | 🔴 Offline only |
| Facebook & WhatsApp | 🟡 Socially gated |
| Roadside schedules | 🔴 Location-locked |
| Verbal timings | 🔴 Ephemeral |
| Local memory | 🔴 Non-transferable |

**Most regions have:**
- No GTFS feeds
- No searchable transit infrastructure
- No route intelligence systems
- No digital discoverability

**Missing a bus means missing:**

```
work  ·  education  ·  healthcare  ·  daily connectivity
```

<br/>

---

## ◉ &nbsp;Vision

Gemma Rural Transit Intelligence  explores how AI can **structure, validate, and operationalize** rural transport systems that were never digitally organized.

```
AI Extraction  ──▶  Route Intelligence  ──▶  HITL Validation  ──▶  Discoverable Mobility
```

<br/>

---

## ⬡ &nbsp;Core Architecture

<div align="center">

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Gemma 4 31B IT   ◈   LangGraph   ◈   RAPTOR Engine       │
│                                                             │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐              │
│   │  Extract  │──▶│ Validate │──▶│  Route   │              │
│   │  Schema  │   │  + HITL  │   │  Graph   │              │
│   └──────────┘   └──────────┘   └──────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

</div>

**Stack:**

| Component | Role |
|-----------|------|
| `Gemma 4 31B IT` | AI extraction, schema generation, messy data parsing |
| `LangGraph` | Multi-stage orchestration, worker pipelines, state |
| `Python` | Core runtime, utility scripts, transformations |
| `RAPTOR` | Journey solving, transfer chains, route computation |
| `HITL System` | Human correction layer, duplicate prevention |

<br/>

---

## ◈ &nbsp;Features

### `01` — AI Timetable Extraction

Upload messy timetable screenshots or transport images. The system:

- ✦ Identifies routes and stop sequences
- ✦ Extracts and structures raw timing data
- ✦ Validates pipeline compatibility
- ✦ Detects and flags anomalies

<br/>

### `02` — Rural Bus Discovery

Natural-language journey planning:

```
User ❯  "I want to go from Chipida to Ranibandh"

System ──▶  Identifies possible buses
       ──▶  Computes transfer chains
       ──▶  Estimates timing windows
       ──▶  Visualizes route segments
```

<br/>

### `03` — Secure Transit Pipeline

```
RAW  ──▶  VALIDATED  ──▶  SECURED  ──▶  DISCOVERABLE
 ↑              ↑               ↑
[AI]          [HITL]       [Dedup + Safeguards]
```

Every record moves through staged gates before becoming queryable. Duplicate prevention and orchestration safeguards maintain consistency.

**Example — secure data query:**
```
@secure give me details of WB33C6656

→ retrieves secured route records
→ summarizes bus metadata
→ exposes operational route details
```

<br/>

### `04` — Route Polyline Intelligence

The platform automatically:

- Generates route skeletons from stop sequences
- Builds transit polylines for mapping
- Prepares RAPTOR-ready routing structures
- Organizes discoverable route networks

<br/>

### `05` — Human-in-the-Loop Validation

```
AI Extraction  +  Algorithmic Inference  +  Human Correction
        └─────────────────┬──────────────────┘
                          ▼
              More reliable than fully autonomous,
              hallucination-prone transit generation
```

<br/>

---

## ◇ &nbsp;Project Structure

```
HITL_Pipeline_new/
│   Human-in-the-loop validation system
│
Polyline_Drawing_Pipeline/
│   Route plotting and polyline generation
│
ZGemma_files/
│   LangGraph orchestration and AI workers
│
scripts/
│   Utility scripts
│
purulia_pipeline_orchestrator.py
    Main orchestration controller
```

<br/>

---

## ◎ &nbsp;Interface Philosophy

> *"A transit intelligence operations system — not a generic chatbot."*

Design goals:
- **Operational clarity** — no noise, only signal
- **Infrastructure visibility** — the system's state is always exposed
- **Atmospheric storytelling** — feels like a real transit command center
- **Realistic transport aesthetics** — purpose-built, not generic

<br/>

---

## ⊹ &nbsp;Status

### ✅ &nbsp;Implemented

| Feature | Status |
|---------|--------|
| AI timetable extraction | `●  Live` |
| Secure transit staging | `●  Live` |
| Route discovery | `●  Live` |
| HITL correction workflows | `●  Live` |
| Polyline generation | `●  Live` |
| Route visualization | `●  Live` |
| RAPTOR-ready outputs | `●  Live` |
| Operational command center UI | `●  Live` |

### 🔄 &nbsp;In Progress

| Feature | Status |
|---------|--------|
| Large-scale route scaling | `◐  Active` |
| Routing optimization | `◐  Active` |
| Expanded timetable ingestion | `◐  Active` |
| Advanced transport intelligence | `◐  Active` |

<br/>

---

## ○ &nbsp;Offline-First Transit Intelligence

One of the core goals: **reducing dependency on continuous internet access.**

Once the transport graph is prepared, the system operates largely offline:

```
✦ bus discovery          ✦ route search
✦ transfer analysis      ✦ RAPTOR journey solving
✦ timetable lookup       ✦ route visualization
✦ secured transit querying
```

**AI is only required during:**
```
timetable extraction  ·  messy data parsing
```

> This is especially critical for rural regions where internet connectivity is unstable, mobile data is limited, and digital infrastructure is inconsistent.

<br/>

---

## ◌ &nbsp;Future Possibilities

```
◦  Multilingual transit querying
◦  Statewide rural transport graphs
◦  AI-assisted GTFS generation
◦  Offline rural route intelligence
◦  Accessibility-focused transit systems
◦  Live telemetry integration
```

<br/>

---

## ⊞ &nbsp;Technical Highlights

| Capability | Description |
|-----------|-------------|
| LangGraph orchestration | Multi-stage worker pipeline management |
| AI schema generation | Structured extraction from unstructured sources |
| Rural route intelligence | Graph-based discovery across sparse stop networks |
| Dynamic transit discovery | Natural-language to route computation |
| RAPTOR preparation | Journey-solving ready output format |
| Duplicate prevention | Safeguards across ingestion stages |
| HITL workflows | Human correction integrated into pipeline |

<br/>

---

## ⊗ &nbsp;Important Note

> *This project models real-world rural transport uncertainty.*

Many datasets are **incomplete, manually maintained, socially distributed, and partially undocumented.** Human validation is intentionally integrated — not as a workaround, but as a design principle.

<br/>

---

<div align="center">

```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   Transit intelligence should not be limited to             ║
║   major cities.                                              ║
║                                                              ║
║   Small villages deserve discoverable mobility              ║
║   infrastructure too.                                        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

*Independent experimental project exploring AI-assisted rural mobility intelligence systems.*

</div>
