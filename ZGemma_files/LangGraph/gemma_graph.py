import os
import sys
import subprocess
from typing import Annotated, Sequence, TypedDict, List, Dict, Any, Optional, Tuple
from typing_extensions import TypedDict
import json
import re
import time
from datetime import datetime

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, RemoveMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

# RAPTOR Journey Planner Tools
from ZGemma_files.LangGraph.raptor_tools import find_transit_route, list_transit_stops
from ZGemma_files.LangGraph.identity_resolver import (
    active_audit_message,
    bus_label,
    load_registry_buses,
    resolve_active_audit_state,
    resolve_bus_identity,
)

# Load secrets from package
try:
    from ASecrets.secrets_config import GOOGLE_AI_STUDIO_KEY
except ImportError:
    GOOGLE_AI_STUDIO_KEY = os.getenv("GOOGLE_AI_STUDIO_KEY", "")

# Define the state
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    context: str # Global identity/instructions
    thought: str # Current reasoning process
    files: List[Dict[str, str]] # Resolved @file mentions
    target_file: Optional[str]
    intent: str
    autonomy_level: str
    working_memory: str  # FIX 1: Entity scratchpad for cross-turn recall
    pending_hybrid_data: Optional[str]  # Injected observed JSON for HYBRID disambiguation

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FILE_ALIASES = {
    "file": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json",
    "secure": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json",
    "secured": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json",
    "secured_file": "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json",
    "polyline": "HITL_Pipeline_new/BD_Phase1_HITL_polyline_output.json",
    "output": "HITL_Pipeline_new/BD_Phase1_HITL_polyline_output.json",
    "polyline_output": "HITL_Pipeline_new/BD_Phase1_HITL_polyline_output.json",
    "tt": "HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json",
    "tt_output": "HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json",
    "timetable": "HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json",
    "input": "HITL_Pipeline_new/BD_Phase1_HITL_input.json",
    "hitl_input": "HITL_Pipeline_new/BD_Phase1_HITL_input.json",
    "master": "Polyline_Drawing_Pipeline/BusData_Phase_1.json",
    "stage1": "Polyline_Drawing_Pipeline/Stage_1_data.json",
    "queue": "Polyline_Drawing_Pipeline/Stage_1_data.json",
    "villages": "HITL_Pipeline_new/Villages_data/Villages_data.json",
    "villages_data.json": "HITL_Pipeline_new/Villages_data/Villages_data.json",
    "gemma_4_good_hackathon": "ZGemma_files/gemma-4-good-hackathon-documentation.md",
    "docs": "ZGemma_files/gemma-4-good-hackathon-documentation.md",
}


def _strip_reply_postfixes(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"\s*\[TRANSPORT\]\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

WRITE_ALLOWED_FILES = {
    "HITL_Pipeline_new/BD_Phase1_HITL_Secured.json",
    "HITL_Pipeline_new/BD_Phase1_HITL_TT_output.json",
    "HITL_Pipeline_new/BD_Phase1_HITL_input.json",
    "Polyline_Drawing_Pipeline/BusData_Phase_1.json",
    "Polyline_Drawing_Pipeline/Stage_1_data.json",
}


def _message_to_text(content: Any) -> str:
    if isinstance(content, list):
        text_blocks = [
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") != "image_url"
        ]
        return " ".join(text_blocks)
    return str(content)

def _safe_rel_path(file_path: str) -> str:
    raw_path = str(file_path or "").strip().strip("'\"`.,;:)]}")
    raw_path = raw_path.lstrip("@").replace("\\", "/")
    raw_path = FILE_ALIASES.get(raw_path.lower(), raw_path)
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(PROJECT_ROOT, raw_path)
    abs_path = os.path.abspath(abs_path)
    project_root_abs = os.path.abspath(PROJECT_ROOT)
    if abs_path != project_root_abs and not abs_path.startswith(project_root_abs + os.sep):
        raise ValueError(f"Access denied. {file_path} is outside the project workspace.")
    rel_path = os.path.relpath(abs_path, project_root_abs).replace("\\", "/")
    return rel_path

def _abs_project_path(file_path: str) -> str:
    return os.path.join(PROJECT_ROOT, _safe_rel_path(file_path).replace("/", os.sep))

def _extract_file_mentions(text: str) -> List[Dict[str, str]]:
    mentions = []
    seen = set()
    for match in re.finditer(r"@([^\s]+)", text or ""):
        raw = match.group(1).strip().strip("'\"`.,;:)]}")
        if not raw:
            continue
        if raw.lower() in {"contextscopeitemmention", "contextscope"}:
            continue
        try:
            rel_path = _safe_rel_path(raw)
        except Exception as exc:
            mentions.append({"mention": f"@{raw}", "path": "", "status": f"ERROR: {exc}"})
            continue
        if rel_path in seen:
            continue
        seen.add(rel_path)
        abs_path = os.path.join(PROJECT_ROOT, rel_path.replace("/", os.sep))
        status = "FOUND" if os.path.exists(abs_path) else "MISSING"
        mentions.append({"mention": f"@{raw}", "path": rel_path, "status": status})
    return mentions

def _load_project_json(file_path: str) -> Tuple[str, Any]:
    rel_path = _safe_rel_path(file_path)
    abs_path = os.path.join(PROJECT_ROOT, rel_path.replace("/", os.sep))
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"{rel_path} not found.")
    with open(abs_path, "r", encoding="utf-8") as f:
        return rel_path, json.load(f)

def _save_project_json(file_path: str, data: Any) -> str:
    rel_path = _safe_rel_path(file_path)
    if rel_path not in WRITE_ALLOWED_FILES:
        raise PermissionError(f"Write denied for {rel_path}. Use an approved transit data file.")
    abs_path = os.path.join(PROJECT_ROOT, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return rel_path

def _normalize_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

def _iter_buses(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("buses"), list):
        return data["buses"]
    if isinstance(data, list):
        return [b for b in data if isinstance(b, dict)]
    return []

def _find_bus(data: Any, bus_query: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    query = str(bus_query or "").strip()
    q_norm = _normalize_token(query)
    q_lower = query.lower()
    candidates = []
    for bus in _iter_buses(data):
        reg = _normalize_token(bus.get("reg_no"))
        name = str(bus.get("bus_name") or bus.get("name") or "").strip()
        name_norm = _normalize_token(name)
        if q_norm and (q_norm == reg or q_norm == name_norm):
            return bus, [bus]
        if q_norm and (q_norm in reg or q_norm in name_norm or q_lower in name.lower()):
            candidates.append(bus)
    return (candidates[0], candidates) if candidates else (None, [])

def _find_trip(bus: Dict[str, Any], trip_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(trip_id or "").strip().lower()
    for movement in bus.get("movements", []):
        if str(movement.get("trip_id", "")).strip().lower() == wanted:
            return movement
    return None

@tool
def read_transit_file(file_path: str):
    """
    PEEK TOOL: Reads only the first 8,000 characters of a file to understand structure.
    FOR LARGE FILES (JSON/CSV): Use smart_grep or audit_hitl_data to fetch specific records.
    NEVER try to read full databases using this tool.
    """
    try:
        rel_path = _safe_rel_path(file_path)
        abs_path = _abs_project_path(rel_path)
    except Exception as e:
        return f"Error: {e}"

    if not os.path.exists(abs_path):
        return f"Error: File {rel_path} not found."
        
    try:
        file_size = os.path.getsize(abs_path)
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read(8000) 
            warning = f"\n\n[SURGICAL WARNING]: File is {file_size/1024:.1f} KB. This is a PEEK only. For specific bus records, use smart_grep or surgical audit tools." if file_size > 8000 else ""
            return content + warning
    except Exception as e:
        return f"Error: {e}"

@tool
def smart_grep(file_path: str, query: str):
    """
    Super-fast OS-level search for a bus name or ID.
    Finds the exact line and context in milliseconds.
    """
    try:
        rel_path = _safe_rel_path(file_path)
        abs_path = _abs_project_path(rel_path)
    except Exception as e:
        return f"Error: {e}"

    if not os.path.exists(abs_path):
        return f"Error: File {rel_path} not found."
        
    try:
        q = str(query or "").strip().strip("\"'")
        if not q:
            return "Error: Empty query."
        # Use findstr (Windows equivalent of grep) for maximum speed
        cmd = ["findstr", "/I", "/C:" + q, abs_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout
        if not output:
            return f"Query '{q}' not found."
            
        # --- SURGICAL GUARD ---
        # Never return more than 6,000 characters to prevent context window crash
        if len(output) > 6000:
            return output[:6000] + "\n\n[SURGICAL TRUNCATION]: Results exceed safety limits. Refine your query or use specific tools."
        return output
    except Exception as e:
        return f"Error: {e}"

@tool
def smart_registry_grep(query: str):
    """
    Searches for a query string in both the secured and master registry JSON files.
    Returns matching lines from HITL_Pipeline_new/BD_Phase1_HITL_Secured.json and Polyline_Drawing_Pipeline/BusData_Phase_1.json.
    Useful for detecting duplicate buses by reg_no or bus_name.
    """
    try:
        q_raw = str(query or "").strip().strip("\"'")
        if not q_raw:
            return "Error: Empty query."

        variants: List[str] = []
        variants.append(q_raw)

        m = re.search(r"\(([^)]+)\)", q_raw)
        if m:
            reg_in_parens = m.group(1).strip()
            if reg_in_parens:
                variants.append(reg_in_parens)

        q_compact = re.sub(r"\s+", "", q_raw)
        if q_compact and q_compact != q_raw:
            variants.append(q_compact)

        q_alnum = re.sub(r"[^A-Za-z0-9]+", "", q_raw)
        if q_alnum and q_alnum not in {q_raw, q_compact}:
            variants.append(q_alnum)

        if m:
            reg_alnum = re.sub(r"[^A-Za-z0-9]+", "", m.group(1))
            if reg_alnum and reg_alnum not in variants:
                variants.append(reg_alnum)

        seen = set()
        variants = [v for v in variants if v and not (v.lower() in seen or seen.add(v.lower()))]

        secured = _abs_project_path("HITL_Pipeline_new/BD_Phase1_HITL_Secured.json")
        master = _abs_project_path("Polyline_Drawing_Pipeline/BusData_Phase_1.json")

        results = []
        for label, path in (("SECURED", secured), ("MASTER", master)):
            if not os.path.exists(path):
                continue
            for v in variants:
                cmd = ["findstr", "/I", "/C:" + v, path]
                r = subprocess.run(cmd, capture_output=True, text=True)
                out = (r.stdout or "").strip()
                if not out:
                    continue
                if len(out) > 6000:
                    out = out[:6000] + "\n\n[SURGICAL TRUNCATION]: Results exceed safety limits. Refine your query."
                results.append(f"[{label}] (match='{v}') {out}")
                break

        if not results:
            return f"Query '{q_raw}' not found in registry."
        return "\n".join(results)
    except Exception as e:
        return f"Error: {e}"

@tool
def resolve_file_mentions(user_text: str):
    """
    Resolves @file mentions into safe project-relative file paths.
    Supports aliases: @file/@secure, @output, @tt, @input, @master, @stage1.
    Use this before any file-specific query or edit when the user mentions @file.
    """
    mentions = _extract_file_mentions(user_text)
    if not mentions:
        return json.dumps({
            "files": [],
            "hint": "No @file mention found. Ask for a file or use an alias like @secure, @output, @tt, @input, or @master."
        }, indent=2)
    return json.dumps({"files": mentions}, indent=2)

@tool
def summarize_transit_file(file_path: str):
    """
    Summarizes a transit JSON file without dumping the whole database into context.
    Returns metadata, bus count, locked-id count, and a few sample buses.
    """
    try:
        rel_path, data = _load_project_json(file_path)
        buses = _iter_buses(data)
        summary = {
            "file": rel_path,
            "top_level_type": type(data).__name__,
            "metadata": data.get("metadata", {}) if isinstance(data, dict) else {},
            "locked_id_count": len(data.get("locked_ids", [])) if isinstance(data, dict) else 0,
            "bus_count": len(buses),
            "sample_buses": [
                {
                    "bus_name": b.get("bus_name") or b.get("name"),
                    "reg_no": b.get("reg_no"),
                    "movement_count": len(b.get("movements", []))
                }
                for b in buses[:8]
            ]
        }
        return json.dumps(summary, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"

@tool
def query_bus_record(file_path: str, bus_query: str):
    """
    Finds one bus by registration number or bus name in a transit JSON file.
    Returns the matching bus record with ambiguity information if multiple buses match.
    """
    try:
        rel_path, data = _load_project_json(file_path)
        bus, candidates = _find_bus(data, bus_query)
        if not bus:
            return f"Error: Bus '{bus_query}' not found in {rel_path}."
        result = {
            "file": rel_path,
            "candidate_count": len(candidates),
            "ambiguous": len(candidates) > 1,
            "matched": bus,
        }
        if len(candidates) > 1:
            result["candidate_summary"] = [
                {"bus_name": c.get("bus_name"), "reg_no": c.get("reg_no")}
                for c in candidates[:10]
            ]
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"

@tool
def get_trip_stop(file_path: str, bus_query: str, trip_id: str, stop_index: int):
    """
    Returns a specific stoppage from a specific bus trip.
    stop_index is 1-based, so stop_index=2 means the second stoppage.
    """
    try:
        rel_path, data = _load_project_json(file_path)
        bus, candidates = _find_bus(data, bus_query)
        if not bus:
            return f"Error: Bus '{bus_query}' not found in {rel_path}."
        if len(candidates) > 1:
            return json.dumps({
                "error": "Ambiguous bus query. Use reg_no for exact selection.",
                "candidate_summary": [
                    {"bus_name": c.get("bus_name"), "reg_no": c.get("reg_no")}
                    for c in candidates[:10]
                ]
            }, indent=2, ensure_ascii=False)
        trip = _find_trip(bus, trip_id)
        if not trip:
            return f"Error: Trip '{trip_id}' not found for {bus.get('bus_name')} ({bus.get('reg_no')})."
        stops = trip.get("stops", [])
        if stop_index < 1 or stop_index > len(stops):
            return f"Error: stop_index {stop_index} is outside 1..{len(stops)}."
        return json.dumps({
            "file": rel_path,
            "bus_name": bus.get("bus_name"),
            "reg_no": bus.get("reg_no"),
            "trip_id": trip.get("trip_id"),
            "stop_index": stop_index,
            "stop": stops[stop_index - 1],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"

@tool
def remove_bus_from_file(file_path: str, bus_query: str, dry_run: bool = True):
    """
    Removes a bus from an approved transit JSON file by registration number or bus name.
    dry_run=True previews the exact change. Use dry_run=False only when the user asked to edit/remove.
    """
    try:
        rel_path, data = _load_project_json(file_path)
        buses = _iter_buses(data)
        bus, candidates = _find_bus(data, bus_query)
        if not bus:
            return f"Error: Bus '{bus_query}' not found in {rel_path}."
        if len(candidates) > 1:
            return json.dumps({
                "error": "Ambiguous bus query. Use reg_no for exact removal.",
                "candidate_summary": [
                    {"bus_name": c.get("bus_name"), "reg_no": c.get("reg_no")}
                    for c in candidates[:10]
                ]
            }, indent=2, ensure_ascii=False)

        new_buses = [b for b in buses if b is not bus]
        preview = {
            "file": rel_path,
            "dry_run": dry_run,
            "action": "remove_bus",
            "matched_bus": {"bus_name": bus.get("bus_name"), "reg_no": bus.get("reg_no")},
            "bus_count_before": len(buses),
            "bus_count_after": len(new_buses),
        }
        if dry_run:
            return json.dumps(preview, indent=2, ensure_ascii=False)

        if isinstance(data, dict):
            data["buses"] = new_buses
            locked_ids = data.get("locked_ids")
            if isinstance(locked_ids, list):
                reg = str(bus.get("reg_no") or "")
                name = str(bus.get("bus_name") or "")
                filtered_locked_ids = []
                for locked in locked_ids:
                    locked_text = str(locked)
                    locked_lower = locked_text.lower()
                    same_reg = bool(reg) and reg in locked_text
                    same_name = bool(name) and name.lower() in locked_lower
                    if not same_reg and not same_name:
                        filtered_locked_ids.append(locked)
                data["locked_ids"] = filtered_locked_ids
            if isinstance(data.get("metadata"), dict):
                data["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
                data["metadata"]["lock_count"] = len(data.get("locked_ids", []))
        elif isinstance(data, list):
            data = new_buses

        _save_project_json(rel_path, data)
        preview["status"] = "SUCCESS"
        return json.dumps(preview, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"

@tool
def update_bus_timetable(file_path: str, bus_query: str, trip_id: str, stop_name: str, arrival_time: Optional[str] = None, departure_time: Optional[str] = None, dry_run: bool = True):
    """
    Updates arrival/departure time for one stop in one bus trip.
    dry_run=True previews the edit. Use dry_run=False only when the user explicitly asks to edit/save.
    """
    try:
        rel_path, data = _load_project_json(file_path)
        bus, candidates = _find_bus(data, bus_query)
        if not bus:
            return f"Error: Bus '{bus_query}' not found in {rel_path}."
        if len(candidates) > 1:
            return json.dumps({
                "error": "Ambiguous bus query. Use reg_no for exact timetable edit.",
                "candidate_summary": [
                    {"bus_name": c.get("bus_name"), "reg_no": c.get("reg_no")}
                    for c in candidates[:10]
                ]
            }, indent=2, ensure_ascii=False)
        trip = _find_trip(bus, trip_id)
        if not trip:
            return f"Error: Trip '{trip_id}' not found for {bus.get('bus_name')} ({bus.get('reg_no')})."

        stop = None
        wanted = _normalize_token(stop_name)
        for candidate_stop in trip.get("stops", []):
            if _normalize_token(candidate_stop.get("name")) == wanted:
                stop = candidate_stop
                break
        if not stop:
            return f"Error: Stop '{stop_name}' not found in {trip_id}."

        before = {
            "arrival_time": stop.get("arrival_time"),
            "departure_time": stop.get("departure_time"),
        }
        after = dict(before)
        if arrival_time is not None:
            after["arrival_time"] = arrival_time
        if departure_time is not None:
            after["departure_time"] = departure_time

        preview = {
            "file": rel_path,
            "dry_run": dry_run,
            "action": "update_bus_timetable",
            "bus_name": bus.get("bus_name"),
            "reg_no": bus.get("reg_no"),
            "trip_id": trip.get("trip_id"),
            "stop_name": stop.get("name"),
            "before": before,
            "after": after,
        }
        if dry_run:
            return json.dumps(preview, indent=2, ensure_ascii=False)

        stop.update(after)
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            data["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_project_json(rel_path, data)
        preview["status"] = "SUCCESS"
        return json.dumps(preview, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"

@tool
def read_error_log(reg_no: str):
    """
    Reads the Stage 1 solver error log for a specific bus registration number.
    Use this when you receive a SYSTEM_ALERT about a Stage 1 failure.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    log_path = os.path.join(base_dir, "Pipeline_Logs", f"error_{reg_no}.log")
    if not os.path.exists(log_path):
        return f"Error: No log found for {reg_no} at {log_path}"
    with open(log_path, 'r', encoding='utf-8') as f:
        return f.read()

@tool
def retrigger_pipeline(reg_no: str):
    """
    Forces a bus back into the STAGE_1_PENDING status in the pipeline state.
    Use this ONLY after you have successfully patched the master database.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    state_file = os.path.join(base_dir, "pipeline_state.json")
    if not os.path.exists(state_file):
        return "Error: pipeline_state.json not found."
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    if reg_no not in state.get("buses", {}):
        return f"Error: {reg_no} not found in pipeline state."
    state["buses"][reg_no]["status"] = "STAGE_1_PENDING"
    state["buses"][reg_no]["last_update"] = datetime.now().isoformat()
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=4)
    return f"SUCCESS: Pipeline re-triggered for {reg_no}."

@tool
def audit_hitl_data(reg_no: str):
    """
    Fetches ONLY the relevant record for a specific bus from the massive HITL output file.
    Surgical lookup prevents context window overflow.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    hitl_out = os.path.join(base_dir, "HITL_Pipeline_new", "BD_Phase1_HITL_polyline_output.json")
    if not os.path.exists(hitl_out):
        return "Error: HITL output file not found."
        
    try:
        import json
        with open(hitl_out, 'r', encoding='utf-8') as f:
            data = json.load(f)
            bus_data = next((b for b in data.get("buses", []) if b.get("reg_no") == reg_no), None)
            if not bus_data:
                return f"Error: {reg_no} not found."
            return json.dumps(bus_data, indent=2)
    except Exception as e:
        return f"Error: {e}"


@tool
def patch_bus_data(reg_no: str, bus_name: str, patched_movements_json: str):
    """
    Surgically patches the 'movements' array for a specific bus in the Master Database.
    This tool is now optimized for large files (100k+ lines).
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    master_file = os.path.join(base_dir, "Polyline_Drawing_Pipeline", "BusData_Phase_1.json")
    
    try:
        new_movements = json.loads(patched_movements_json)
        with open(master_file, 'r', encoding='utf-8') as f:
            master = json.load(f)
            
        updated = False
        for b in master.get("buses", []):
            if b.get("reg_no") == reg_no:
                b["movements"] = new_movements
                updated = True
                break
                
        if not updated:
            return f"Error: {reg_no} not found in master database."
            
        with open(master_file, 'w', encoding='utf-8') as f:
            json.dump(master, f, indent=2)
        return f"SUCCESS: Master Database patched for {reg_no}."
    except Exception as e:
        return f"Error: {e}"

@tool
def python_interpreter(code: str):
    """
    Executes Python code for precise data analysis or to validate JSON structure before saving. 
    IMPORTANT: Use print() to output results. 
    Validate generated data against the schema and architectural rules defined in your global context.
    """
    print(f"\n[DEBUG] Gemma executing code:\n{code}\n")
    try:
        import io
        import contextlib
        import builtins
        import json, datetime, re, math, statistics, collections, itertools

        class ReadOnlyOS:
            path = os.path
            sep = os.sep
            linesep = os.linesep

            @staticmethod
            def getcwd():
                return os.getcwd()

            @staticmethod
            def listdir(path="."):
                return os.listdir(path)

            @staticmethod
            def stat(path):
                return os.stat(path)

            @staticmethod
            def scandir(path="."):
                return os.scandir(path)

        safe_os = ReadOnlyOS()

        def read_only_open(file, mode="r", *args, **kwargs):
            mode = mode or "r"
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                raise PermissionError(
                    "python_interpreter is read-only. Use save_to_file for all persistence."
                )
            return builtins.open(file, mode, *args, **kwargs)

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".")[0]
            allowed = {
                "json",
                "re",
                "datetime",
                "math",
                "statistics",
                "collections",
                "itertools",
                "os",
            }
            if root not in allowed:
                raise ImportError(f"Import denied in read-only python_interpreter: {name}")
            if root == "os":
                return safe_os
            return builtins.__import__(name, globals, locals, fromlist, level)

        safe_builtins = {
            "print": print,
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "isinstance": isinstance,
            "type": type,
            "sorted": sorted,
            "min": min,
            "max": max,
            "sum": sum,
            "any": any,
            "all": all,
            "abs": abs,
            "round": round,
            "next": next,
            "iter": iter,
            "reversed": reversed,
            "map": map,
            "filter": filter,
            "repr": repr,
            "getattr": getattr,
            "hasattr": hasattr,
            "Exception": Exception,
            "ValueError": ValueError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "FileNotFoundError": FileNotFoundError,
            "zip": zip,
            "open": read_only_open,
            "__import__": safe_import,
        }
        
        output_buffer = io.StringIO()
        local_vars = {
            "json": json,
            "os": safe_os,
            "datetime": datetime,
            "re": re,
            "math": math,
            "statistics": statistics,
            "collections": collections,
            "itertools": itertools,
        }
        
        with contextlib.redirect_stdout(output_buffer):
            exec(code, {"__builtins__": safe_builtins}, local_vars)
            
        result = output_buffer.getvalue().strip()
        return result if result else "Success: Code executed, but nothing was printed. Use print() to see results."
    except Exception as e:
        return f"Runtime Error: {e}"


@tool
def read_markdown_doc(file_path: str):
    """
    Reads a Markdown documentation file.
    Use this to read project guidelines, hackathon rules, or general text files.
    """
    try:
        rel_path = _safe_rel_path(file_path)
        abs_path = _abs_project_path(rel_path)
        
        if not os.path.exists(abs_path):
            return f"Error: File {rel_path} not found at {abs_path}."
            
        with open(abs_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"

@tool
def save_to_file(file_path: str, content: str):
    """
    Saves a string (usually JSON) to a file path. 
    Use this to persist extracted transit data or analysis.
    """
    try:
        sanitized_path = _safe_rel_path(file_path)
        abs_path = os.path.join(PROJECT_ROOT, sanitized_path.replace("/", os.sep))
        if sanitized_path not in WRITE_ALLOWED_FILES:
            return f"Error: Write denied for {sanitized_path}. Use an approved transit data file."
        
        # --- VIRTUAL REGISTRATION SCRIPT ---
        try:
            data = json.loads(content)
            
            buses_to_process = []
            if isinstance(data, dict) and "buses" in data:
                buses_to_process = data["buses"]
            elif isinstance(data, dict) and "bus_name" in data:
                buses_to_process.append(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "buses" in item:
                        buses_to_process.extend(item["buses"])
                    elif isinstance(item, dict) and "bus_name" in item:
                        buses_to_process.append(item)
            
            if buses_to_process:
                # Use the cached bus count from master_bus_count.json
                count_file = os.path.join(PROJECT_ROOT, "ZGemma_files", "master_bus_count.json")
                total_bus_count = 0
                
                if os.path.exists(count_file):
                    try:
                        with open(count_file, 'r', encoding='utf-8') as cf:
                            count_data = json.load(cf)
                            total_bus_count = count_data.get("count", 0)
                    except:
                        pass

                updated = False
                for bus in buses_to_process:
                    reg = bus.get("reg_no")
                    if not reg or str(reg).strip().lower() in ["null", "none", ""]:
                        total_bus_count += 1
                        bus["reg_no"] = f"UNKNOWN{total_bus_count}"
                        updated = True
                
                if updated:
                    content = json.dumps(data, indent=4)
        except Exception as e:
            pass # If JSON parsing fails, just fall through and save raw content
        # -----------------------------------

        if sanitized_path.endswith("Stage_1_data.json"):
            new_entry = json.loads(content)
            if os.path.exists(abs_path):
                with open(abs_path, 'r', encoding='utf-8') as f:
                    stage_data = json.load(f)
            else:
                stage_data = {"buses": []}

            stage_data.setdefault("buses", [])

            def _norm_token(v: Any) -> str:
                return re.sub(r"[^A-Z0-9]+", "", str(v or "").upper())

            def _bus_signature(bus: Dict[str, Any]) -> str:
                name = _norm_token(bus.get("bus_name") or bus.get("name"))
                parts = [name]
                for mv in (bus.get("movements") or []):
                    origin = _norm_token(mv.get("origin"))
                    dest = _norm_token(mv.get("destination"))
                    stops = mv.get("stops") or []
                    first = _norm_token(stops[0].get("name")) if stops else ""
                    last = _norm_token(stops[-1].get("name")) if stops else ""
                    parts.append(f"{origin}>{dest}>{first}>{last}")
                return "|".join(parts)

            existing_regs = {
                _norm_token(b.get("reg_no"))
                for b in stage_data.get("buses", [])
                if isinstance(b, dict) and b.get("reg_no")
            }
            existing_sigs = {
                _bus_signature(b)
                for b in stage_data.get("buses", [])
                if isinstance(b, dict)
            }

            # Cross-file uniqueness guard: do NOT enqueue buses already present in secured or master DB
            secured_path = os.path.join(PROJECT_ROOT, "HITL_Pipeline_new", "BD_Phase1_HITL_Secured.json")
            master_path = os.path.join(PROJECT_ROOT, "Polyline_Drawing_Pipeline", "BusData_Phase_1.json")

            def _file_contains_token(path: str, token: str) -> bool:
                if not token or not os.path.exists(path):
                    return False
                try:
                    # Chunked scan with overlap to avoid missing a token split across chunk boundaries
                    overlap = max(len(token) - 1, 0)
                    prev_tail = ""
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), ""):
                            hay = prev_tail + chunk
                            if token in hay:
                                return True
                            prev_tail = hay[-overlap:] if overlap else ""
                    return False
                except Exception:
                    return False

            def _load_json_if_exists(path: str) -> Any:
                if not os.path.exists(path):
                    return None
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        return json.load(fh)
                except Exception:
                    return None

            def _norm(v: Any) -> str:
                return re.sub(r"[^A-Z0-9]+", "", str(v or "").upper())

            def _stop_seq(bus: Dict[str, Any]) -> List[str]:
                seq: List[str] = []
                for mv in (bus.get("movements") or []):
                    for st in (mv.get("stops") or []):
                        n = _norm(st.get("name"))
                        if n:
                            seq.append(n)
                seen = set()
                out = []
                for x in seq:
                    if x not in seen:
                        out.append(x)
                        seen.add(x)
                return out

            def _is_subsequence(a: List[str], b: List[str]) -> bool:
                if not a:
                    return False
                it = iter(b)
                return all(any(x == y for y in it) for x in a)

            def _route_similar(a: List[str], b: List[str]) -> bool:
                if not a or not b:
                    return False
                if _is_subsequence(a, b) or _is_subsequence(b, a):
                    return True
                sa, sb = set(a), set(b)
                overlap_ct = len(sa & sb)
                denom = max(len(sa), len(sb))
                return denom > 0 and (overlap_ct / denom) >= 0.7

            def _merge_movements(existing_bus: Dict[str, Any], new_bus: Dict[str, Any]) -> Tuple[int, int]:
                existing_bus.setdefault("movements", [])
                existing_movs = existing_bus.get("movements") or []
                by_trip = {str(m.get("trip_id") or "").strip().lower(): m for m in existing_movs if isinstance(m, dict)}

                added = 0
                updated = 0
                for mv in (new_bus.get("movements") or []):
                    if not isinstance(mv, dict):
                        continue
                    trip_key = str(mv.get("trip_id") or "").strip().lower()
                    if not trip_key:
                        existing_movs.append(mv)
                        added += 1
                        continue
                    if trip_key not in by_trip:
                        existing_movs.append(mv)
                        by_trip[trip_key] = mv
                        added += 1
                        continue

                    ex_mv = by_trip[trip_key]
                    ex_mv.setdefault("stops", [])
                    ex_stops = ex_mv.get("stops") or []
                    ex_index = {_norm(s.get("name")): s for s in ex_stops if isinstance(s, dict) and s.get("name")}

                    for st in (mv.get("stops") or []):
                        if not isinstance(st, dict):
                            continue
                        key = _norm(st.get("name"))
                        if not key:
                            continue
                        if key not in ex_index:
                            ex_stops.append(st)
                            ex_index[key] = st
                            updated += 1
                            continue
                        ex_st = ex_index[key]
                        for k in ("arrival_time", "departure_time"):
                            if ex_st.get(k) in (None, "", "null") and st.get(k) not in (None, "", "null"):
                                ex_st[k] = st.get(k)
                                updated += 1

                existing_bus["movements"] = existing_movs
                return added, updated

            def _find_bus_match(buses: List[Dict[str, Any]], new_bus: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                new_reg = _norm(new_bus.get("reg_no"))
                if new_reg:
                    for b in buses:
                        if _norm(b.get("reg_no")) == new_reg:
                            return b

                new_name = _norm(new_bus.get("bus_name") or new_bus.get("name"))
                new_seq = _stop_seq(new_bus)
                best = None
                best_score = 0.0
                for b in buses:
                    b_name = _norm(b.get("bus_name") or b.get("name"))
                    if new_name and b_name and new_name != b_name:
                        continue
                    b_seq = _stop_seq(b)
                    if not _route_similar(new_seq, b_seq):
                        continue
                    sa, sb = set(new_seq), set(b_seq)
                    denom = max(len(sa), len(sb)) or 1
                    score = len(sa & sb) / denom
                    if score > best_score:
                        best_score = score
                        best = b
                return best

            def _merge_into_secured(bus: Dict[str, Any]) -> Tuple[bool, int, int]:
                secured = _load_json_if_exists(secured_path)
                if not isinstance(secured, dict):
                    secured = {"buses": [], "locked_ids": [], "metadata": {"created_at": datetime.now().isoformat()}}
                secured.setdefault("buses", [])
                secured.setdefault("locked_ids", [])
                if not isinstance(secured.get("metadata"), dict):
                    secured["metadata"] = {}

                match_bus = _find_bus_match(secured["buses"], bus)
                if match_bus:
                    a, u = _merge_movements(match_bus, bus)
                    secured["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    os.makedirs(os.path.dirname(secured_path), exist_ok=True)
                    with open(secured_path, 'w', encoding='utf-8') as f:
                        json.dump(secured, f, indent=2, ensure_ascii=False)
                    return True, a, u

                secured["buses"].append(bus)
                secured["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
                os.makedirs(os.path.dirname(secured_path), exist_ok=True)
                with open(secured_path, 'w', encoding='utf-8') as f:
                    json.dump(secured, f, indent=2, ensure_ascii=False)
                return True, 0, 0

            incoming_buses: List[Dict[str, Any]] = []
            if isinstance(new_entry, dict) and isinstance(new_entry.get("buses"), list):
                incoming_buses = [b for b in new_entry.get("buses", []) if isinstance(b, dict)]
            elif isinstance(new_entry, list):
                incoming_buses = [b for b in new_entry if isinstance(b, dict)]
            elif isinstance(new_entry, dict):
                incoming_buses = [new_entry]

            registry_buses = load_registry_buses([secured_path, master_path])
            appended = 0
            skipped = 0
            active_audit_buses: List[str] = []
            duplicate_buses: List[str] = []
            for bus in incoming_buses:
                raw_reg = str(bus.get("reg_no") or "").strip()
                reg_norm = _norm_token(raw_reg)
                raw_name = str(bus.get("bus_name") or bus.get("name") or "").strip()
                name_norm = _norm_token(raw_name)
                sig = _bus_signature(bus)

                # If bus already exists in secured/master, do not enqueue to Stage_1.
                # This is deterministic and does not depend on model tool choices.
                audit_state = resolve_active_audit_state(bus, PROJECT_ROOT)
                if audit_state.get("action") == "audit_active":
                    active_audit_buses.append(active_audit_message(bus, audit_state))
                    skipped += 1
                    continue

                identity = resolve_bus_identity(bus, registry_buses)
                if identity.get("action") == "duplicate":
                    duplicate_buses.append(bus_label(bus))
                    skipped += 1
                    continue

                if reg_norm and reg_norm in existing_regs:
                    skipped += 1
                    continue
                if sig in existing_sigs:
                    skipped += 1
                    continue

                stage_data["buses"].append(bus)
                if reg_norm:
                    existing_regs.add(reg_norm)
                existing_sigs.add(sig)
                appended += 1

            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                json.dump(stage_data, f, indent=2, ensure_ascii=False)
            if active_audit_buses and appended == 0 and not duplicate_buses:
                return "Audit state unchanged: " + " ".join(active_audit_buses)
            if duplicate_buses and appended == 0:
                if len(duplicate_buses) == 1:
                    return (
                        f"Pipeline Not updated: [Failed] Bus {duplicate_buses[0]} identified as duplicate. "
                        f"Trip data has not added."
                    )
                return (
                    f"Pipeline Not updated: [Failed] {len(duplicate_buses)} bus(es) identified as duplicate. "
                    f"Trip data has not added."
                )
            return f"Stage_1 queue updated: appended {appended}, skipped {skipped} duplicate(s)"

        if sanitized_path.endswith("BD_Phase1_HITL_Secured.json"):
            incoming = json.loads(content)

            if os.path.exists(abs_path):
                with open(abs_path, 'r', encoding='utf-8') as f:
                    secured = json.load(f)
            else:
                secured = {"buses": [], "locked_ids": [], "metadata": {"created_at": datetime.now().isoformat()}}

            if not isinstance(secured, dict):
                secured = {"buses": [], "locked_ids": [], "metadata": {"created_at": datetime.now().isoformat()}}
            secured.setdefault("buses", [])
            secured.setdefault("locked_ids", [])
            if not isinstance(secured.get("metadata"), dict):
                secured["metadata"] = {}

            def _norm(v: Any) -> str:
                return re.sub(r"[^A-Z0-9]+", "", str(v or "").upper())

            def _stop_seq(bus: Dict[str, Any]) -> List[str]:
                seq: List[str] = []
                for mv in (bus.get("movements") or []):
                    for st in (mv.get("stops") or []):
                        n = _norm(st.get("name"))
                        if n:
                            seq.append(n)
                # de-dup while preserving order
                seen = set()
                out = []
                for x in seq:
                    if x not in seen:
                        out.append(x)
                        seen.add(x)
                return out

            def _is_subsequence(a: List[str], b: List[str]) -> bool:
                if not a:
                    return False
                it = iter(b)
                return all(any(x == y for y in it) for x in a)

            def _route_similar(a: List[str], b: List[str]) -> bool:
                if not a or not b:
                    return False
                if _is_subsequence(a, b) or _is_subsequence(b, a):
                    return True
                sa, sb = set(a), set(b)
                overlap = len(sa & sb)
                denom = max(len(sa), len(sb))
                return denom > 0 and (overlap / denom) >= 0.7

            def _find_match(existing_buses: List[Dict[str, Any]], new_bus: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                new_reg = _norm(new_bus.get("reg_no"))
                if new_reg:
                    for b in existing_buses:
                        if _norm(b.get("reg_no")) == new_reg:
                            return b

                new_name = _norm(new_bus.get("bus_name") or new_bus.get("name"))
                new_seq = _stop_seq(new_bus)

                best = None
                best_score = 0.0
                for b in existing_buses:
                    b_name = _norm(b.get("bus_name") or b.get("name"))
                    if new_name and b_name and new_name != b_name:
                        continue
                    b_seq = _stop_seq(b)
                    if not _route_similar(new_seq, b_seq):
                        continue
                    # score by overlap ratio
                    sa, sb = set(new_seq), set(b_seq)
                    denom = max(len(sa), len(sb)) or 1
                    score = len(sa & sb) / denom
                    if score > best_score:
                        best_score = score
                        best = b
                return best

            def _merge_movements(existing_bus: Dict[str, Any], new_bus: Dict[str, Any]) -> Tuple[int, int]:
                existing_bus.setdefault("movements", [])
                existing_movs = existing_bus.get("movements") or []
                by_trip = {str(m.get("trip_id") or "").strip().lower(): m for m in existing_movs if isinstance(m, dict)}

                added = 0
                updated = 0
                for mv in (new_bus.get("movements") or []):
                    if not isinstance(mv, dict):
                        continue
                    trip_key = str(mv.get("trip_id") or "").strip().lower()
                    if not trip_key:
                        # If trip_id missing, treat as new movement
                        existing_movs.append(mv)
                        added += 1
                        continue
                    if trip_key not in by_trip:
                        existing_movs.append(mv)
                        by_trip[trip_key] = mv
                        added += 1
                        continue

                    # Same trip_id: merge stop times (fill missing only)
                    ex_mv = by_trip[trip_key]
                    ex_mv.setdefault("stops", [])
                    ex_stops = ex_mv.get("stops") or []
                    ex_index = {_norm(s.get("name")): s for s in ex_stops if isinstance(s, dict) and s.get("name")}

                    for st in (mv.get("stops") or []):
                        if not isinstance(st, dict):
                            continue
                        key = _norm(st.get("name"))
                        if not key:
                            continue
                        if key not in ex_index:
                            ex_stops.append(st)
                            ex_index[key] = st
                            updated += 1
                            continue
                        ex_st = ex_index[key]
                        for k in ("arrival_time", "departure_time"):
                            if ex_st.get(k) in (None, "", "null") and st.get(k) not in (None, "", "null"):
                                ex_st[k] = st.get(k)
                                updated += 1

                existing_bus["movements"] = existing_movs
                return added, updated

            incoming_buses: List[Dict[str, Any]] = []
            if isinstance(incoming, dict) and isinstance(incoming.get("buses"), list):
                incoming_buses = [b for b in incoming["buses"] if isinstance(b, dict)]
            elif isinstance(incoming, dict) and ("bus_name" in incoming or "movements" in incoming):
                incoming_buses = [incoming]
            elif isinstance(incoming, list):
                incoming_buses = [b for b in incoming if isinstance(b, dict)]

            merged_buses = 0
            appended_buses = 0
            added_movements = 0
            updated_stops = 0

            for nb in incoming_buses:
                match_bus = _find_match(secured["buses"], nb)
                if match_bus:
                    merged_buses += 1
                    a, u = _merge_movements(match_bus, nb)
                    added_movements += a
                    updated_stops += u
                else:
                    secured["buses"].append(nb)
                    appended_buses += 1

            secured["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            secured["metadata"]["bus_count"] = len(secured.get("buses", []))

            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                json.dump(secured, f, indent=2, ensure_ascii=False)

            return (
                f"Secure file updated: appended_bus={appended_buses}, merged_bus={merged_buses}, "
                f"added_movements={added_movements}, updated_stops={updated_stops}"
            )

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # --- AUTO-UPDATE CACHED COUNT ---
        if "BusData_Phase_1.json" in sanitized_path:
            try:
                count_file = os.path.join(PROJECT_ROOT, "ZGemma_files", "master_bus_count.json")
                import json as _json
                data = _json.loads(content)
                new_count = len(data.get("buses", []))
                with open(count_file, 'w', encoding='utf-8') as cf:
                    _json.dump({"count": new_count, "last_updated": datetime.now().isoformat()}, cf)
            except:
                pass
        # --------------------------------
        
        return f"Successfully saved to {sanitized_path}"
    except Exception as e:
        return f"Save Error: {e}"

class GemmaGraph:
    def __init__(self):
        # Tools
        self.read_tools = [
            resolve_file_mentions,
            summarize_transit_file,
            read_transit_file,
            smart_grep,
            query_bus_record,
            get_trip_stop,
            audit_hitl_data,
            read_error_log,
            python_interpreter,
            find_transit_route,
            list_transit_stops,
            read_markdown_doc,
        ]
        self.write_tools = [
            save_to_file,
            remove_bus_from_file,
            update_bus_timetable,
            patch_bus_data,
            retrigger_pipeline,
        ]
        self.tools = self.read_tools + self.write_tools
        self.tool_node = ToolNode(self.tools)
        
        # --- PROFESSIONAL TUNING APPLIED HERE ---
        self.llm = ChatGoogleGenerativeAI(
            model="gemma-4-31b-it",
            google_api_key=GOOGLE_AI_STUDIO_KEY,
            temperature=0.2,            # Lowered from 0.4 for strict JSON adherence
            top_p=0.95,                 # Nucleus sampling for professional-grade determinism
            top_k=40,                   # Top-K to filter out low-probability hallucinations
            max_output_tokens=8192,     # Gives the agent heavy breathing room for large JSONs
        ).bind_tools(self.tools)

        # Disambiguation LLM: NO tools bound to enforce "talk first" policy
        self.llm_no_tools = ChatGoogleGenerativeAI(
            model="gemma-4-31b-it",
            google_api_key=GOOGLE_AI_STUDIO_KEY,
            temperature=0.4,            # Keep conversational model slightly warmer
            top_p=0.90,
            top_k=40,
        )
        
        # --- EPHEMERAL IN-MEMORY PERSISTENCE ---
        self.memory = MemorySaver()
        self.workflow = self._create_workflow()
        self.app = self.workflow.compile(checkpointer=self.memory)

    def _create_workflow(self):
        graph = StateGraph(AgentState)

        graph.add_node("resolve_files", self.resolve_files_node)
        graph.add_node("router", self.router_node)
        graph.add_node("update_context", self.update_context_node)
        graph.add_node("chat", self.chat_node)
        graph.add_node("tools", self.tool_node)
        graph.add_node("disambiguation", self.disambiguation_node)

        # Edges
        graph.add_edge(START, "resolve_files")
        graph.add_edge("resolve_files", "router")
        
        graph.add_conditional_edges(
            "router",
            self.route_decision,
            {
                "to_context": "update_context",
                "to_disambiguation": "disambiguation",
                "to_chat": "chat"
            }
        )

        graph.add_edge("update_context", "chat")
        graph.add_edge("disambiguation", END)
        
        graph.add_conditional_edges(
            "chat",
            self.should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        
        graph.add_node("extract_entities", self.extract_entities_node)
        graph.add_edge("tools", "extract_entities")
        graph.add_edge("extract_entities", "chat")

        return graph

    def should_continue(self, state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "continue"
        return "end"

    def resolve_files_node(self, state: AgentState):
        """Normalizes @file mentions before the model decides what to do."""
        text = _message_to_text(state["messages"][-1].content)
        files = _extract_file_mentions(text)
        previous_files = state.get("files", []) or []
        previous_target_file = state.get("target_file")
        target_file = next((f["path"] for f in files if f.get("path") and f.get("status") == "FOUND"), None)
        if files:
            detail = ", ".join(
                f"{f.get('mention')} -> {f.get('path') or f.get('status')}"
                for f in files
            )
            thought = f"File Scope Resolver: {detail}"
        else:
            files = previous_files
            target_file = previous_target_file
            if target_file:
                thought = f"File Scope Resolver: No new @file mention; continuing with {target_file}."
            else:
                thought = "File Scope Resolver: No @file mention found; tools can still use explicit paths if needed."
        return {
            "files": files,
            "target_file": target_file,
            "autonomy_level": "trusted_write",
            "thought": thought,
        }

    def router_node(self, state: AgentState):
        """Analyzes user intent."""
        last_msg = _message_to_text(state["messages"][-1].content).lower()
        
        if any(kw in last_msg for kw in ["update context", "global context", "schema", "architecture", "rules"]):
            return {
                "intent": "update_context",
                "thought": "User is providing schema/architecture instructions. Routing to Context Update node."
            }

        if any(kw in last_msg for kw in ["remove", "delete", "drop"]):
            intent = "remove_bus"
        elif any(kw in last_msg for kw in ["edit", "update", "change", "correct", "fix"]) and any(kw in last_msg for kw in ["time", "timetable", "arrival", "departure", "stop"]):
            intent = "edit_timetable"
        elif any(kw in last_msg for kw in ["secure", "lock", "approve"]):
            intent = "secure_bus"
        elif any(kw in last_msg for kw in ["second stoppage", "2nd stoppage", "second stop", "2nd stop", "stoppage", "trip"]):
            intent = "trip_stop_query"
        elif any(kw in last_msg for kw in ["bus from", "go from", "go to", "travel from", "travel to", "reach", "route from", "journey", "how to get", "buses from", "bus to", "show me bus", "i want to go", "next bus", "nearby bus"]):
            intent = "journey_planner"
        elif any(kw in last_msg for kw in ["detail", "details", "information", "info", "summary", "what is", "give me"]):
            intent = "file_or_bus_query"
        else:
            intent = "general_transit_agent"
        
        return {
            "intent": intent,
            "thought": f"Intent Router: Classified request as {intent}. Routing to Standard Chat node."
        }

    def route_decision(self, state: AgentState):
        if state.get("intent") == "update_context" or "to Context Update node" in state.get("thought", ""):
            return "to_context"
        if state.get("pending_hybrid_data"):
            return "to_disambiguation"
        return "to_chat"

    def disambiguation_node(self, state: AgentState):
        """HYBRID disambiguation: NO tools. Presents data overview and asks user for direction."""
        user_query = _message_to_text(state["messages"][-1].content)
        observed_data = state.get("pending_hybrid_data", "")

        card_data = observed_data
        data_summary = observed_data[:400]
        try:
            data = json.loads(observed_data)
            if isinstance(data, dict) and "buses" in data and data["buses"]:
                bus = data["buses"][0]
                card_data = json.dumps(bus, indent=2, ensure_ascii=False)
                total = len(data["buses"])
                bname = bus.get("bus_name", "Unknown")
                reg = bus.get("reg_no", "N/A")
                trips = len(bus.get("movements", []))
                data_summary = f"{bname} ({reg}) — {trips} trip(s)"
                if total > 1:
                    data_summary += f" (plus {total - 1} more bus(es))"
            elif isinstance(data, list) and data:
                bus = data[0]
                card_data = json.dumps(bus, indent=2, ensure_ascii=False)
                data_summary = f"{bus.get('bus_name', 'Unknown')} ({bus.get('reg_no', 'N/A')})"
        except (json.JSONDecodeError, TypeError):
            pass

        is_bengali = any(ord(c) >= 0x0980 and ord(c) <= 0x09FF for c in user_query)
        lang = "bn" if is_bengali else "en"

        if lang == "bn":
            prompt = f"""তুমি পুরুলিয়া ট্রানজিট ওএস-এর হাইব্রিড ইনটেন্ট ডিস্যাম্বিগুয়েটর।

## তোমার ভূমিকা
ব্যবহারকারী একটি ছবি আপলোড করেছেন যাতে একটি যাত্রা প্রশ্ন এবং বাসের সময়সূচী উভয়ই আছে।
তোমার কোনো টুল নেই। তুমি শুধু ডেটা দেখাতে পারো এবং জিজ্ঞেস করতে পারো ব্যবহারকারী কী করতে চায়।

## ব্যবহারকারীর প্রশ্ন
{user_query}

## বের করা বাস ডেটা সারসংক্ষেপ
{data_summary}

## নির্দেশনা
১. বাংলায় উত্তর দাও।
২. বলো: "আমি তোমার যাত্রার প্রশ্ন এবং বাসের ডেটা দুটোই দেখতে পাচ্ছি।"
৩. নিচের `suggestion-card` ব্লকে বাসের ডেটা দেখাও।
৪. জিজ্ঞেস করো: "তুমি কি আগে তোমার যাত্রার জন্য বাস খুঁজতে চাও, নাকি বাসের ডেটা সেভ করতে চাও? দুটোই করতে পারো।"
৫. সংক্ষিপ্ত এবং স্বাভাবিক রাখো।

## আউটপুট ফরম্যাট
THOUGHT: [সংক্ষিপ্ত যুক্তি]
RESPONSE: [স্বাভাবিক বাংলা বার্তা এবং `suggestion-card` ব্লক]

গুরুত্বপূর্ণ: এই ফরম্যাটে ডেটা কার্ড দেখাও:
```suggestion-card
{card_data}
```"""
        else:
            prompt = f"""You are the Hybrid Intent Disambiguator.

## Your Role
The user uploaded an image containing BOTH a travel question AND bus schedule data.
You have NO tools available. Your only job is to present the data and ask what the user wants to do next.

## User's Question
{user_query}

## Extracted Bus Data Overview
{data_summary}

## Instructions
1. Reply in English only.
2. Acknowledge that you see both a journey question and bus data.
3. Present the bus data inside a `suggestion-card` block using the exact JSON below.
4. Ask: "Would you like to find buses for your journey first, or save this bus data to help others? You can do both."
5. Keep it warm, conversational, and brief.

## Output Format
THOUGHT: [brief reasoning]
RESPONSE: [natural language message with `suggestion-card` block]

CRITICAL: Use this exact format for the data card:
```suggestion-card
{card_data}
```"""

        response = self.llm_no_tools.invoke([HumanMessage(content=prompt)])

        working_memory = state.get("working_memory", "")
        hybrid_entry = f"[HYBRID_DATA] {observed_data}"
        existing_lines = [l.strip() for l in working_memory.split("\n") if l.strip()]
        if hybrid_entry not in existing_lines:
            existing_lines.append(hybrid_entry)

        return {
            "messages": [response],
            "working_memory": "\n".join(existing_lines),
            "pending_hybrid_data": None,
            "thought": "Hybrid disambiguation: presented data overview and asked for user direction."
        }

    def update_context_node(self, state: AgentState):
        """Injects the Purulia Transit Route Master schema and architectural rules into the global context."""
        last_msg_content = state["messages"][-1].content
        if isinstance(last_msg_content, list):
            text_blocks = [b.get("text", "") if isinstance(b, dict) else str(b) for b in last_msg_content if not isinstance(b, dict) or b.get("type") != "image_url"]
            last_msg_text = " ".join(text_blocks)
        else:
            last_msg_text = str(last_msg_content)
            
        meticulous_schema = """
        - **Tool-First Policy**: Whenever you generate a bus registration (JSON), you MUST execute `save_to_file`. The tool's output is your ONLY confirmation of success. 
        - **No Premature Narration**: Do NOT claim to have saved data in your text response unless the `save_to_file` tool has already returned a `SUCCESS` message. 
        - **Data Integrity**: You are responsible for ensuring uniqueness and schema compliance. Use `smart_grep` before every save. 
        - **Environmental Context**: Target `Polyline_Drawing_Pipeline/Stage_1_data.json` for all new registrations.

        ## Technical Architecture (JSON-HITL-v5)
        Any JSON generated MUST strictly adhere to this interface:

        ## System Logic & Architectural Rules (CRITICAL)
        1. **Dual-Directional Architecture (Purulia-Centric):** - If a route includes 'Purulia', direction MUST be 'UP' (inbound towards Purulia) or 'DOWN' (outbound away from Purulia).
           - Bypass routes (no Purulia stop) use 'towards [Destination]'.
        2. **Automated Pivot Splitting (No Hub-Through):** Purulia CANNOT be an intermediate stop. If a bus travels through Purulia, split it into two movements (e.g., UP to Purulia, then DOWN from Purulia).
        3. **Terminal Anchoring:** The `origin` string MUST exactly match the first stop's `name`. The `destination` string MUST exactly match the last stop's `name`.
        4. **Temporal Monotonicity & Terminal Completeness:** Stop times must increase chronologically. Origin `arrival_time` MUST be `null`. Destination `departure_time` MUST be `null`. However, the Destination MUST always have a valid `arrival_time` if visible in the schedule.
        5. **HITL Learning Block:** EVERY stop must contain the `hitl_learning` block. For new extractions, default to: `{"status": "INFERRED", "historical_offset_mins": null, "variance_range": null, "total_reports": 1}`.
        6. **Route Continuity (No Teleportation):** If a bus performs multiple trips (e.g., DOWN then UP), the `destination` of Trip 1 MUST physically match the `origin` of Trip 2.
        7. **Zero-Skip Integrity (Exhaustive Extraction):** You MUST extract every single timestamp visible in the image for every single stop. Do not summarize, skip, or use `null` for intermediary times if they are visible in the table. Dropping timestamps is a failure of logic.
        8. **Normalization (Data Cleaning):**
           - **Bus Name:** Strip prefixes/suffixes like "M/S", "TRAVEL'S", "COACH", "LINE", "ENTERPRISE". Keep only the primary identifier (e.g., "GOUTAM TRAVEL'S" -> "GOUTAM").
           - **Reg No:** Normalize to a clean alphanumeric string. Remove all hyphens, spaces, and dots. Uppercase only (e.g., "WB-55A-6647" -> "WB55A6647").
           - **Trip IDs:** STRICT FORMAT: "1st trip", "2nd trip", "3rd trip", etc.
           
        ## Multi-Layer Entity Resolution (CRITICAL FOR SAVING)
        Before appending a newly extracted schedule to the target database, you MUST use your reasoning and the `read_transit_file` or `python_interpreter` tools to check if the bus already exists. Do NOT blindly duplicate buses. Use the following heuristic:
        - **Fast-Path (No Conflict):** If the normalized `bus_name` and `reg_no` do not exist in the target database at all, it is a brand new bus. Do not waste tokens on deep layer reasoning. Just append it. If `reg_no` is unknown, leave it as `null`—the backend script will automatically assign it a virtual ID (e.g., `UNKNOWN115`).
        - **Layer 1 (Strict Identity):** If the extracted schedule has a `reg_no`, check if this exact `reg_no` exists in the target file. If YES, they are the same bus.
        - **Layer 2 (Dynamic Signature Match):** If the `reg_no` is missing or `null` in the new schedule, check if the normalized `bus_name` exists. If YES, compare the "Stoppage Signature" (the sequence of stops). Remember that a Stoppage Signature can be a SUBSET or partial route (e.g., "Purulia to Medinipur" is a subset trip of "Medinipur to Chandra to Purulia"). If the new schedule shares a highly similar directional subset route with the existing bus, REASON that they are the same bus.
        - **Layer 3 (Timetable Integration/Trip Merging):** Once you determine a bus already exists (via Layer 1 or 2), DO NOT overwrite it. Instead, check if the new schedule represents an entirely new trip (chronologically separate) or an update to an existing trip. If it's a new trip, APPEND the new `movement` (e.g., "3rd trip") to the EXISTING bus's `movements` array. Leave its `reg_no` untouched.
        - **Layer 4 (New Entity Fallback):** Only if Layer 1 and 2 find zero matches, append as a brand new bus object with `reg_no: null`.
        
        ## Directional & Rebound Consistency (v5.5.0)
        1. **Hub Registry Alignment**: To ensure correct coordinate snapping, you MUST use the exact hub name **"Bankura"** for all terminals/stops referring to the Bankura hub. Do not use "Bankura Bus Stand".
        2. **Directional Alignment**: For bypass routes, the `direction` field MUST be `towards <DESTINATION>`. The name used MUST match the `destination` field exactly (e.g., direction: "towards Bankura" for destination: "Bankura").
        3. **Topological Symmetry (Injection)**: Rebound trips MUST have identical stop sequences (reversed). If the source image is sparse for the return trip, surgically inject the missing intermediate stops from the forward trip with `null` timestamps. This is critical for the backend deduplication signatures.
        4. **Rebound Symmetry**: The `destination` of the 1st trip MUST be the exact, character-perfect `origin` of the 2nd trip.
        
        ## Typescript Interface
        ```typescript
        interface TransitData {
          buses: Array<{
            bus_name: string;
            reg_no: string | null; 
            primary_hub: string;
            _comment?: string;
            movements: Array<{
              trip_id: string; // STRICT FORMAT: "1st trip", "2nd trip", "3rd trip", etc. NEVER use descriptive names like "Morning Down".
              direction: string; // "UP", "DOWN", or "towards [Dest]"
              origin: string;
              destination: string;
              stops: Array<{
                name: string;
                arrival_time: string | null; // e.g. "06:40 AM"
                departure_time: string | null;
                stop_type: "ORIGIN" | "INTERMEDIARY" | "DESTINATION";
                hitl_learning: {
                  status: "INFERRED" | "VERIFIED";
                  historical_offset_mins: number | null;
                  variance_range: [number, number] | null;
                  total_reports: number;
                }
              }>
            }>
          }>
        }
        ```
        
        **The Surgical Indexing Rule (CRITICAL)**
        The project contains massive JSON files (100k+ lines). You are STRICTLY PROHIBITED from using `read_transit_file` to read entire databases.
        - To find a bus: Use `smart_grep(file, reg_no)`.
        - To audit HITL data: Use `audit_hitl_data(reg_no)`.
        - To read logs: Use `read_error_log(reg_no)`.
        
        Only use `read_transit_file` to "peek" at the first few lines of a file to understand its schema. If you violate this and cause a context overflow, the system will crash.

        ## Self-Correction Loop & Tool Usage (CRITICAL)
        1. **Validation:** You MUST use the `python_interpreter` tool to read the target database and validate the data against the rules above (including Multi-Layer Entity Resolution) BEFORE saving.
        2. **File Paths & The '@' Symbol:** The user often provides file paths prefixed with an '@' symbol (e.g., `@ZGemma_files/...` or `@Polyline_Drawing_Pipeline/...`). This '@' is just a conversational alias. When referencing these files in your Python scripts or `read_transit_file` tool, you MUST strip the '@' symbol and use standard relative paths (e.g., `ZGemma_files/...`).
        3. **Saving Data:** You MUST EXCLUSIVELY use the `save_to_file` tool to save or update JSON data. NEVER use the `python_interpreter` (e.g., `json.dump()`, `open(f, 'w')`) to write or append to a file. The `save_to_file` tool triggers essential backend scripts (like automatic `UNKNOWN` reg_no assignment) that will be completely bypassed if you write the file manually.
        """
        
        current_context = state.get("context", """
        You are Purulia Transit Intelligence System (PTIS).

        You are an AI-powered rural transit intelligence and mobility analysis system.

        Your PRIMARY capability is transforming messy real-world transport information into structured transit intelligence.

        You specialize in:
        - extracting bus schedules from noisy images
        - digitizing handwritten or printed timetable boards
        - parsing WhatsApp screenshots and social media transport posts
        - understanding chaotic rural transit data
        - converting unstructured transport information into structured JSON trip graphs
        - identifying routes, timings, directions, and stop sequences
        - validating and repairing transport schedules

        You ALSO assist with:
        - finding buses between locations
        - journey planning
        - route discovery
        - timetable lookup
        - transit database auditing
        - querying transport records from project files
        - analyzing @file datasets
        - validating and correcting structured transit data

        You can work with:
        - uploaded timetable images
        - screenshots
        - handwritten schedules
        - structured JSON
        - transit database files
        - @file references

        You are NOT just a conversational chatbot.
        You are a multimodal transport intelligence engine.

        GREETING POLICY (HIGH PRIORITY):

        Your greeting must immediately communicate that you are:
        1. A transit route assistant
        2. A multimodal schedule extraction system
        3. A transit data intelligence engine

        When users say things like:
        - hello
        - hi
        - hey
        - good morning
        - নমস্কার
        - হ্যালো
        - কেমন আছো

        your response MUST naturally mention:
        - route/journey assistance
        - timetable image extraction
        - structured trip data generation
        - @file transit dataset analysis

        Adapt to the user's language automatically.
        If the user speaks Bengali, greet in Bengali.
        If the user speaks English, greet in English.

        Do NOT behave like a generic chatbot.

        Your greeting should make users immediately understand that they can:
        - find buses and journey options between locations
        - upload timetable images
        - upload screenshots/posters
        - extract messy visual bus data into structured trip JSON
        - query transport files using @file

        Preferred English greeting style:

        "Hello — I can help you find buses between places, check routes and timetables, extract messy bus data from images into structured trip records, and analyze transit datasets using @file references."

        Preferred Bengali greeting style:

        "হ্যালো — আমি আপনাকে এক জায়গা থেকে আরেক জায়গার বাস খুঁজতে, রুট ও সময়সূচী দেখতে, ছবি বা স্ক্রিনশট থেকে এলোমেলো বাসের তথ্য structured trip data-তে রূপান্তর করতে, এবং @file transit dataset বিশ্লেষণ করতে সাহায্য করতে পারি।"

        Keep greetings concise, professional, and capability-oriented.

        Never undersell your multimodal extraction and structured-data capabilities.
        If the user asks what you can do, explicitly mention these three pillars first:
        1. Bus finding and journey planning
        2. Image-to-structured-bus-data extraction
        3. @file-based transit dataset querying
        """)
        updated_context = f"{current_context}\n\n{meticulous_schema}\n\nLatest User Instruction: {last_msg_text}"

        
        return {
            "context": updated_context,
            "thought": "Global context updated with Purulia Transit Route Master (v5.0.0) Architecture & Schema."
        }

    def extract_entities_node(self, state: AgentState):
        """Extracts bus entity references from tool results into working_memory."""
        current_memory = state.get("working_memory", "")
        new_entities = []
        for m in state["messages"][-4:]:
            content = getattr(m, 'content', '')
            if not isinstance(content, str):
                content = str(content)
            try:
                data = json.loads(content)
                if isinstance(data, dict) and 'matched' in data:
                    bus = data['matched']
                    ent = f"{bus.get('bus_name', '?')} ({bus.get('reg_no', '?')}) from {data.get('file', '?')}"
                    new_entities.append(ent)
                elif isinstance(data, dict) and 'bus_name' in data and 'reg_no' in data:
                    new_entities.append(f"{data['bus_name']} ({data['reg_no']})")
            except (json.JSONDecodeError, TypeError, ValueError):
                for pat in re.finditer(r'"bus_name"\s*:\s*"([^"]+)"', content):
                    reg_m = re.search(r'"reg_no"\s*:\s*"([^"]+)"', content)
                    new_entities.append(f"{pat.group(1)} ({reg_m.group(1) if reg_m else '?'})")
                    break
        if new_entities:
            existing = [e.strip() for e in current_memory.split("\n") if e.strip().startswith("-")]
            for ent in new_entities:
                line = f"- {ent}"
                if line not in existing:
                    existing.append(line)
            return {"working_memory": "\n".join(existing[-5:])}
        return {}

    def _trim_messages(self, messages):
        """Keeps entity-bearing messages alive longer than naive window."""
        if len(messages) <= 16:
            return list(messages)
        recent = list(messages[-10:])
        entity_anchor = None
        for m in reversed(messages[:-10]):
            content = getattr(m, 'content', '')
            if not isinstance(content, str): content = str(content)
            if any(k in content for k in ['"bus_name"', '"reg_no"', '"matched"']):
                entity_anchor = m
                break
        kept = []
        if messages[0] not in recent:
            kept.append(messages[0])
        if entity_anchor and entity_anchor not in recent and entity_anchor not in kept:
            kept.append(entity_anchor)
        kept.extend(recent)
        return kept

    def _compress_old_tool_results(self, messages, recent_count=6):
        """Compresses old tool results to save context tokens."""
        compressed = []
        cutoff = len(messages) - recent_count
        for i, m in enumerate(messages):
            if i < cutoff and isinstance(m, ToolMessage) and len(getattr(m, 'content', '')) > 400:
                content = m.content
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and 'matched' in data:
                        bus = data['matched']
                        summary = f"[Compressed] Bus: {bus.get('bus_name')} ({bus.get('reg_no')}), File: {data.get('file')}, Trips: {len(bus.get('movements', []))}"
                        m = ToolMessage(content=summary, tool_call_id=m.tool_call_id)
                    elif isinstance(data, dict) and 'bus_count' in data:
                        m = ToolMessage(content=f"[Compressed] File: {data.get('file')}, {data.get('bus_count')} buses", tool_call_id=m.tool_call_id)
                except (json.JSONDecodeError, TypeError):
                    if len(content) > 800:
                        m = ToolMessage(content=content[:400] + "\n[TRUNCATED]", tool_call_id=m.tool_call_id)
            compressed.append(m)
        return compressed

    def chat_node(self, state: AgentState):
        """Standard chat response with Context Engineering v2.0."""
        system_instructions = state.get("context", "You are Gemma 4, Bus Transit Intelligence")
        messages = state["messages"]

        def _msg_text(msg) -> str:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif "text" in block:
                            text_parts.append(str(block.get("text", "")))
                    elif isinstance(block, str):
                        text_parts.append(block)
                return "\n".join(part for part in text_parts if part).strip()
            return str(content).strip()

        last_user_text = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_text = _msg_text(msg).lower()
                break

        files = state.get("files", []) or []
        target_file = state.get("target_file")
        intent = state.get("intent", "general_transit_agent")
        working_memory = state.get("working_memory", "")
        file_scope = json.dumps(files, indent=2) if files else "[]"

        heavy_intents = {"update_context", "vision_extraction", "edit_timetable", "remove_bus", "secure_bus"}
        greeting_like = bool(re.search(
            r"^(hi|hello|hey|good morning|good afternoon|good evening|নমস্কার|হ্যালো|hello again)\b",
            last_user_text
        ))
        capability_like = any(phrase in last_user_text for phrase in (
            "what can you do",
            "capabilities",
            "ability",
            "abilities",
            "features",
            "how can you help",
            "ki korte paro",
            "কি করতে পারো",
            "কি কি পারো",
            "তুমি কী করতে পারো",
            "what do you do"
        ))

        schema_block = system_instructions if (intent in heavy_intents or greeting_like or capability_like) else (
            "You are Gemma 4, the brain of Bus Transit Intelligence. Full transit schema loaded. Use tools for all operations."
        )

        if greeting_like or capability_like:
            schema_block = f"""{schema_block}

HIGH PRIORITY FOR THIS REPLY:
- In the first reply, explicitly mention these three capabilities:
  1. Finding buses and journey options between places
  2. Extracting messy bus/timetable/poster image data into structured trip data
  3. Querying and analyzing transit project files via @file references
- Do not give a generic assistant greeting that omits image extraction.
"""

        recap_lines = []
        for m in reversed(list(messages[-14:])):
            content = getattr(m, 'content', '')
            if not isinstance(content, str): content = str(content)
            try:
                data = json.loads(content)
                if isinstance(data, dict) and 'matched' in data:
                    bus = data['matched']
                    recap_lines.append(f"- {bus.get('bus_name')} ({bus.get('reg_no')}) from {data.get('file')}")
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        recap = "\n".join(recap_lines[:5]) if recap_lines else "None in recent window."
        wm_display = working_memory if working_memory else "No entities queried yet."
        current_time = datetime.now().strftime("%I:%M %p")

        hybrid_data = None
        for line in working_memory.split("\n"):
            if line.startswith("[HYBRID_DATA] "):
                hybrid_data = line[len("[HYBRID_DATA] "):].strip()
                break
        hybrid_block = ""
        if hybrid_data:
            hybrid_block = f"""
## Hybrid Intent Resolution (Follow-up)
The following bus data JSON was extracted from a previous image and is awaiting your action:
```json
{hybrid_data}
```
**ACTION GUIDE:**
- If the user says anything like "save", "add", "keep", "database", "store", "রাখুন", "সেভ" → call `save_to_file(file_path="@stage1", content=<the EXACT JSON above>)`.
- If the user says anything like "find", "bus", "route", "journey", "travel", "খুঁজুন", "যাত্রা", "গাড়ি" → call `find_transit_route` using their journey details from the conversation history.
- If the user says anything like "both", "and", "do both", "দুটোই", "save and find", "সব" → call BOTH `save_to_file` AND `find_transit_route`.
- If the user sends `[SYSTEM_COMMAND]: SAVE_BUS_DATA <json>` (button click), treat it as a save command.
"""

        system_msg = SystemMessage(content=f"""{schema_block}

## Working Memory (Cross-Turn Entity Recall)
{wm_display}

## Recent Tool Results (Entity Recap)
{recap}

## Transit Data Protocol
Current intent: {intent}
Resolved @file scope: {file_scope}
Primary target_file: {target_file or "None"}

Rules:
- Treat @file mentions as already resolved in the file scope above.
- For file summaries/details, call `summarize_transit_file` first.
- For bus-specific details, call `query_bus_record`.
- For questions like "second stoppage of bus A, 1st trip", call `get_trip_stop` with stop_index=2.
- For removal, call `remove_bus_from_file`. If the user clearly asked to remove/delete, use dry_run=False.
- For timetable edits, call `update_bus_timetable`. If the user clearly asked to edit/save, use dry_run=False.
- Never use `python_interpreter` to write files. Use dedicated write tools.
- Follow-up commands like "return full json", "show more", "edit that bus" refer to the entities in Working Memory above.
{hybrid_block}

## RAPTOR Journey Planner
- When the user asks about travel/journey/buses between places, call `find_transit_route(origin, destination)`.
- **Clarification Rule**: If the user provides a destination and time (e.g., "reach Bardhaman before 3pm") but omits the starting location, you MUST ask "From where would you like to start your journey?" before calling any tools.
- **Time Interpretation (Think Naturally)**:
  - "I want to reach before 3pm" → The user wants to ARRIVE by that time. Pass `arrive_by="3:00 PM"`.
  - "after 10am" or "next bus from now" → The user wants to DEPART after that time. Pass `depart_after`.
  - A time range like "11am to 12pm ki bus ache" → The user wants to know what buses DEPART in that window. Use `depart_after="11:00 AM"`. Do NOT set arrive_by — the range describes when they are available to catch a bus, not when they must arrive.
  - "Sbstc/local" or similar → The user is expressing a preference (e.g., SBSTC preferred, any local bus also acceptable). This is NOT a filter — show all available buses and let them choose.
  - For "most recent bus" or "next bus", use the current time as depart_after (current time: {current_time}).
- **Graceful Fallback**: If `find_transit_route` returns `time_note` in the response, it means no exact time-window match was found but the closest alternatives are shown. Naturally explain this to the user (e.g., "I couldn't find a bus departing exactly at 11 AM, but here are the closest options:").
- If a location is not found, use `list_transit_stops` to search for it.
- **IMPORTANT**: When `find_transit_route` returns SUCCESS, you MUST output the raw JSON string exactly as returned inside a markdown block labeled `raptor-cards` (i.e. ````raptor-cards\n<json>\n````). DO NOT narrate or summarize the individual bus options in text — the UI renders the interactive cards automatically.

## Personality & Specialized Skills
- **NO EMOJIS**: Never use emojis in your responses. Keep formatting professional and clean, using plain text or short labels only when genuinely helpful.
- **Language Mirroring**: Match the user's language. If they write in Bengali/Bangla ("আমি পুরুলিয়া যেতে চাই"), respond in Bengali. If they write in Romanized Bengali, respond in that style.
- **Human Warmth**: After showing transit cards, add a warm message (e.g., "শুভ যাত্রা!" / "Happy journey!") and offer further help (e.g., "আর কিছু জানতে চাইলে বলুন!").
- **CRITICAL FORMATTING**:
  - Journey results (RAPTOR): ````raptor-cards\n<json>\n````
  - Data suggestions (Discovery Overview): ````suggestion-card\n<json>\n````
  - DO NOT output raw JSON outside these blocks. Use your natural response to explain what you're doing.

Task: Explain your THOUGHT process then provide your RESPONSE. Format exactly as:
THOUGHT: [short operational status]
RESPONSE: [message]""")

        compressed_messages = self._compress_old_tool_results(list(messages))
        trimmed_messages = self._trim_messages(compressed_messages)

        full_prompt = [system_msg] + trimmed_messages
        response = None
        last_err = None
        for attempt in range(2):
            try:
                response = self.llm.invoke(full_prompt)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(0.8 * (attempt + 1))

        if response is None:
            try:
                slim_messages = list(trimmed_messages)[-8:]
                slim_prompt = [system_msg] + slim_messages
                response = self.llm.invoke(slim_prompt)
                last_err = None
            except Exception as e:
                last_err = e

        if response is None:
            response = AIMessage(
                content=(
                    "THOUGHT: Upstream model error (recoverable).\n"
                    "RESPONSE: [ALERT] The model endpoint returned an internal error while processing this request. "
                    "Please retry the same action once. If it repeats, try a shorter message or retry after 30 seconds."
                )
            )
        
        content = response.content
        thought = state.get("thought", "Generating response...")
        
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        thought = block.get("thought", thought)
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif "text" in block:
                        text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)
            reply_text = " ".join(text_parts)
        else:
            reply_text = str(content)
        reply_text = _strip_reply_postfixes(reply_text)

        last_tool_msg = next((m for m in reversed(messages) if isinstance(m, AIMessage) and m.tool_calls), None)
        if last_tool_msg:
            tool_name = last_tool_msg.tool_calls[0]['name']
            if tool_name == "smart_grep":
                thought = f"Data Integrity: Scanning Master Registry (BusData_Phase_1.json) for reg_no uniqueness..."
            elif tool_name == "smart_registry_grep":
                thought = f"Data Integrity: Scanning secured + master registry for duplicates..."
            elif tool_name == "save_to_file":
                if "Pipeline Not updated:" in (reply_text or ""):
                    thought = f"Data Integrity: Duplicate detected; skipping persistence to prevent duplicates..."
                else:
                    thought = f"Pipeline Dispatch: Persisting validated JSON to Stage_1_data.json for solver pickup..."
            elif tool_name == "python_interpreter":
                thought = f"Data Audit: Executing Python-based schema validation and entity resolution..."
            else:
                thought = f"Executing {tool_name} to fulfill transit request..."
        else:
            thought = "Processing request and aligning with Bus Transit Intelligence v5.0.0 architecture."

        return {
            "messages": [response],
            "thought": thought
        }

gemma_graph = GemmaGraph()
