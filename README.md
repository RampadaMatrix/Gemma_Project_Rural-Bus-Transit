# Rural Transit Intelligence 

AI-assisted rural bus intelligence for regions where transit data is fragmented, informal, and largely undiscoverable.

## Overview

Rural Transit Intelligence  is a hybrid AI + Human-in-the-Loop transit system built to turn messy rural bus information into searchable, structured, and operational transport data.

The project focuses on a real gap in rural mobility systems:

- Timetables often exist only as images, handwritten boards, social posts, or local memory.
- There is usually no GTFS feed, no digital route graph, and no reliable journey planner.
- Missing a bus can mean missing work, school, healthcare, or the only viable connection for the day.

This system uses Gemma-powered extraction, staged validation, route geometry generation, and a RAPTOR-based journey engine to make rural bus networks discoverable.

## Why This Matters

Urban transit systems increasingly benefit from structured APIs, apps, and real-time routing. Rural systems often do not. That creates a digital access gap, not just a data gap.

Rural Transit Intelligence  is designed to help close that gap by:

- digitizing unstructured bus information,
- validating it through human review,
- converting it into route-ready transport data,
- and exposing it through an interface that supports search, audit, and journey planning.

## What The System Does

### 1. AI timetable extraction

The system can ingest noisy or messy bus timetable images and extract:

- bus identity,
- routes and stop sequences,
- trip directions,
- timetable structure,
- and candidate staged records for downstream processing.

### 2. Human-in-the-Loop validation

AI extraction is not treated as the final source of truth.

Every important record can pass through a review workflow where the operator can:

- inspect route geometry,
- correct timing and stop issues,
- validate route logic,
- and secure approved records into the trusted dataset.

### 3. Route polyline generation

The platform generates map-ready route geometry from staged transit records and prepares the route data for audit and discovery workflows.

### 4. Transit discovery and journey planning

Once records are secured, the system supports rural route lookup and journey planning through a RAPTOR-based routing layer.

### 5. Operational AI orchestration

Gemma is integrated into the broader pipeline through LangGraph-based orchestration rather than a standalone chatbot flow.

## Core Architecture

The project uses a dual-server architecture.

### FastAPI Orchestrator

File: `purulia_pipeline_orchestrator.py`

Responsibilities:

- runs the central orchestration loop,
- manages pipeline state,
- coordinates background processing,
- exposes the main chat and event-stream APIs,
- and supervises the HITL server lifecycle.

### Flask HITL Server

File: `HITL_Pipeline_new/hitl_server.py`

Responsibilities:

- serves the route verification UI,
- provides spatial and audit endpoints,
- runs route and cache workflows,
- exposes RAPTOR journey planning endpoints,
- and supports secure transit review operations.

### Gemma + LangGraph Agent Layer

Files:

- `ZGemma_files/LangGraph/gemma_graph.py`
- `ZGemma_files/LangGraph/identity_resolver.py`
- `ZGemma_files/LangGraph/raptor_tools.py`

Responsibilities:
