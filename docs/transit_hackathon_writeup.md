# Rural Bus Transit Infrastructure

## Creating Digital Infrastructure for Rural Public Buses

Millions of people across rural and semi-urban regions depend on bus systems that are operationally real but digitally invisible. The buses exist. The mobility exists. The travel patterns exist. But the discoverable transit infrastructure often does not.

In public community groups such as **PURULIA BUS UPDATE**, people regularly ask basic journey questions: whether a bus runs after noon, whether there is a morning route toward Bokaro, or what the last bus is from Raghunathpur to Bankura. Some community members answer from local knowledge, but many questions remain uncertain.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2Fe9c710094f4f670c29d4145d07b5a9e9%2F13730.png?generation=1779078990637930&alt=media)

This is only the visible layer. Many rural passengers face this uncertainty every day. Bus information still lives in handwritten timetable sheets, roadside notices, Facebook posts, WhatsApp groups, operator memory, informal stop names, and partially documented route lists. These systems move people daily, but they are difficult to search, validate, visualize, or route through digitally.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2F13ace6f45ccd19d55bc5ebbb90ad1b0f%2F13641.jpg?generation=1779077558837756&alt=media)

Gemma Rural Bus Transit Infrastructure explores how Gemma 4 can help convert fragmented community transport knowledge, much of it Bengali-language dominant, into searchable, route-ready, offline-capable mobility infrastructure. The current implementation focuses on Purulia district and nearby inter-state corridors connecting West Bengal, Jharkhand, and Odisha.

## The Problem

Most modern transit technology assumes structured GTFS feeds, centralized operators, stable APIs, consistent stop names, and continuous connectivity. Rural transit ecosystems often work differently.

Schedules change informally. Route names vary by locality. Stop sequences may be incomplete. Timings can be approximate. A bus may be known by its operator, registration number, route nickname, or simply by regular passenger memory. Information is socially distributed rather than centrally maintained.

This creates a digital equity gap. Communities may have active transport networks but no reliable way to discover whether a journey is possible, where transfers happen, or which route connects a village to a market, school, hospital, railway station, or district center.

The system keeps informed local people, bus operators, and transit maintainers in the loop. AI structures the information, but local validation keeps it trustworthy.

## What We Built

Rural Bus Transit Infrastructure is a working prototype for rural transit digitization, validation, and discovery. It turns messy inputs into staged records, computes route geometry, presents routes for human audit, secures approved data, builds a routing graph, and exposes journey discovery.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2F89ffdef7da0194f50db23d5db9355883%2F13734.png?generation=1779083148085869&alt=media)

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

This is not a generic chatbot over transport files. It is a coordinated transit pipeline with state management, duplicate safeguards, route geometry generation, secured snapshots, validation stages, and routing data generation.

## How Gemma 4 Is Used

Gemma 4 is used where language understanding and ambiguity handling matter most: ingestion and interpretation.

The Gemma layer helps with timetable extraction, route interpretation, stop-sequence understanding, duplicate bus reasoning, registration normalization, ambiguous records, and schema generation from noisy inputs. Rural transport information is written for humans, not machines, so the AI layer is valuable because it can interpret semi-structured social information.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2Fecd83dd13a3d9299b824eaa996c3a98e%2F13732.png?generation=1779082428846677&alt=media)

For example, a noisy timetable image can become a structured candidate record:

```json
{
  "bus_name": "GOUTAM",
  "reg_no": "WB55A6647",
  "primary_hub": "Purulia",
  "movements": [
    {
      "trip_id": "1st trip",
      "direction": "UP",
      "origin": "Ranga",
      "destination": "Purulia",
      "stops": [
        {"name": "Ranga", "departure_time": "10:30 AM", "stop_type": "ORIGIN"},
        {"name": "Ajodhya Hill", "departure_time": "11:10 AM", "stop_type": "INTERMEDIARY"},
        {"name": "Sirkabad", "departure_time": "11:50 AM", "stop_type": "INTERMEDIARY"},
        {"name": "Purulia", "arrival_time": "12:50 PM", "stop_type": "DESTINATION"}
      ]
    }
  ]
}
```

The project separates AI-assisted interpretation from deterministic transit operations. Gemma helps understand messy source data. Deterministic systems handle graph construction, validation gates, duplicate prevention, atomic writes, route solving, and offline discovery.

This division matters. A rural transit system should not blindly trust generated data. Gemma accelerates conversion of messy information into candidate infrastructure, but records must pass validation before they become authoritative.

## Architecture

The project runs through two coordinated services.

The FastAPI master orchestrator, implemented in `purulia_pipeline_orchestrator.py`, manages the background pipeline. It supervises Stage-1 data, coordinates the Gemma and LangGraph agent layer, tracks bus lifecycle state, dispatches polyline solving jobs, monitors Human-in-the-Loop completion, and triggers discovery rebuilds after secured approvals.

The Flask HITL server, implemented in `HITL_Pipeline_new/hitl_server.py`, serves the route verification interface. It powers route review, stop correction, timetable updates, recomputation, secure locking, proximity analysis, and RAPTOR journey solving.

The Gemma and LangGraph layer, implemented mainly in `ZGemma_files/LangGraph/gemma_graph.py`, acts as a project-aware transit agent. It can extract route records, reason over transport data, check duplicates, stage valid bus entries, query secured data, and call routing tools.

The identity resolver protects the system from duplicate and ambiguous entries. It normalizes registration numbers, handles OCR-like character confusion, compares route signatures, checks active audit state, and distinguishes already-staged buses from true duplicates.

After validation, secured records are converted into a RAPTOR-compatible routing bundle. The current bundle contains **2,369 stops, 190 routes, 291 trips, 27,730 stop times, and 4,773 transfers**.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2F9e354b0ce7d5ffd662ac9c67839b4345%2F13736.png?generation=1779084866931083&alt=media)

## Human-in-the-Loop Trust Boundary

One of the most important engineering choices was preserving Human-in-the-Loop validation.

Fully autonomous transit generation is risky for real rural systems. Stop names vary socially. Routes evolve. Schedules can be uncertain. Operators may reuse identifiers. Sometimes only informed local people or bus authorities know whether a bus takes a bypass, follows the main road, or changes stopping behavior on specific days.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2F08e67cf3b70b5d407af6f73c949afae4%2F13740.png?generation=1779084984648290&alt=media)

Instead of pretending AI can solve all of this automatically, the system uses assisted intelligence. Gemma and deterministic pipelines prepare candidate transit infrastructure, but a human reviewer decides what becomes trusted.

The HITL interface allows an operator to inspect route geometry, correct stops, update timetable information, recompute geometry after edits, and securely lock the approved record. This creates an auditable path from informal knowledge to trusted transit data.

## Offline-First Discovery

Connectivity cannot be assumed in many rural environments. For that reason, discovery is designed to become largely local after graph generation.

Gemma is mainly needed during extraction, parsing, validation, and graph preparation. Once records are secured and the RAPTOR bundle is built, the journey discovery layer can search the local transit graph, inspect routes and stops, evaluate transfer possibilities, and return route options without depending on a live model call for every routing decision.

Example query:

```text
How can I go from Kalabani to Chipida before 2:00 PM?
```

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2F522610ea66685ca8c523407e4414bcdb%2F13746.png?generation=1779086871496075&alt=media)

The system can resolve the origin and destination, search the secured graph, compute direct or transfer-aware options, estimate timing windows, and return route segments for visualization.

## Why Purulia

Purulia is a strong real-world test environment because it combines large rural geography, thousands of villages, long road corridors, and important inter-district and inter-state movement. Public transport is not a convenience layer here. For many people, buses support education, healthcare access, market participation, rail connectivity, work, and family life.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2Fbdbbc0ed7f515a599e920442dedfd8b7%2F13764.jpg?generation=1779089841400010&alt=media)

At the same time, structured transit data remains limited. The current secured dataset in this project contains **119 validated bus records**, while the broader regional system likely contains many more active services. That gap is the core motivation: rural transport is already operating, but its digital infrastructure is incomplete.

## Impact

This project fits the Digital Equity and Inclusivity impact area because it addresses communities whose mobility systems are active but underrepresented in digital infrastructure.

Although the current prototype is demonstrated through a web interface, the system was originally explored with a mobile-first direction because rural users are more likely to access transit discovery through phones. The same routing logic can support a mobile experience showing journey time, transfer points, walking distance, waiting time, and bus choices.

![](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F25553182%2Fa39eefc5e99c94501db175e39559ee4e%2FScreenshot_20260518-124938.png?generation=1779091895510980&alt=media)

The goal is not to replace operators or local knowledge. The goal is to structure that knowledge into public, searchable, auditable infrastructure. A successful version could help villages, local governments, transport groups, civic volunteers, and planners build usable transit intelligence without requiring a perfect centralized feed from day one.

Purulia is the first region. The broader architecture can scale gradually: ingest messy records, validate them, secure them, build routing graphs, and expose discovery anywhere transport systems are real but digitally fragmented.

## Conclusion

Transportation intelligence should not exist only in highly digitized urban systems.

Rural Bus Transit Infrastructure demonstrates how AI can bridge the gap between informal rural transport knowledge and operational mobility infrastructure. Gemma 4 provides the interpretation layer for messy, human-formatted data. Deterministic systems provide validation, graph generation, routing, and offline discovery. Human review provides the trust boundary.

The result is an auditable workflow for turning fragmented bus information into searchable, route-ready transit infrastructure.

## Acknowledgements

Some publicly shared transport-related images and examples used in this presentation were referenced from local community and Facebook group discussions to illustrate real rural transit problems and ambiguity in informal transport systems.

Public Facebook group reference: **PURULIA BUS UPDATE**. Credit to the community members and local contributors whose discussions expose the operational challenges faced in underserved transit regions.
