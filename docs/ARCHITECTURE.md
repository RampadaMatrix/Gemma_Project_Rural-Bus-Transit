# Rural Bus Transit Intelligence (Purulia District & Nearby Region Focus) - System Architecture

This document provides a high-level overview of the backend, frontend, server architecture, and the primary packages utilized in the **Gemma Project: Rural Bus Transit** system.

## 1. System Overview

The project is a hybrid Human-in-the-Loop (HITL) transit mapping system designed for rural environments. It combines an asynchronous processing pipeline, an AI orchestration agent (powered by Gemma 4), a dedicated route-verification UI, and a transit-routing engine (RAPTOR).

## 2. Servers

The system utilizes a dual-server architecture to decouple heavy pipeline orchestration from the frontend UI serving:

### A. Master Orchestrator (`purulia_pipeline_orchestrator.py`)
- **Framework**: `FastAPI` (running via `uvicorn`)
- **Role**: The central nervous system of the project. It manages the asynchronous background tasks (like `Stage_1_solver`), synchronizes state via `pipeline_state.json`, and coordinates the Gemma LangGraph agent.
- **Key Features**:
  - **SSE Streaming**: Pushes real-time terminal and pipeline alerts to the frontend.
  - **State Management**: Tracks bus data transition from ingestion to PHITL (Human-in-the-Loop) to final discovery.
  - **Agent Routing**: Routes queries and data through the LangGraph AI agent.

### B. HITL Server (`HITL_Pipeline_new/hitl_server.py`)
- **Framework**: `Flask` with `Flask-CORS`
- **Role**: The dedicated server for the frontend mapping UI and spatial analytics. 
- **Key Features**:
  - Serves `route_verification_map.html`.
  - Exposes the **RAPTOR Engine** (`RaptorRouter`) for complex transit journey planning.
  - Handles polyline computations (`Plotting_Polyline_HITL_Algo.py`) and proximity analysis (`Analyses.proximity_backend`).
  - Implements atomic file writing and automated JSON rolling backups.

---

## 3. Backend & AI Architecture

### The AI Agent (Gemma 4 via LangGraph)
- **Location**: `ZGemma_files/LangGraph/gemma_graph.py`
- **Stack**: `langgraph`, `langchain_google_genai`, `langchain_core`
- **Capabilities**:
  - Uses the **Gemma 4 model** as an autonomous reasoning agent.
  - **Tool Use**: Equipped with custom tools (`smart_grep`, `read_transit_file`, `patch_bus_data`, `update_bus_timetable`) to surgically audit and manipulate the massive JSON datasets without overloading the context window.
  - **Memory**: Uses LangGraph's `MemorySaver` for persistent conversational memory across turns.

### Transit Engines
- **RAPTOR Solver** (`HITL_Pipeline_new/Raptor_data/raptor_solver.py`): A high-performance transit routing algorithm used to calculate optimal journeys based on the finalized timetables and bus networks.
- **Polyline Generator** (`Polyline_Drawing_Pipeline/`): Scripts that ingest sequential bus stops and generate map-ready routing geometries.

---

## 4. Frontend

- **Primary UI**: `route_verification_map.html`
- **Design Paradigm**: A highly custom, premium "Glassmorphism" interface featuring cinematic motion physics, a dark/light mode, and deep 3D parallax effects.
- **Components**:
  - **Gemma Command Center**: The primary chat interface where the user interacts with the AI agent. Includes markdown rendering, syntax highlighting, and live-streaming text.
  - **Raptor Panel**: A dedicated interface for executing and visualizing RAPTOR transit queries.
  - **Fleet Panel**: A comprehensive dashboard showing bus status (Pending, HITL, Secured).
- **Client-Side Packages (via CDN)**:
  - **`marked.js`**: Renders markdown output from the Gemma agent.
  - **`KaTeX`**: Renders mathematical equations.
  - **`PrismJS`**: Provides syntax highlighting for JSON and code blocks within the chat.

---

## 5. Key Packages & Dependencies

### Python Backend
- **`fastapi` & `uvicorn`**: High-performance async web framework for the Orchestrator.
- **`flask` & `flask-cors`**: Web framework for the HITL UI and analytics server.
- **`pydantic`**: Data validation and strict typing for API schemas.
- **`langgraph` & `langchain-core`**: The state-machine orchestration framework for building the agentic workflow.
- **`langchain-google-genai`**: Google's generative AI integration for Gemma/Gemini.

### Development & Built-ins
- **`subprocess`, `threading`, `asyncio`**: Used heavily in the orchestrator to run background solvers non-blocking.
- **`json`, `re`**: Used extensively for parsing the massive transit data structures.
