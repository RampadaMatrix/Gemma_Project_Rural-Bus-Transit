# Rural Bus Transit Intelligence - System Architecture

This document summarizes the runtime architecture of the project and the responsibilities of its main subsystems.

## System Overview

The project is a hybrid Human-in-the-Loop transit intelligence system designed for rural bus data that begins in unstructured form. It combines:

- a FastAPI orchestrator,
- a Flask HITL and map server,
- a Gemma 4 LangGraph agent layer,
- a route geometry pipeline,
- and a RAPTOR-based journey discovery engine.

## Runtime Services

### Master Orchestrator

File: `purulia_pipeline_orchestrator.py`

Framework: `FastAPI` via `uvicorn`

Responsibilities:

- loads environment configuration,
- exposes chat, stream, history, and file endpoints,
- supervises the Stage-1 queue,
- dispatches polyline solving jobs,
- tracks bus lifecycle state,
- monitors the HITL server,
- and triggers discovery rebuilds after secured approvals.

### HITL Server

File: `HITL_Pipeline_new/hitl_server.py`

Framework: `Flask`

Responsibilities:

- serves `route_verification_map.html`,
- powers route review and correction workflows,
- manages secure snapshots and rolling backups,
- performs recompute and surgical commit flows,
- exposes proximity and analysis endpoints,
- and runs RAPTOR metadata and route solving APIs.

## Pipeline States

The system moves buses through a staged lifecycle:

1. `Stage_1_data.json`: new extracted or submitted buses wait in the queue.
2. `STAGE_1_PENDING`: the orchestrator picks up a bus for route solving.
3. `WAITING_FOR_HITL`: route geometry is ready for operator review.
4. `PHITL` / `TTHITL`: human-reviewed geometry or timetable state exists.
5. `SECURE`: the bus has been approved and written into the trusted secured dataset.

## Key Data Artifacts

- `Polyline_Drawing_Pipeline/Stage_1_data.json`
- `Polyline_Drawing_Pipeline/BusData_Phase_1.json`
- `Polyline_Drawing_Pipeline/BusData_Phase_1_polyline_stoppages.json`
- `HITL_Pipeline_new/BD_Phase1_HITL_input.json`
- `HITL_Pipeline_new/BD_Phase1_HITL_polyline_output.json`
- `HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json`
- `HITL_Pipeline_new/BD_Phase1_HITL_Secured.json`
- `HITL_Pipeline_new/Raptor_data/raptor_bundle.json`
- `pipeline_state.json`

## Gemma + LangGraph Layer

Main file: `ZGemma_files/LangGraph/gemma_graph.py`

The Gemma layer is integrated as a tool-using transit worker rather than a freeform assistant. It can:

- extract timetable structure from images,
- reason over transport records,
- query large JSON stores surgically,
- resolve duplicate/active audit cases,
- save validated Stage-1 payloads,
- and call RAPTOR-backed journey tools.

## Geometry and Routing

### Polyline Generation

Main file: `Polyline_Drawing_Pipeline/Plotting_Polyline_Algo.py`

Responsibilities:

- validates route continuity,
- computes route geometry from stop sequences,
- reuses signatures for repeated movement patterns,
- and writes HITL-ready polyline output.

### HITL Geometry Recompute

Main file: `HITL_Pipeline_new/Plotting_Polyline_HITL_Algo.py`

Responsibilities:

- uses HITL-corrected coordinates as source of truth,
- recomputes map geometry safely after edits,
- and preserves stop ordering and route integrity.

### RAPTOR Discovery

Files:

- `HITL_Pipeline_new/Raptor_data/build_raptor_data.py`
- `HITL_Pipeline_new/Raptor_data/raptor_solver.py`
- `ZGemma_files/LangGraph/raptor_tools.py`

Responsibilities:

- converts secured routes into a routing bundle,
- extracts curated and virtual stops,
- materializes trips and stop times,
- builds transfer links,
- validates the generated bundle with QA gates,
- and serves direct and transfer-based journey discovery.

## Reliability Patterns

The codebase includes several practical safeguards:

- duplicate prevention before Stage-1 enqueue,
- active audit detection,
- atomic JSON writes,
- rolling backups for critical HITL files,
- secured snapshot normalization,
- background cache warmup,
- and QA checks for RAPTOR bundle generation.
