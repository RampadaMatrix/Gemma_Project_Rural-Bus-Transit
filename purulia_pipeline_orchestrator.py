import json
import os
import re
import time
import subprocess
import sys
import shutil
import threading
import atexit
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends, Query, Request

from pydantic import BaseModel
from typing import List, Optional, Annotated

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import asyncio
import sys
from typing import Any, Dict
import config

config.load_env()
API_TOKEN = config.get_api_token()

def _is_loopback_host(host: str | None) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def verify_token(request: Request, x_api_token: Annotated[str | None, Header()] = None):
    client_host = getattr(request.client, "host", None)
    if _is_loopback_host(client_host):
        return x_api_token
    if not API_TOKEN or x_api_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API Token")
    return x_api_token


def verify_token_with_query(
    request: Request,
    x_api_token: Annotated[str | None, Header()] = None,
    api_token: Annotated[str | None, Query()] = None,
):
    client_host = getattr(request.client, "host", None)
    if _is_loopback_host(client_host):
        return x_api_token or api_token
    token = x_api_token or api_token
    if not API_TOKEN or token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API Token")
    return token



# Fix Windows console encoding for Bengali/Unicode logs
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Import our custom Gemma interface
try:
    from ZGemma_files.gemma_interface import GemmaAgent
    from ZGemma_files.LangGraph.gemma_graph import gemma_graph
    from ZGemma_files.LangGraph.identity_resolver import (
        active_audit_message,
        bus_label,
        extract_buses_from_payload,
        load_registry_buses,
        resolve_active_audit_state,
        resolve_bus_identity,
    )
    from langchain_core.messages import HumanMessage, AIMessage
except ImportError as e:
    print(f"[WARNING] Agent components not found: {e}. Chat will be restricted.")
    GemmaAgent = None
    gemma_graph = None
    AIMessage = None

BUSDATA_PHASE_1 = os.path.join(BASE_DIR, "Polyline_Drawing_Pipeline", "BusData_Phase_1.json")
STAGE_1_SOLVER = os.path.join(BASE_DIR, "Polyline_Drawing_Pipeline", "Plotting_Polyline_Algo.py")

# --- STAGE 0: REMOVED (Legacy Image Digitization) ---

HITL_INPUT = config.HITL_INPUT # 

SECURED_FILE = os.path.join(BASE_DIR, "HITL_Pipeline_new", "BD_Phase1_HITL_Secured.json")
RAPTOR_BUILDER = os.path.join(BASE_DIR, "HITL_Pipeline_new", "Raptor_data", "build_raptor_data.py")

BUS_COUNT_FILE = os.path.join(BASE_DIR, "ZGemma_files", "master_bus_count.json")
STATE_FILE = os.path.join(BASE_DIR, "pipeline_state.json")
STAGE_1_QUEUE = os.path.join(BASE_DIR, "Polyline_Drawing_Pipeline", "Stage_1_data.json")
LAST_BUSDATA_MTIME = 0
RUNNING_SOLVES = set()
state_lock = threading.Lock()

def extract_json_block(text: str) -> str:
    """Strips markdown and conversational filler to return raw JSON."""
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        return match.group(1).strip()
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        return match.group(1).strip()
    return text.strip()


def normalize_extracted_json_text(extracted_json: str) -> str:
    """Return pretty JSON even when the vision model emits escaped newlines."""
    raw = (extracted_json or "").strip()
    candidates = [raw]
    if "\\n" in raw:
        candidates.append(raw.replace("\\n", "\n"))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            continue

    return candidates[-1] if candidates else raw


def is_generic_image_extraction_instruction(message: str) -> bool:
    """Detect plain extraction commands that should not be treated as travel questions."""
    text = " ".join(str(message or "").lower().split())
    if not text:
        return False

    generic_phrases = (
        "analyze image and extract data",
        "analyse image and extract data",
        "extract data",
        "extract schedule",
        "extract timetable",
        "extract bus data",
        "analyze image",
        "analyse image",
        "read this image",
        "process this image",
    )
    return any(phrase in text for phrase in generic_phrases)


def format_extracted_data_handoff(extracted_json: str) -> str:
    """Build the visible Gemma-lane handoff message for image extraction."""
    display_json = normalize_extracted_json_text(extracted_json)

    return (
        "Extracted structured schedule data:\n\n"
        "```json\n"
        f"{display_json}\n"
        "```\n\n"
        "**Pipeline status:** validating duplicate checks and registration..."
    )


def is_recoverable_model_error_text(text: str) -> bool:
    """Detect transient upstream model failures that should not enter the extraction pipeline."""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return (
        "THOUGHT: Upstream model error" in normalized
        or "[ALERT] The model endpoint returned an internal error" in normalized
    )


def parse_hybrid(content: str) -> tuple:
    """Parses HYBRID format: 'USER_QUERY: <q> [OBSERVED_DATA]: <json>' into (query, json)."""
    query_match = re.search(r'USER_QUERY:\s*(.*?)\s*\[OBSERVED_DATA\]:', content, re.DOTALL)
    data_match = re.search(r'\[OBSERVED_DATA\]:\s*(.*)', content, re.DOTALL)
    user_query = query_match.group(1).strip() if query_match else ""
    observed_json = data_match.group(1).strip() if data_match else ""
    if not user_query and not observed_json:
        return content, ""
    return user_query, observed_json


def update_master_bus_count():
    """Scans the master database and updates the cached bus count for the agent."""
    global LAST_BUSDATA_MTIME
    if not os.path.exists(BUSDATA_PHASE_1):
        return
    try:
        current_mtime = os.path.getmtime(BUSDATA_PHASE_1)
        if current_mtime <= LAST_BUSDATA_MTIME:
            return

        with open(BUSDATA_PHASE_1, 'r', encoding='utf-8') as f:
            data = json.load(f)
            count = len(data.get("buses", []))
            with open(BUS_COUNT_FILE, 'w', encoding='utf-8') as cf:
                json.dump({"count": count, "last_updated": datetime.now().isoformat()}, cf)
        
        LAST_BUSDATA_MTIME = current_mtime
    except Exception as e:
        log(f"Error updating bus count: {e}")


POLL_INTERVAL_SEC = 5
_SERVER_LAUNCH_TIME = 0
_HITL_SERVER_PROCESS = None
_HITL_SERVER_LOG_HANDLE = None
_HITL_SERVER_AUTOSTART_WARNED = False
_HITL_SERVER_ONLINE_LOGGED = False

# --- GEMMA AGENT SETUP ---
gemma_agent = None
try:
    if GemmaAgent:
        gemma_agent = GemmaAgent(model_id="gemma-4-31b-it")
except Exception as e:
    print(f"[WARNING] Failed to initialize Gemma Agent: {e}")

app = FastAPI(title="Purulia Transit OS - Master Orchestrator")

# Enable CORS for the UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SESSION & HISTORY ENDPOINTS ---
def _get_session_path(session_id: str):
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)
    session_dir = os.path.join(BASE_DIR, "ZGemma_files", "Sessions")
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, f"{safe_id}.json")

def _load_session_history(session_id: str):
    path = _get_session_path(session_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def _save_session_history(session_id: str, history: list):
    path = _get_session_path(session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

# --- ACTIVE SESSION LOCKING ---
SESSIONS_IN_FLIGHT = set()

@app.get("/history", dependencies=[Depends(verify_token)])
async def get_history(sessionId: str):
    history = _load_session_history(sessionId)
    return {
        "history": history,
        "isBusy": sessionId in SESSIONS_IN_FLIGHT
    }

@app.post("/reset_session", dependencies=[Depends(verify_token)])
async def reset_session(sessionId: str):
    path = _get_session_path(sessionId)
    if os.path.exists(path):
        os.remove(path)
    return {"status": "success", "message": "Session history wiped from backend."}

# LangGraph handles persistence via Checkpointers, 
# so we don't need manual gemma_session_history dict.

class ChatRequest(BaseModel):
    sessionId: str
    message: str
    image: Optional[str] = None # Base64 image data
    turnId: Optional[str] = None

class TraceStep(BaseModel):
    node: str           # "router", "update_context", "chat", "tools", "mcc_worker", "mcc_handoff"
    action: str         # "Routing to Chat", "Calling save_to_file", etc.
    detail: str = ""    # Tool input/output, schema loaded, etc.
    duration_ms: int = 0
    status: str = "ok"  # "ok", "error", "pending"

class ChatResponse(BaseModel):
    reply: str
    thought: Optional[str] = None
    node: str = "ORCHESTRATOR"
    turnId: Optional[str] = None
    trace: List[TraceStep] = []       # Full execution trace
    mcc_used: bool = False            # Was the MCC worker used?
    worker_id: Optional[str] = None   # Ephemeral worker thread ID

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [ORCHESTRATOR] {msg}", flush=True)

# --- SSE INFRASTRUCTURE (PHASE 0) ---
alert_subscribers = set()

# We need the main event loop to put items in the queue from a background thread
main_loop = None

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()

def push_alert(alert_type: str, message: str, payload: dict = None):
    """Pushes a real-time event to the UI via SSE."""
    event = {
        "type": alert_type,
        "message": message,
        "payload": payload or {},
        "timestamp": datetime.now().isoformat()
    }
    # Broadcast safely from sync threads to all connected SSE clients
    try:
        if main_loop and not main_loop.is_closed():
            async def _broadcast():
                stale = []
                for subscriber in list(alert_subscribers):
                    try:
                        subscriber.put_nowait(event)
                    except Exception:
                        stale.append(subscriber)
                for subscriber in stale:
                    alert_subscribers.discard(subscriber)

            asyncio.run_coroutine_threadsafe(_broadcast(), main_loop)
    except Exception as e:
        print(f"[SSE Error] Failed to push alert: {e}")


def push_session_alert(session_id: str, turn_id: Optional[str], alert_type: str, message: str, payload: dict = None):
    scoped_payload = dict(payload or {})
    if session_id:
        scoped_payload["sessionId"] = session_id
    if turn_id:
        scoped_payload["turnId"] = turn_id
    push_alert(alert_type, message, scoped_payload)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _safe_snippet(value: Any, limit: int = 180) -> str:
    text = _ANSI_RE.sub("", str(value or ""))
    text = re.sub(r"data:image\/[^;]+;base64,[A-Za-z0-9+/=]+", "[image payload]", text)
    text = re.sub(r"```[\s\S]*?```", "[structured block]", text)
    text = re.sub(r"\{[\s\S]{220,}\}", "[structured data]", text)
    text = re.sub(r"\[[A-Za-z_ ]+\]:\s*\{[\s\S]*", "[structured payload]", text)
    text = re.sub(r"[A-Za-z]:\\[^\s]+", lambda m: os.path.basename(m.group(0)), text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "..."
    return text


def _activity_payload(kind: str, title: str, detail: str = "", node: str = "ORCHESTRATOR", status: str = "ok") -> Dict[str, Any]:
    return {
        "activity": {
            "kind": kind,
            "title": title,
            "detail": _safe_snippet(detail, 220),
            "node": node,
            "status": status,
            "timestamp": datetime.now().isoformat()
        }
    }


def push_activity(session_id: Optional[str], turn_id: Optional[str], kind: str, title: str, detail: str = "", node: str = "ORCHESTRATOR", status: str = "ok", phase: Optional[str] = None):
    payload = _activity_payload(kind, title, detail, node=node, status=status)
    if phase:
        payload["phase"] = phase
    push_session_alert(session_id, turn_id, "AGENT_THOUGHT", f"{title}: {detail}".strip(": "), payload)

@app.get("/stream", dependencies=[Depends(verify_token_with_query)])
async def sse_stream():
    """SSE Endpoint for pushing live alerts to the UI."""
    async def event_generator():
        subscriber_queue = asyncio.Queue()
        alert_subscribers.add(subscriber_queue)
        try:
            while True:
                try:
                    # Wait for 15 seconds for an event, else send heartbeat
                    event = await asyncio.wait_for(subscriber_queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive and reduce "reflection" lag
                    yield f"data: {json.dumps({'type': 'HEARTBEAT', 'timestamp': datetime.now().isoformat()})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            alert_subscribers.discard(subscriber_queue)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/")
async def gateway_index():
    return RedirectResponse("http://127.0.0.1:5000/")


@app.get("/route_verification_map.html")
async def gateway_route_verification_map():
    return RedirectResponse("http://127.0.0.1:5000/route_verification_map.html")

def load_state():
    with state_lock:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                log("[WARNING] Pipeline state file is corrupted. Initializing fresh state.")
                return {"buses": {}}
        return {"buses": {}}

def save_state(state):
    with state_lock:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4)


def remember_bus_session_context(buses: List[Dict[str, Any]], session_id: str, turn_id: Optional[str]):
    if not session_id or not buses:
        return
    state = load_state()
    bus_context = state.setdefault("bus_context", {})
    touched = False
    for bus in buses:
        reg = str(bus.get("reg_no") or "").strip()
        if not reg:
            continue
        bus_context[reg] = {
            "sessionId": session_id,
            "turnId": turn_id,
            "name": bus.get("bus_name") or bus.get("name") or reg,
            "updated_at": datetime.now().isoformat(),
        }
        touched = True
    if touched:
        save_state(state)


def get_bus_session_context(reg: str) -> Dict[str, Any]:
    state = load_state()
    bus_context = state.get("bus_context") if isinstance(state, dict) else {}
    if not isinstance(bus_context, dict):
        return {}
    context = bus_context.get(reg) or {}
    return context if isinstance(context, dict) else {}


def validate_bus_schema(bus):
    required = ["bus_name", "reg_no", "movements"]
    for field in required:
        if field not in bus:
            return False, f"Missing field: {field}"
    return True, "OK"

def _summarize_terminal_line(line: str) -> Optional[Dict[str, str]]:
    clean = _safe_snippet(line, 180)
    if not clean:
        return None

    lower = clean.lower()
    if any(token in lower for token in ("traceback", "exception", "error", "failed")):
        return {"title": "Terminal reported an issue", "detail": clean, "status": "error"}
    if any(token in lower for token in ("success", "complete", "completed", "done", "saved", "rebuilt")):
        return {"title": "Terminal completed a step", "detail": clean, "status": "ok"}
    if any(token in lower for token in ("processing", "building", "validating", "solving", "loading", "reading", "writing", "updating")):
        return {"title": "Terminal progress", "detail": clean, "status": "pending"}
    return {"title": "Terminal update", "detail": clean, "status": "pending"}


def run_script(path, args=[], session_id: Optional[str] = None, turn_id: Optional[str] = None, node: str = "TERMINAL", phase: Optional[str] = None):
    """Executes a script and streams sanitized progress to the UI in realtime."""
    cmd = ["python", path] + args
    log(f"Running: {' '.join(cmd)}")
    full_output = []
    try:
        # Popen allows us to read line by line while the process is running
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True, 
            bufsize=1, 
            universal_newlines=True
        )
        
        for line in process.stdout:
            clean_line = line.strip()
            if clean_line:
                full_output.append(clean_line)
                terminal_event = _summarize_terminal_line(clean_line)
                if terminal_event and session_id and turn_id:
                    push_activity(
                        session_id,
                        turn_id,
                        kind="terminal",
                        title=terminal_event["title"],
                        detail=terminal_event["detail"],
                        node=node,
                        status=terminal_event["status"],
                        phase=phase
                    )
        
        process.wait()
        success = (process.returncode == 0)
        return success, "\n".join(full_output)
    except Exception as e:
        return False, str(e)


def run_stage_1_ingestor():
    """Stage 1: Watch Stage_1_data.json for UNIQUE entries and trigger the solver."""
    if not os.path.exists(STAGE_1_QUEUE):
        return

    try:
        with open(STAGE_1_QUEUE, 'r', encoding='utf-8') as f:
            queue_data = json.load(f)
        
        buses = queue_data.get("buses", [])
        if not buses:
            return

        state = load_state()
        master_updated = False

        # Secondary guard: if reg_no already exists in secured DB, do NOT process/solve it from Stage_1 queue
        secured_regs = set()
        secured_path = os.path.join(BASE_DIR, "HITL_Pipeline_new", "BD_Phase1_HITL_Secured.json")
        if os.path.exists(secured_path):
            try:
                with open(secured_path, 'r', encoding='utf-8') as sf:
                    secured = json.load(sf)
                for item in secured.get("locked_ids", []) if isinstance(secured, dict) else []:
                    if isinstance(item, str) and "(" in item and ")" in item:
                        reg = item.split("(")[-1].split(")")[0].strip()
                        if reg:
                            secured_regs.add(reg)
                for b in secured.get("buses", []) if isinstance(secured, dict) else []:
                    if isinstance(b, dict) and b.get("reg_no"):
                        secured_regs.add(str(b.get("reg_no")).strip())
            except Exception:
                pass
        
        with open(BUSDATA_PHASE_1, 'r', encoding='utf-8') as f:
            master = json.load(f)
        registry_buses = load_registry_buses([SECURED_FILE, BUSDATA_PHASE_1])
        
        seen_regs = set()
        unique_queue = []
        for bus in buses:
            reg = bus.get("reg_no")
            if not reg:
                continue
            if reg in seen_regs:
                continue
            seen_regs.add(reg)
            unique_queue.append(bus)

        processed_regs = set()
        blocked_regs = set()

        for bus in unique_queue:
            reg = bus.get("reg_no")
            if not reg:
                continue

            identity = resolve_bus_identity(bus, registry_buses)
            if identity.get("action") == "duplicate":
                matched = identity.get("matched_bus") or {}
                blocked_regs.add(reg)
                processed_regs.add(reg)
                log(
                    "[GUARDIAN] Duplicate registry match; skipping Stage_1 solve "
                    f"and popping from queue: {bus_label(bus)} matched {bus_label(matched)} "
                    f"({identity.get('reason')})"
                )
                continue

            # If secured already contains this reg, block it from Stage_1 processing and pop from queue
            if reg in secured_regs:
                blocked_regs.add(reg)
                processed_regs.add(reg)
                log(f"[GUARDIAN] Reg already secured; skipping Stage_1 solve and popping from queue: {bus.get('bus_name')} ({reg})")
                continue

            exists_in_master = any(b.get("reg_no") == reg for b in master.get("buses", []))

            if not exists_in_master:
                log(f"[GUARDIAN] New Unique Bus Detected in Queue: {bus.get('bus_name')} ({reg})")
                master["buses"].append(bus)
                master_updated = True

            processed_regs.add(reg)

            state["buses"][reg] = {
                "name": bus.get("bus_name"),
                "status": "STAGE_1_PENDING",
                "last_update": datetime.now().isoformat()
            }
            context = get_bus_session_context(reg)
            if context.get("sessionId"):
                state["buses"][reg]["sessionId"] = context.get("sessionId")
            if context.get("turnId"):
                state["buses"][reg]["turnId"] = context.get("turnId")
            log(f"           Status forced to STAGE_1_PENDING for solve.")

        if master_updated:
            with open(BUSDATA_PHASE_1, 'w', encoding='utf-8') as f:
                json.dump(master, f, indent=2)
            log("[SUCCESS] Master updated with new unique buses.")

        if processed_regs:
            save_state(state)
            remaining_buses = [b for b in buses if b.get("reg_no") not in processed_regs]
            with open(STAGE_1_QUEUE, 'w', encoding='utf-8') as f:
                json.dump({"buses": remaining_buses}, f, indent=2)
            if blocked_regs:
                log(f"           Queue updated surgically. {len(processed_regs)} popped ({len(blocked_regs)} blocked-secured), {len(remaining_buses)} remaining.")
            else:
                log(f"           Queue updated surgically. {len(processed_regs)} popped, {len(remaining_buses)} remaining.")

    except Exception as e:
        log(f"[INGESTOR ERROR] {e}")

def run_stage_1_solver():
    state = load_state()
    for reg, info in state["buses"].items():
        if info["status"] == "STAGE_1_PENDING" and reg not in RUNNING_SOLVES:
            RUNNING_SOLVES.add(reg)
            threading.Thread(target=threaded_solve, args=(reg,), daemon=True).start()

def threaded_solve(reg):
    try:
        state = load_state()
        info = state["buses"].get(reg, {})
        session_id = info.get("sessionId")
        turn_id = info.get("turnId")
        log(f"Starting Async Stage 1 Solve for {reg}...")
        push_activity(session_id, turn_id, "route", "Stage 1 solver queued", f"Preparing polyline construction for {reg}.", node="STAGE_1", status="pending", phase="stage1_solver")
        success, out = run_script(STAGE_1_SOLVER, ["--bus", reg], session_id=session_id, turn_id=turn_id, node="STAGE_1", phase="stage1_solver")
        
        state = load_state()
        if reg not in state["buses"]:
            return

        info = state["buses"][reg]
        if success:
            log(f"Stage 1 Solve Success for {reg}")
            info["status"] = "WAITING_FOR_HITL"
            info["last_update"] = datetime.now().isoformat()
            push_session_alert(session_id, turn_id, "SYSTEM_ALERT", f"[{reg}] Stage 1 polyline construction is complete and ready for your review (HITL audit). Once you have verified the data, the bus will be automatically added for discovery.", {"phase": "ready_for_audit"})
        else:
            log(f"Stage 1 Solve FAILED for {reg}")
            info["status"] = "ERROR_STAGE_1"
            info["last_update"] = datetime.now().isoformat()
            
            # Log Archival Infrastructure
            log_dir = os.path.join(BASE_DIR, "Pipeline_Logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f"error_{reg}.log")
            with open(log_file, 'w', encoding='utf-8') as lf:
                lf.write(f"--- STAGE 1 SOLVE FAILURE FOR {reg} ---\n")
                lf.write(f"Timestamp: {datetime.now().isoformat()}\n\n")
                lf.write(out)
            
            push_session_alert(session_id, turn_id, "SYSTEM_ALERT", f"[{reg}] CRITICAL FAILURE in Stage 1! Error log generated.", {"phase": "error"})
        
        save_state(state)
    except Exception as e:
        log(f"[THREAD ERROR] {e}")
    finally:
        RUNNING_SOLVES.remove(reg)


def check_hitl_completion():
    if not os.path.exists(SECURED_FILE):
        return
    
    try:
        with open(SECURED_FILE, 'r', encoding='utf-8') as f:
            secured_data = json.load(f)
    except Exception:
        return

    secured_regs = {b["reg_no"] for b in secured_data.get("buses", [])}
    
    state = load_state()
    triggered_discovery = False
    
    for reg, info in state["buses"].items():
        if info["status"] == "WAITING_FOR_HITL":
            session_id = info.get("sessionId")
            turn_id = info.get("turnId")
            if reg in secured_regs:
                log(f"[ACTION] User Secured {reg}! Transitioning to Stage 4...")
                info["status"] = "DISCOVERY_PENDING"
                info["last_update"] = datetime.now().isoformat()
                push_session_alert(session_id, turn_id, "SYSTEM_ALERT", f"[{reg}] PHITL sync complete. Discovery rebuild started.", {"phase": "phitl_synced"})
                triggered_discovery = True
            else:
                # Proactive status update
                log(f"[STAGED] {info['name']} ({reg}) is waiting in the HITL Refinery.")
                log(f"         >>> Please verify and click [SECURE] in the Route Explorer UI.")
            
    if triggered_discovery:
        save_state(state)
        log("--- TRIGGERING FINAL DISCOVERY PIPELINE (STAGE 4) ---")
        for reg, info in state["buses"].items():
            if info["status"] == "DISCOVERY_PENDING":
                push_activity(info.get("sessionId"), info.get("turnId"), "route", "Discovery rebuild started", f"Rebuilding searchable network bundles after PHITL sync for {reg}.", node="DISCOVERY", status="pending", phase="discovery_rebuild")
        success, out = run_script(RAPTOR_BUILDER)
        if success:
            log("SUCCESS: Discovery Bundle Rebuilt. Bus is now searchable.")
            for reg, info in state["buses"].items():
                if info["status"] == "DISCOVERY_PENDING":
                    info["status"] = "COMPLETED"
                    push_session_alert(
                        info.get("sessionId"),
                        info.get("turnId"),
                        "SYSTEM_ALERT",
                        f"[{reg}] Discovery rebuild complete. Bus is now ready for discovery.",
                        {"phase": "discovery_ready"}
                    )
            save_state(state)
        else:
            log(f"CRITICAL ERROR in Discovery Build: {out}")

import socket

def _env_flag(name, default=True):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}

def _hitl_visible_console_enabled():
    return os.name == "nt" and _env_flag("HITL_SERVER_VISIBLE_CONSOLE", True)

def is_server_alive(host="127.0.0.1", port=5000):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

def cleanup_managed_hitl_server():
    global _HITL_SERVER_PROCESS, _HITL_SERVER_LOG_HANDLE
    process = _HITL_SERVER_PROCESS
    _HITL_SERVER_PROCESS = None
    if process is not None and process.poll() is None:
        log(f"[HITL] Stopping managed HITL Server PID {process.pid}...")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    if _HITL_SERVER_LOG_HANDLE is not None:
        try:
            _HITL_SERVER_LOG_HANDLE.close()
        except OSError:
            pass
        _HITL_SERVER_LOG_HANDLE = None

atexit.register(cleanup_managed_hitl_server)

def ensure_hitl_server():
    global _SERVER_LAUNCH_TIME, _HITL_SERVER_PROCESS, _HITL_SERVER_LOG_HANDLE, _HITL_SERVER_AUTOSTART_WARNED, _HITL_SERVER_ONLINE_LOGGED
    if not _env_flag("HITL_SERVER_AUTOSTART", True):
        if not _HITL_SERVER_AUTOSTART_WARNED:
            log("[HITL] Autostart disabled by HITL_SERVER_AUTOSTART=0. Start HITL_Pipeline_new/hitl_server.py manually when needed.")
            _HITL_SERVER_AUTOSTART_WARNED = True
        return

    if _HITL_SERVER_PROCESS is not None and _HITL_SERVER_PROCESS.poll() is not None:
        log(f"[HITL] Managed HITL Server PID {_HITL_SERVER_PROCESS.pid} exited with code {_HITL_SERVER_PROCESS.returncode}.")
        _HITL_SERVER_PROCESS = None

    if is_server_alive():
        if _SERVER_LAUNCH_TIME > 0:
            log("[SUCCESS] HITL Server is now ONLINE and responding.")
            _SERVER_LAUNCH_TIME = 0
            _HITL_SERVER_ONLINE_LOGGED = True
        elif _HITL_SERVER_PROCESS is not None and not _HITL_SERVER_ONLINE_LOGGED:
            log(f"[HITL] Managed HITL Server is ONLINE at http://127.0.0.1:5000 (PID {_HITL_SERVER_PROCESS.pid}).")
            _HITL_SERVER_ONLINE_LOGGED = True
        return

    # Cooldown check: give the server up to 90 seconds to warm up (proximity engine prewarm is slow)
    if _SERVER_LAUNCH_TIME > 0:
        elapsed = time.time() - _SERVER_LAUNCH_TIME
        if elapsed < 90:
            if int(elapsed) % 15 == 0: # Log every 15 seconds
                log(f"           Server is still warming up ({int(elapsed)}s elapsed)...")
            return

    log("[CRITICAL] HITL Server is DOWN. Attempting to bring it online...")
    _SERVER_LAUNCH_TIME = time.time()
    _HITL_SERVER_ONLINE_LOGGED = False
    server_path = os.path.join(BASE_DIR, "HITL_Pipeline_new", "hitl_server.py")
    run_dir = os.path.join(BASE_DIR, "HITL_Pipeline_new")
    visible_console = _hitl_visible_console_enabled()
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "hitl_server.log")
    pid_path = os.path.join(log_dir, "hitl_server.pid")
    if _HITL_SERVER_LOG_HANDLE is not None:
        try:
            _HITL_SERVER_LOG_HANDLE.close()
        except OSError:
            pass
    stdout_target = None
    stderr_target = None
    creationflags = 0
    if visible_console:
        creationflags = subprocess.CREATE_NEW_CONSOLE
    else:
        _HITL_SERVER_LOG_HANDLE = open(log_path, "a", encoding="utf-8", buffering=1)
        stdout_target = _HITL_SERVER_LOG_HANDLE
        stderr_target = subprocess.STDOUT
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    _HITL_SERVER_PROCESS = subprocess.Popen(
        [sys.executable, server_path],
        cwd=run_dir,
        stdout=stdout_target,
        stderr=stderr_target,
        creationflags=creationflags,
    )
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(_HITL_SERVER_PROCESS.pid))
    log(f"[HITL] Started managed HITL Server PID {_HITL_SERVER_PROCESS.pid}.")
    if visible_console:
        log("[HITL] Visible terminal opened for HITL Server. Close that window or press Ctrl+C there to stop it.")
        log("[HITL] Set HITL_SERVER_VISIBLE_CONSOLE=0 to run it hidden with file logging instead.")
    else:
        log(f"[HITL] Child log: {log_path}")
    log(f"[HITL] PID file:  {pid_path}")
    log("           Waiting for server to initialize (this may take up to 90s)...")

# --- FILE EXPLORER ENDPOINT ---
@app.get("/files", dependencies=[Depends(verify_token)])
async def list_files_endpoint():
    """Lists files in the project for the UI @mention feature."""
    file_list = [
        {"name": "file", "path": "file", "alias_for": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json"},
        {"name": "secure", "path": "secure", "alias_for": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json"},
        {"name": "output", "path": "output", "alias_for": "HITL_Pipeline_new/BD_Phase1_HITL_polyline_output.json"},
        {"name": "tt", "path": "tt", "alias_for": "HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json"},
        {"name": "input", "path": "input", "alias_for": "HITL_Pipeline_new/BD_Phase1_HITL_input.json"},
        {"name": "master", "path": "master", "alias_for": "Polyline_Drawing_Pipeline/BusData_Phase_1.json"},
        {"name": "stage1", "path": "stage1", "alias_for": "Polyline_Drawing_Pipeline/Stage_1_data.json"},
    ]
    exclude_dirs = {".git", "__pycache__", ".gemini", "venv", ".ipynb_checkpoints"}
    exclude_exts = {".pyc", ".pyo", ".pyd", ".exe", ".bin"}
    
    for root, dirs, files in os.walk(BASE_DIR):
        # In-place modification to skip excluded dirs
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if any(file.endswith(ext) for ext in exclude_exts):
                continue
                
            rel_path = os.path.relpath(os.path.join(root, file), BASE_DIR)
            file_list.append({
                "name": file,
                "path": rel_path.replace("\\", "/")
            })
            
    return sorted(file_list, key=lambda x: x["path"])

def _sync_invoke_graph(messages_payload, config):
    """Runs gemma_graph.app.invoke synchronously. Called from a thread."""
    return gemma_graph.app.invoke(messages_payload, config=config)

def _extract_reply(result):
    """Extracts the text reply and thought from a LangGraph result dict."""
    latest_msg_obj = result["messages"][-1]
    content = getattr(latest_msg_obj, "content", latest_msg_obj)
    thought = result.get("thought", "Action complete.")
    
    if isinstance(content, (list, tuple)):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                text_val = block.get("text") or block.get("content") or ""
                if text_val and not isinstance(text_val, (dict, list)):
                    text_parts.append(str(text_val))
                if block.get("type") == "thinking" or "thought" in block:
                    potential_thought = block.get("thought") or block.get("text")
                    if potential_thought: thought = str(potential_thought)
            else:
                text_parts.append(str(block))
        reply = " ".join(text_parts).strip()
    else:
        reply = str(content).strip()

    if "THOUGHT:" in reply and "RESPONSE:" in reply:
        parts = reply.split("RESPONSE:")
        thought = parts[0].replace("THOUGHT:", "").strip()
        reply = parts[1].strip()
    
    return reply, thought


def _summarize_tool_args(args: Any) -> str:
    if isinstance(args, dict):
        keys = list(args.keys())[:5]
        if not keys:
            return "No parameters."
        return "Parameters: " + ", ".join(keys)
    return _safe_snippet(args, 120) or "Parameters unavailable."


def _build_trace_from_result(result: Dict[str, Any]) -> List[TraceStep]:
    trace: List[TraceStep] = []

    intent = result.get("intent")
    if intent:
        trace.append(TraceStep(
            node="router",
            action="Intent classified",
            detail=intent.replace("_", " "),
            status="ok"
        ))

    files = result.get("files") or []
    target_file = result.get("target_file")
    if files:
        detail = f"{len(files)} file reference(s) in scope."
        if target_file:
            detail += f" Primary target: {target_file}."
        trace.append(TraceStep(
            node="file_scope",
            action="Resolved @file context",
            detail=_safe_snippet(detail),
            status="ok"
        ))

    messages = result.get("messages") or []
    last_tool_ai = next((m for m in reversed(messages) if AIMessage and isinstance(m, AIMessage) and getattr(m, "tool_calls", None)), None)
    if last_tool_ai:
        for call in list(getattr(last_tool_ai, "tool_calls", []))[:3]:
            tool_name = call.get("name", "tool")
            trace.append(TraceStep(
                node="tool",
                action=f"Selected tool `{tool_name}`",
                detail=_summarize_tool_args(call.get("args")),
                status="ok"
            ))

    thought = result.get("thought")
    if thought:
        trace.append(TraceStep(
            node="model",
            action="Model reasoning summary",
            detail=_safe_snippet(thought, 220),
            status="ok"
        ))

    return trace

@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_token)])
async def chat_endpoint(req: ChatRequest):
    if not gemma_graph:
        return ChatResponse(reply="Gemma Graph is offline: Check imports.", node="SYSTEM")
        
    sid = req.sessionId or "default"
    turn_id = req.turnId
    if sid in SESSIONS_IN_FLIGHT:
        return ChatResponse(reply="Session is busy processing a previous request. Please wait.", node="SYSTEM")

    SESSIONS_IN_FLIGHT.add(sid)
    
    try:
        sid = req.sessionId or "default"
        safe_session_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", sid)
        session_thread_id = f"session_{safe_session_id}"
        config = {"configurable": {"thread_id": session_thread_id}}

        # --- IMMEDIATE PERSISTENCE (Save User Message Early) ---
        history = _load_session_history(sid)
        history.append({
            "role": "user",
            "text": req.message or ("Extract schedule" if req.image else ""),
            "image": bool(req.image),
            "turnId": turn_id,
            "timestamp": datetime.now().isoformat()
        })
        _save_session_history(sid, history)
        
        result = None
        if req.image:
            log("[CHAT] Image request received; processing in the main Gemma session.")
            push_activity(
                sid,
                turn_id,
                "model",
                "Image analysis started",
                "Analyzing and processing the uploaded image in the current session.",
                node="MODEL",
                status="pending",
                phase="image_analysis",
            )

            img_data = req.image
            if "," in img_data:
                img_data = img_data.split(",", 1)[1]

            direct_prompt = (
                "Analyze the uploaded image and handle the complete task in this same Gemma session.\n\n"
                "If the image contains a user travel question, answer it directly using the available route/timetable tools.\n"
                "If the image contains bus schedule/timetable data, extract the data into the strict V5.0.0 JSON schema, "
                "check for duplicate or active-audit buses, and persist only truly new buses to Polyline_Drawing_Pipeline/Stage_1_data.json.\n"
                "If the image contains both a user question and bus data, answer the question and process the bus data in this same turn.\n\n"
                "Persistence rules:\n"
                "1) Check secured + master registry by reg_no and/or bus_name before staging.\n"
                "2) If the bus is already in Stage_1/HITL audit, report the active audit state instead of treating it as a failed duplicate.\n"
                "3) If it is a completed registry duplicate, do not enqueue Stage_1 and return a clear `Pipeline Not updated: [Failed] ... duplicate` message.\n"
                "4) If it is new, save it to Stage_1_data.json and return the actual pipeline result.\n\n"
                "Reply requirements:\n"
                "- Always show the generated/extracted JSON in the visible reply lane as a fenced ```json block.\n"
                "- Keep the JSON in the final reply even if you also save it, reject it as duplicate, or answer a travel question.\n"
                "- Do not put the generated JSON only in thought/internal reasoning.\n\n"
                f"USER_REQUEST_CONTEXT: {req.message or '[none]'}"
            )
            image_payload = {
                "messages": [HumanMessage(content=[
                    {"type": "text", "text": direct_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}},
                ])]
            }
            result = await asyncio.to_thread(_sync_invoke_graph, image_payload, config)
            reply_probe, _ = _extract_reply(result)

            if is_recoverable_model_error_text(reply_probe):
                push_activity(
                    sid,
                    turn_id,
                    "model",
                    "Transient model retry",
                    "The image request hit a temporary upstream model issue. Retrying once in the same session.",
                    node="MODEL",
                    status="pending",
                    phase="model_retry",
                )
                await asyncio.sleep(1.2)
                result = await asyncio.to_thread(_sync_invoke_graph, image_payload, config)

            log("[CHAT] Main-session image processing complete.")
        else:
            # --- TEXT CHAT PATH ---
            log(f"[CHAT] Text query received/transcribed: '{req.message[:80]}...'")
            push_activity(sid, turn_id, "route", "Request accepted", _safe_snippet(req.message, 90), node="ROUTER", status="pending", phase="routing")
            if "@" in (req.message or ""):
                push_activity(sid, turn_id, "tool", "File-scope detection", "Detected @file-style references and prepared scoped dataset access.", node="FILE_SCOPE", status="pending", phase="routing")
            push_activity(sid, turn_id, "model", "Gemma reasoning pass", "Preparing intent classification, tool selection, and response synthesis.", node="MODEL", status="pending", phase="routing")
            
            text_payload = {"messages": [HumanMessage(content=req.message)]}
            result = await asyncio.to_thread(_sync_invoke_graph, text_payload, config)
            log(f"[CHAT] Response generated.")

        reply, thought = _extract_reply(result)
        trace = _build_trace_from_result(result)
        
        # --- PERSIST TO BACKEND SESSION (Final Text Reply) ---
        history = _load_session_history(sid)
        history.append({
            "role": "agent",
            "text": reply,
            "thought": thought,
            "node": "ORCHESTRATOR",
            "turnId": turn_id
        })
        _save_session_history(sid, history)
        
        # Release lock for text chat
        if sid in SESSIONS_IN_FLIGHT: SESSIONS_IN_FLIGHT.remove(sid)
        
        return ChatResponse(reply=reply, thought=thought, node="ORCHESTRATOR", turnId=turn_id, trace=trace)
        
    except Exception as e:
        # Ensure lock is released on error
        if sid in SESSIONS_IN_FLIGHT: SESSIONS_IN_FLIGHT.remove(sid)
        import traceback
        error_msg = str(e)
        log(f"[CHAT ERROR] {error_msg}")
        traceback.print_exc()
        push_activity(sid, turn_id, "trace", "Backend error", error_msg[:200], node="SYSTEM", status="error", phase="error")
        raise HTTPException(status_code=500, detail=error_msg)

def orchestrator_loop():
    log("================================================================")
    log("  PURULIA TRANSIT PIPELINE ORCHESTRATOR v1.3 (UNIFIED MODE)")
    log("================================================================")
    log("Monitoring Pipeline, Server Health & Gemma API...")
    
    while True:
        try:
            ensure_hitl_server()
            update_master_bus_count()
            run_stage_1_ingestor()
            run_stage_1_solver()
            check_hitl_completion()
        except Exception as e:
            log(f"[LOOP ERROR] {e}")
            
        # Yield to allow SSE messages to flush instantly
        time.sleep(1)

def main():
    # Start the legacy loop in a background thread
    threading.Thread(target=orchestrator_loop, daemon=True).start()
    
    # Start the FastAPI server on port 8000
    log("[SERVER] Launching Unified API Gateway on http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
