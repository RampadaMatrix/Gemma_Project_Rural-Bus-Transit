# Gemma Transit Intelligence OS

## AI-Assisted Infrastructure for Fragmented Rural Bus Networks

Millions of people across rural and semi-urban regions depend on bus systems that are operationally real but digitally invisible. The buses exist. The mobility exists. But the discoverable transit infrastructure often does not.

In many places, transportation information still lives in handwritten timetable sheets, roadside notices, local Facebook posts, WhatsApp groups, operator memory, informal stop names, and partially documented route lists. These systems move people every day, but they are difficult to search, validate, visualize, or route through digitally.

Gemma Transit Intelligence OS explores how Gemma 4 can help convert fragmented community transport knowledge into searchable, route-ready, offline-capable mobility infrastructure. The current implementation focuses on Purulia district and nearby inter-state transport corridors connecting West Bengal, Jharkhand, and Odisha.

## The Problem

Most modern transit technology assumes structured GTFS feeds, centralized operators, stable APIs, consistent stop names, and continuous connectivity. Rural transit ecosystems often work differently.

Schedules may change informally. Route names vary by locality. Stop sequences can be incomplete. Timings are approximate. A bus may be known by its operator, registration number, route nickname, or the memory of regular passengers. Information is socially distributed rather than centrally maintained.

This creates a digital equity gap. Communities may have active transport networks but no reliable way to discover whether a journey is possible, where transfers happen, or which route connects a village to a market, school, hospital, railway station, or district center.

## Why Purulia

Purulia is a strong real-world test environment because it combines large rural geography, thousands of villages, long road corridors, and important inter-district and inter-state movement. Public transport is not a convenience layer here. For many people, buses support education, healthcare access, market participation, rail connectivity, work, and family life.

At the same time, structured transit data remains limited. The current secured dataset in this project contains 119 validated bus records, while the broader regional system likely contains many more active services. That gap is the core motivation: rural transport is already operating, but its digital infrastructure is incomplete.

## What We Built

Gemma Transit Intelligence OS is a working prototype for rural transit digitization, validation, and discovery. It turns messy transport inputs into staged records, computes route geometry, presents routes for human audit, secures approved data, builds a routing graph, and exposes journey discovery through a local interface.

The pipeline is:

```text
Community transit data
        -> Gemma-assisted extraction and reasoning
        -> Structured transit schema
        -> Stage-1 queue
        -> Polyline and route computation
        -> Human-in-the-Loop validation
        -> Secured transit registry
        -> RAPTOR-compatible routing bundle
        -> Offline journey discovery
```

This is not a generic chatbot over files. The system is a coordinated transit pipeline with state management, duplicate safeguards, route geometry generation, secured snapshots, validation stages, and routing data generation.

## How Gemma 4 Is Used

Gemma 4 is used where language understanding and ambiguity handling matter most: the ingestion and interpretation layer.

The Gemma layer helps with timetable extraction, route interpretation, stop-sequence understanding, duplicate bus reasoning, registration normalization, ambiguous transport records, and structured schema generation from noisy inputs. Rural transport information is often written for humans, not machines, so the AI layer is valuable because it can interpret semi-structured and socially formatted information.

The project intentionally separates AI-assisted interpretation from deterministic transit operations. Gemma helps understand messy source data. Deterministic systems handle graph construction, validation gates, duplicate prevention, atomic writes, route solving, and offline discovery.

This division is important. A rural transit system should not blindly trust generated data. Gemma accelerates the conversion of messy information into candidate infrastructure, but the system requires validation before records become authoritative.

## Architecture

The project runs through two coordinated services.

The FastAPI master orchestrator, implemented in `purulia_pipeline_orchestrator.py`, manages the background pipeline. It supervises Stage-1 data, coordinates the Gemma and LangGraph agent layer, tracks bus lifecycle state, dispatches polyline solving jobs, monitors Human-in-the-Loop completion, and triggers discovery rebuilds after secured approvals.

The Flask HITL server, implemented in `HITL_Pipeline_new/hitl_server.py`, serves the route verification interface. It powers route review, stop correction, timetable updates, recomputation, secure locking, proximity analysis, and RAPTOR journey solving.

The Gemma and LangGraph layer, implemented mainly in `ZGemma_files/LangGraph/gemma_graph.py`, acts as a project-aware transit agent. It can extract route records, reason over transport data, check duplicates, stage valid bus entries, query secured data, and call routing tools.

The identity resolver protects the system from duplicate and ambiguous entries. It normalizes registration numbers, handles OCR-like character confusion, compares route signatures, checks active audit state, and distinguishes already-staged buses from true duplicates.

The polyline generation layer converts staged bus records into map-ready geometry. It resolves stop coordinates, validates movement continuity, reuses route signatures, supports reverse-trip geometry reuse, and writes HITL-ready output.

After validation, secured records are converted into a RAPTOR-compatible routing bundle. The current bundle contains 2,369 stops, 190 routes, 291 trips, 27,730 stop times, and 4,773 transfers.

## Human-in-the-Loop Trust Boundary

One of the most important engineering choices was preserving Human-in-the-Loop validation.

During development, it became clear that fully autonomous transit generation is risky for real rural systems. Stop names vary socially. Routes evolve. Schedules can be uncertain. Operators may reuse identifiers. Geographic ambiguity is common.

Instead of pretending that AI can solve all of this automatically, the system uses assisted intelligence. Gemma and deterministic pipelines prepare candidate transit infrastructure, but a human reviewer decides what becomes trusted.

The HITL interface allows an operator to inspect route geometry, correct stops, update timetable information, recompute geometry after edits, and securely lock the approved record. This creates an auditable path from informal knowledge to trusted transit data.

## Offline-First Discovery

Connectivity cannot be assumed in many rural environments. For that reason, the project is designed so discovery can become largely local after graph generation.

Gemma is mainly needed during extraction, parsing, validation, and graph preparation. Once records are secured and the RAPTOR bundle is built, the journey discovery layer can search the local transit graph, inspect routes and stops, evaluate transfer possibilities, and return route options without depending on a live model call for every routing decision.

Example user query:

```text
How can I go from Chipida to Ranibandh?
```

The system can resolve the origin and destination, search the secured graph, compute direct or transfer-aware options, estimate timing windows, and return route segments for visualization.

## Technical Challenges

The hardest challenge was not building one model prompt. It was designing a reliable workflow around imperfect transit knowledge.

The project had to handle duplicate bus entries across secured records, active audit state, and staged data. It needed registration normalization because rural records and OCR outputs may confuse similar characters. It needed route-signature comparison because the same movement may appear under slightly different names. It needed atomic writes and backups because critical JSON datasets should not become corrupted halfway through an update.

Route geometry was another challenge. Rural stop sequences are not always clean, and automatic routing can produce misleading paths if intermediate stops are wrong. The system therefore separates initial route computation from human correction and HITL recomputation.

Finally, journey planning required transforming secured bus records into a routing structure. The RAPTOR data build process extracts stops, routes, trips, stop times, and transfers from approved data so that discovery can be performed through deterministic routing rather than freeform generation.

## Impact

This project fits the Digital Equity and Inclusivity impact area because it addresses communities whose mobility systems are active but underrepresented in digital infrastructure.

The goal is not to replace existing transport operators or local knowledge. The goal is to help structure that knowledge into public, searchable, auditable infrastructure. A successful version of this system could help villages, local governments, transport groups, civic volunteers, and regional planners build usable transit intelligence without requiring a perfect centralized feed from day one.

The Purulia prototype is only the first region. The broader architecture can scale gradually: ingest messy records, validate them, secure them, build routing graphs, and expose discovery. That pattern is relevant anywhere transport systems are real but digitally fragmented.

## Conclusion

Transportation intelligence should not exist only in highly digitized urban systems.

Gemma Transit Intelligence OS demonstrates how AI can help bridge the gap between informal rural transport knowledge and operational mobility infrastructure. Gemma 4 provides the interpretation layer for messy, human-formatted data. Deterministic systems provide validation, graph generation, routing, and offline discovery. Human review provides the trust boundary.

The result is an auditable workflow for turning fragmented bus information into searchable, route-ready transit infrastructure.

