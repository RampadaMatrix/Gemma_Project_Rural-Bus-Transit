import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import threading

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
config.load_env()
API_TOKEN = config.get_api_token()

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app)


def _is_loopback_request() -> bool:
    host = (request.remote_addr or "").strip()
    return host in {"127.0.0.1", "::1", "localhost"}


@app.before_request
def verify_token():
    if _is_loopback_request():
        return
    # Allow static files and health checks (if any)
    if request.path in ["/route_verification_map.html", "/"] or request.path.startswith("/Background/"):
        return
    if request.method == "OPTIONS":
        return
    token = request.headers.get("X-API-Token")
    if not API_TOKEN or token != API_TOKEN:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

from Plotting_Polyline_HITL_Algo import API_KEY, PuruliaTransitRouter
from Analyses.analysis_backend import (
    analysis_from_movement,
    compute_trip_fallback_kinematics,
    enrich_bus_kinematics,
    enrich_movement_kinematics,
    movement_has_embedded_kinematics,
)
from Analyses.proximity_backend import prewarm_proximity_engine, resolve_secure_proximity

from Raptor_data.raptor_solver import RaptorRouter

INPUT_FILE = config.HITL_INPUT
OUTPUT_FILE = config.HITL_OUTPUT
TT_OUTPUT_FILE = config.HITL_TT_OUTPUT
SECURED_FILE = config.HITL_SECURED

MAP_FILE = config.MAP_FILE
BACKUP_DIR = PROJECT_ROOT / "BackUP"

router = PuruliaTransitRouter(API_KEY)



def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except OSError:
        pass

# Initialize RAPTOR Engine
# Initialize RAPTOR Engine Lazily
raptor_data_dir = BASE_DIR / 'Raptor_data'
raptor_router = None

def get_raptor_router():
    global raptor_router
    if raptor_router is None:
        safe_print("[RAPTOR] Initializing engine (first use)...", flush=True)
        try:
            raptor_router = RaptorRouter(str(raptor_data_dir))
            safe_print("[RAPTOR] Ready", flush=True)
        except Exception as e:
            safe_print(f"[RAPTOR ERROR] Failed to init: {e}", flush=True)
    return raptor_router
_EMBEDDED_BACKFILL_DONE = False
_TT_EMBEDDED_BACKFILL_DONE = False
_TERMINAL_TIME_BACKFILL_DONE = False
_SECURE_PROX_DATA_CACHE = {"sig": None, "payload": None}
_RAPTOR_METADATA_CACHE = {"sig": None, "payload": None}

_GET_CACHE_DATA_CACHE = {"sig": None, "payload": None, "built_at": None}
_GET_CACHE_DATA_LOCK = threading.Lock()

PRECOMPUTED_CACHE_FILE = BASE_DIR / "precomputed_cache.json"

WARMUP_STATE = {
    "ready": False,
    "started": False,
    "phase": "idle",
    "progress": 0,
    "error": None
}


def load_json(path: Path, default):
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def flip_polyline(encoded_polyline):
    """Mathematically flips an encoded polyline string."""
    if not encoded_polyline:
        return ""
    try:
        points = router._decode_polyline(encoded_polyline)
        reversed_points = points[::-1]
        return router._encode_polyline(reversed_points)
    except Exception as e:
        print(f"[REVERSAL ERROR] Failed to flip polyline: {e}")
        return encoded_polyline


def try_load_json(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def atomic_write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except (PermissionError, OSError):
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def find_latest_backup(target_file: Path):
    pattern = f"{target_file.stem}.backup_*{target_file.suffix}"
    candidates = sorted(
        [p for p in BACKUP_DIR.glob(pattern) if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def backup_json_file(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = BACKUP_DIR / f"{path.stem}.backup_{ts}{path.suffix}"
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    
    # --- ROLLING BACKUP (Keep last 10) ---
    try:
        pattern = f"{path.stem}.backup_*{path.suffix}"
        backups = sorted(list(BACKUP_DIR.glob(pattern)), key=lambda x: x.stat().st_mtime)
        while len(backups) > 10:
            oldest = backups.pop(0)
            oldest.unlink()
    except Exception as e:
        print(f"[BACKUP CLEANUP ERROR] {e}")
    # -------------------------------------
    
    return backup


def is_bus_dataset(payload):
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("metadata"), dict)
        and isinstance(payload.get("buses"), list)
    )


def is_secure_dataset(payload):
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("buses"), list)
        and all(isinstance(bus, dict) for bus in (payload.get("buses") or []))
        and (
            "locked_ids" not in payload
            or isinstance(payload.get("locked_ids"), list)
        )
        and (
            "metadata" not in payload
            or isinstance(payload.get("metadata"), dict)
        )
    )





def restore_from_latest_backup(path: Path, validator):
    if not BACKUP_DIR.exists():
        return None
    latest_backup = find_latest_backup(path)
    if latest_backup is None:
        return None
    restored = try_load_json(latest_backup)
    if not validator(restored):
        return None
    atomic_write_json(path, restored)
    return restored

_BUS_DATASET_CACHE = {}


def load_bus_dataset(path: Path):
    path = Path(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0

    if path in _BUS_DATASET_CACHE:
        cached_mtime, cached_payload = _BUS_DATASET_CACHE[path]
        if cached_mtime == mtime and mtime != 0:
            return cached_payload

    payload = try_load_json(path)
    if is_bus_dataset(payload):
        _BUS_DATASET_CACHE[path] = (mtime, payload)
        return payload
    restored = restore_from_latest_backup(path, is_bus_dataset)
    if is_bus_dataset(restored):
        _BUS_DATASET_CACHE[path] = (mtime, restored)
        return restored
    return {"metadata": {}, "buses": []}


def ensure_embedded_kinematics_backfill():
    global _EMBEDDED_BACKFILL_DONE
    if _EMBEDDED_BACKFILL_DONE:
        return {"checked": True, "updated": 0}

    output_data = load_bus_dataset(OUTPUT_FILE)
    changed = False
    updated_movements = 0
    for bus in output_data.get("buses", []) or []:
        needs_backfill = any(not movement_has_embedded_kinematics(mv) for mv in (bus.get("movements", []) or []))
        if not needs_backfill:
            continue
        updated_movements += enrich_bus_kinematics(bus)
        changed = True

    if changed:
        meta = output_data.get("metadata") if isinstance(output_data.get("metadata"), dict) else {}
        meta["analysis_embedded_at"] = datetime.now().isoformat(timespec="seconds")
        meta["analysis_engine"] = "hitl_embedded_kinematics_v1 + speed_physics_v1"
        output_data["metadata"] = meta
        backup_json_file(OUTPUT_FILE)
        atomic_write_json(OUTPUT_FILE, output_data)

    _EMBEDDED_BACKFILL_DONE = True
    return {"checked": True, "updated": updated_movements}


def _overlay_tt_times(base_bus, tt_bus):
    if not isinstance(base_bus, dict) or not isinstance(tt_bus, dict):
        return 0
    updates = 0
    tt_mvs = tt_bus.get("movements") or []
    base_mvs = base_bus.get("movements") or []

    def _tt_lookup(mv):
        lookup = {}
        for idx, s in enumerate((mv.get("stops") or [])):
            if not isinstance(s, dict):
                continue
            nm = normalize_stop_name(s.get("name"))
            if nm and nm not in lookup:
                lookup[nm] = s
            lookup[f"@idx:{idx}"] = s
        return lookup

    for i, bmv in enumerate(base_mvs):
        if not isinstance(bmv, dict):
            continue
        t_mv = None
        if i < len(tt_mvs) and isinstance(tt_mvs[i], dict):
            t_mv = tt_mvs[i]
        if t_mv is None:
            t_mv = next(
                (
                    x for x in tt_mvs
                    if isinstance(x, dict)
                    and str(x.get("trip_id") or "").strip() == str(bmv.get("trip_id") or "").strip()
                    and str(x.get("direction") or "").strip().lower() == str(bmv.get("direction") or "").strip().lower()
                ),
                None,
            )
        if t_mv is None:
            continue
        lookup = _tt_lookup(t_mv)
        for idx, s in enumerate((bmv.get("stops") or [])):
            if not isinstance(s, dict):
                continue
            key = normalize_stop_name(s.get("name"))
            src = lookup.get(key) if key else None
            if src is None:
                src = lookup.get(f"@idx:{idx}")
            if src is None:
                continue
            s["arrival_time"] = src.get("arrival_time")
            s["departure_time"] = src.get("departure_time")
            updates += 1
        route = bmv.get("route") if isinstance(bmv.get("route"), dict) else {}
        for n in route.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            src = lookup.get(normalize_stop_name(n.get("name")))
            if src is None:
                continue
            n["arrival_time"] = src.get("arrival_time")
            n["departure_time"] = src.get("departure_time")
            updates += 1
    return updates


def ensure_tt_embedded_backfill():
    global _TT_EMBEDDED_BACKFILL_DONE
    if _TT_EMBEDDED_BACKFILL_DONE:
        return {"checked": True, "updated": 0}

    output_data = load_bus_dataset(OUTPUT_FILE)
    tt_data = load_bus_dataset(TT_OUTPUT_FILE)
    changed = False
    updated_movements = 0
    for tt_bus in tt_data.get("buses", []) or []:
        if not isinstance(tt_bus, dict):
            continue
        _, out_bus, _ = find_bus(output_data, tt_bus.get("bus_name"), tt_bus.get("reg_no"))
        if out_bus is not None:
            base_bus = json.loads(json.dumps(out_bus))
            _overlay_tt_times(base_bus, tt_bus)
            tt_bus.clear()
            tt_bus.update(base_bus)
            changed = True
        if any(not movement_has_embedded_kinematics(mv) for mv in (tt_bus.get("movements") or [])):
            updated_movements += enrich_bus_kinematics(tt_bus)
            changed = True

    if changed:
        meta = tt_data.get("metadata") if isinstance(tt_data.get("metadata"), dict) else {}
        meta["analysis_embedded_at"] = datetime.now().isoformat(timespec="seconds")
        meta["analysis_engine"] = "hitl_embedded_kinematics_v1 + speed_physics_v1"
        tt_data["metadata"] = meta
        backup_json_file(TT_OUTPUT_FILE)
        atomic_write_json(TT_OUTPUT_FILE, tt_data)

    _TT_EMBEDDED_BACKFILL_DONE = True
    return {"checked": True, "updated": updated_movements}


def _has_nonempty_time(value):
    return value is not None and str(value).strip() != ""


def normalize_terminal_times_in_movement(movement):
    if not isinstance(movement, dict):
        return 0
    changed = 0

    stops = movement.get("stops") or []
    if not isinstance(stops, list):
        stops = []

    first_key = ""
    last_key = ""
    if len(stops) > 0 and isinstance(stops[0], dict):
        first_key = normalize_stop_name(stops[0].get("name"))
        if _has_nonempty_time(stops[0].get("arrival_time")):
            stops[0]["arrival_time"] = None
            changed += 1
    if len(stops) > 1 and isinstance(stops[-1], dict):
        last_key = normalize_stop_name(stops[-1].get("name"))
        if _has_nonempty_time(stops[-1].get("departure_time")):
            stops[-1]["departure_time"] = None
            changed += 1

    route = movement.get("route")
    if isinstance(route, dict):
        nodes = route.get("nodes") or []
        if isinstance(nodes, list) and nodes:
            for idx, node in enumerate(nodes):
                if not isinstance(node, dict):
                    continue
                node_key = normalize_stop_name(node.get("name"))
                is_first = (first_key and node_key == first_key) or (not first_key and idx == 0)
                is_last = (last_key and node_key == last_key) or (not last_key and idx == len(nodes) - 1)
                if is_first and _has_nonempty_time(node.get("arrival_time")):
                    node["arrival_time"] = None
                    changed += 1
                if is_last and _has_nonempty_time(node.get("departure_time")):
                    node["departure_time"] = None
                    changed += 1

    return changed


def normalize_terminal_times_in_dataset(dataset):
    total = 0
    if not isinstance(dataset, dict):
        return total
    for bus in dataset.get("buses", []) or []:
        if not isinstance(bus, dict):
            continue
        for movement in bus.get("movements", []) or []:
            total += normalize_terminal_times_in_movement(movement)
    return total


def _stamp_terminal_time_meta(dataset):
    if not isinstance(dataset, dict):
        return
    meta = dataset.get("metadata") if isinstance(dataset.get("metadata"), dict) else {}
    meta["terminal_time_normalized_at"] = datetime.now().isoformat(timespec="seconds")
    meta["terminal_time_rule"] = "origin_arrival_null_destination_departure_null"
    dataset["metadata"] = meta


def ensure_terminal_time_backfill():
    global _TERMINAL_TIME_BACKFILL_DONE
    if _TERMINAL_TIME_BACKFILL_DONE:
        return {"checked": True, "updated": 0}

    total_updates = 0
    for path in (INPUT_FILE, OUTPUT_FILE, TT_OUTPUT_FILE):
        data = load_bus_dataset(path)
        updates = normalize_terminal_times_in_dataset(data)
        if updates > 0:
            _stamp_terminal_time_meta(data)
            backup_json_file(path)
            atomic_write_json(path, data)
            total_updates += updates

    _TERMINAL_TIME_BACKFILL_DONE = True
    return {"checked": True, "updated": total_updates}


def normalize_bus_identity(bus_name=None, reg_no=None):
    raw_name = str(bus_name or "").strip()
    raw_reg = str(reg_no or "").strip().upper()
    if raw_name.endswith(")") and " (" in raw_name:
        maybe_name, maybe_reg = raw_name.rsplit(" (", 1)
        maybe_reg = maybe_reg[:-1].strip().upper()
        if maybe_name.strip():
            raw_name = maybe_name.strip()
        if maybe_reg:
            raw_reg = raw_reg or maybe_reg
    clean_name = raw_name.strip()
    fleet_id = f"{clean_name} ({raw_reg})" if raw_reg else clean_name
    return clean_name, raw_reg, fleet_id


def normalize_stop_name(name):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split())


def movement_route(movement):
    route = movement.get("route")
    if isinstance(route, dict):
        return route
    return {"polyline": "", "nodes": []}


def has_route_geometry(route):
    if not isinstance(route, dict):
        return False
    if route.get("polyline"):
        return True
    nodes = route.get("nodes") or []
    return isinstance(nodes, list) and len(nodes) >= 2


def enrich_stops_from_nodes(movement):
    if not isinstance(movement, dict):
        return
    stops = movement.get("stops") or []
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    nodes = route.get("nodes") or []
    if not isinstance(stops, list) or not isinstance(nodes, list) or not stops or not nodes:
        return

    by_name = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        key = normalize_stop_name(n.get("name"))
        lat = n.get("lat")
        lng = n.get("lng")
        if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            by_name[key] = (float(lat), float(lng))

    if not by_name:
        return

    for s in stops:
        if not isinstance(s, dict):
            continue
        key = normalize_stop_name(s.get("name"))
        if key not in by_name:
            continue
        lat = s.get("lat")
        lng = s.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            s["lat"], s["lng"] = by_name[key]


def propagate_bus_stop_coordinates(bus):
    if not isinstance(bus, dict):
        return
    movements = bus.get("movements", []) or []
    coord_map = {}
    for mv in movements:
        route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        for n in route.get("nodes", []) or []:
            key = normalize_stop_name(n.get("name"))
            lat = n.get("lat")
            lng = n.get("lng")
            if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                coord_map[key] = (float(lat), float(lng))
        for s in mv.get("stops", []) or []:
            key = normalize_stop_name(s.get("name"))
            lat = s.get("lat")
            lng = s.get("lng")
            if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                coord_map[key] = (float(lat), float(lng))
    if not coord_map:
        return
    for mv in movements:
        for s in mv.get("stops", []) or []:
            key = normalize_stop_name(s.get("name"))
            if key in coord_map:
                lat = s.get("lat")
                lng = s.get("lng")
                if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                    s["lat"], s["lng"] = coord_map[key]


def build_route_entry(bus, movement):
    audit = movement.get("audit") if isinstance(movement.get("audit"), dict) else {}
    violations = audit.get("violations") if isinstance(audit.get("violations"), list) else []
    speed_anomaly_count = len(violations)
    if speed_anomaly_count <= 0:
        segs = movement.get("segments") or []
        if isinstance(segs, list):
            speed_anomaly_count = sum(1 for s in segs if isinstance(s, dict) and bool(s.get("is_outlier")))

    bus_name = bus.get("bus_name")
    reg_no = bus.get("reg_no")
    _, _, display_name = normalize_bus_identity(bus_name, reg_no)
    route = movement_route(movement)
    return {
        "bus_name": bus_name,
        "display_name": display_name,
        "reg_no": reg_no,
        "trip_id": movement.get("trip_id"),
        "direction": movement.get("direction"),
        "origin": movement.get("origin"),
        "destination": movement.get("destination"),
        "corridor_signature": movement.get("corridor_signature"),
        "polyline": route.get("polyline", ""),
        "nodes": route.get("nodes") or [],
        "markers": movement.get("stops") or [],
        "segments": movement.get("segments") or [],
        "audit": audit,
        "average_speed_kmph": movement.get("average_speed_kmph", movement.get("filtered_avg_kmph")),
        "max_speed_kmph": movement.get("max_speed_kmph"),
        "speed_meta": movement.get("speed_meta") or {},
        "speed_anomaly_count": speed_anomaly_count,
        "speed_has_anomaly": speed_anomaly_count > 0,

        "is_master": route.get("master_geometry", True),
        "redundant_of": route.get("redundant_of")
    }


def dataset_to_verified_routes(dataset):
    routes = []
    for bus in dataset.get("buses", []) or []:
        for movement in bus.get("movements", []) or []:
            routes.append(build_route_entry(bus, movement))
    return routes


def buses_to_hitl_storage(routes):
    grouped = defaultdict(list)
    for r in routes:
        grouped[r.get("display_name") or r.get("bus_name")].append(r)
    storage = {}
    for bid, trips in grouped.items():
        storage[bid] = {
            "trips": trips,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "hitl_verified": True,
        }
    return storage


def normalize_locked_id(bus_name=None, reg_no=None):
    _, _, fleet_id = normalize_bus_identity(bus_name, reg_no)
    return fleet_id


def _route_dict(value):
    if isinstance(value, dict):
        return {
            "polyline": value.get("polyline") or "",
            "nodes": value.get("nodes") or [],
        }
    return {"polyline": "", "nodes": []}


def _is_current_output_contract(input_data, output_data):
    if not isinstance(output_data, dict):
        return False
    output_meta = output_data.get("metadata") if isinstance(output_data.get("metadata"), dict) else {}
    if not output_meta.get("generated_at"):
        return False

    source_file = output_meta.get("source_file")
    if not source_file:
        return False

    def _norm_path(p):
        try:
            return os.path.normcase(os.path.normpath(str(Path(p).resolve())))
        except Exception:
            return os.path.normcase(os.path.normpath(str(p)))

    if _norm_path(source_file) != _norm_path(INPUT_FILE):
        return False
    # Treat output as authoritative for any HITL-produced sync/recompute source.
    source = str(output_meta.get("source") or "").strip()
    if not source.startswith("HITL"):
        return False
    return isinstance(output_data.get("buses"), list) and len(output_data.get("buses")) > 0


def load_output_for_write(input_data):
    existing = load_bus_dataset(OUTPUT_FILE)
    if _is_current_output_contract(input_data, existing):
        return existing
    # Safety fallback: preserve any existing output bus payload even when metadata
    # contract is stale/mismatched, so partial sync cannot accidentally truncate output.
    if isinstance(existing, dict) and isinstance(existing.get("buses"), list) and existing.get("buses"):
        metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        metadata.setdefault("source_file", str(INPUT_FILE))
        metadata.setdefault("source", "HITL legacy output")
        existing["metadata"] = metadata
        return existing
    return {"metadata": {}, "buses": []}


def load_primary_dataset():
    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    if _is_current_output_contract(input_data, output_data):
        return output_data
    return input_data


def load_primary_dataset_with_source():
    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    if _is_current_output_contract(input_data, output_data):
        return output_data, "output"
    return input_data, "input"


def build_merged_dataset(input_data, output_data):
    def merge_movement(in_mv, out_mv):
        in_mv = in_mv if isinstance(in_mv, dict) else {}
        out_mv = out_mv if isinstance(out_mv, dict) else {}

        # Output is authoritative when available; input only backfills missing values.
        merged_mv = dict(out_mv) if out_mv else dict(in_mv)
        for k, v in in_mv.items():
            if k in merged_mv:
                cur = merged_mv.get(k)
                if cur is None:
                    merged_mv[k] = v
                elif isinstance(cur, str) and not cur.strip():
                    merged_mv[k] = v
                elif isinstance(cur, list) and len(cur) == 0 and isinstance(v, list) and len(v) > 0:
                    merged_mv[k] = v
                continue
            merged_mv[k] = v

        in_route = in_mv.get("route") if isinstance(in_mv.get("route"), dict) else {}
        out_route = out_mv.get("route") if isinstance(out_mv.get("route"), dict) else {}
        # Output route object is authoritative for HITL-synced trips.
        if isinstance(out_mv.get("route"), dict):
            merged_mv["route"] = out_route if out_route else {"polyline": "", "nodes": []}
        elif in_route:
            merged_mv["route"] = in_route
        else:
            merged_mv["route"] = {"polyline": "", "nodes": []}
        return merged_mv

    merged = {"metadata": dict(input_data.get("metadata") or {}), "buses": []}
    for bus in input_data.get("buses", []) or []:
        merged["buses"].append(dict(bus))

    for out_bus in output_data.get("buses", []) or []:
        out_idx, merged_bus, _ = find_bus(merged, out_bus.get("bus_name"), out_bus.get("reg_no"))
        if merged_bus is None:
            # Ignore output-only buses; input file is canonical bus roster.
            continue

        in_bus = merged_bus if isinstance(merged_bus, dict) else {}
        out_bus_safe = out_bus if isinstance(out_bus, dict) else {}
        # Bus-level fields: prefer output; backfill from input when absent.
        merged_bus_new = dict(out_bus_safe)
        for k, v in in_bus.items():
            if k in merged_bus_new:
                cur = merged_bus_new.get(k)
                if cur is None:
                    merged_bus_new[k] = v
                elif isinstance(cur, str) and not cur.strip():
                    merged_bus_new[k] = v
                continue
            merged_bus_new[k] = v

        out_movements = out_bus_safe.get("movements", []) or []
        in_movements = in_bus.get("movements", []) or []
        # If output has movements for this bus, use exactly that movement set for UI.
        # Otherwise fall back to input movement set.
        max_len = len(out_movements) if len(out_movements) > 0 else len(in_movements)
        new_movements = []
        for i in range(max_len):
            base_mv = in_movements[i] if i < len(in_movements) and isinstance(in_movements[i], dict) else {}
            out_mv = out_movements[i] if i < len(out_movements) and isinstance(out_movements[i], dict) else {}
            merged_mv = merge_movement(base_mv, out_mv)
            new_movements.append(merged_mv)
        merged_bus_new["movements"] = new_movements
        merged["buses"][out_idx] = merged_bus_new

    for bus in merged.get("buses", []) or []:
        harmonize_bus_stop_coordinates(bus)

    return merged


def harmonize_bus_stop_coordinates(bus):
    if not isinstance(bus, dict):
        return

    def norm(name):
        return normalize_stop_name(name)

    latest_coords = {}
    for mv in bus.get("movements", []) or []:
        for s in mv.get("stops", []) or []:
            key = norm(s.get("name"))
            lat = s.get("lat")
            lng = s.get("lng")
            if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                latest_coords[key] = (float(lat), float(lng))

    if not latest_coords:
        return

    for mv in bus.get("movements", []) or []:
        rt = mv.get("route")
        if not isinstance(rt, dict):
            continue
        nodes = rt.get("nodes") or []
        if not isinstance(nodes, list):
            continue
        for n in nodes:
            if not isinstance(n, dict):
                continue
            key = norm(n.get("name"))
            if key in latest_coords:
                lat, lng = latest_coords[key]
                n["lat"] = lat
                n["lng"] = lng


def load_route_view_dataset():
    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    if _is_current_output_contract(input_data, output_data):
        merged = build_merged_dataset(input_data, output_data)
        return merged, output_data, "merged"
    return input_data, {"metadata": {}, "buses": []}, "input"


_SECURED_DATA_CACHE = {"mtime": 0, "payload": None}


def load_secured_data():
    try:
        mtime = os.path.getmtime(SECURED_FILE)
    except OSError:
        mtime = 0

    if _SECURED_DATA_CACHE["mtime"] == mtime and mtime != 0:
        return _SECURED_DATA_CACHE["payload"]

    secured = try_load_json(SECURED_FILE)
    if not is_secure_dataset(secured):
        secured = restore_from_latest_backup(SECURED_FILE, is_secure_dataset)
    if not is_secure_dataset(secured):
        secured = {"metadata": {}, "locked_ids": [], "buses": []}
    dirty = False

    # Canonicalize and de-duplicate snapshot buses by normalized fleet id.
    normalized_buses = []
    seen_bus_ids = set()
    for bus in secured.get("buses", []) or []:
        if not isinstance(bus, dict):
            dirty = True
            continue
        fleet_id = normalize_locked_id(bus.get("bus_name"), bus.get("reg_no"))
        if not fleet_id or fleet_id in seen_bus_ids:
            dirty = True
            continue
        seen_bus_ids.add(fleet_id)
        normalized_buses.append(bus)
    if normalized_buses != (secured.get("buses") or []):
        secured["buses"] = normalized_buses
        dirty = True

    # Keep locked_ids synchronized with snapshot buses and normalized for robust lookup.
    normalized_locked = set()
    for lid in secured.get("locked_ids", []) or []:
        lid = str(lid or "").strip()
        if not lid:
            dirty = True
            continue
        name = lid
        reg = ""
        if lid.endswith(")") and " (" in lid:
            maybe_name, maybe_reg = lid.rsplit(" (", 1)
            maybe_reg = maybe_reg[:-1].strip()
            if maybe_name.strip():
                name = maybe_name.strip()
            reg = maybe_reg
        normalized_locked.add(normalize_locked_id(name, reg))
    for bus in secured.get("buses", []) or []:
        normalized_locked.add(normalize_locked_id(bus.get("bus_name"), bus.get("reg_no")))
    normalized_locked = sorted(normalized_locked)
    if normalized_locked != (secured.get("locked_ids") or []):
        secured["locked_ids"] = normalized_locked
        dirty = True
    # Repair secured polylines from current output when secure geometry is stale/mismatched.
    repaired_from_output = 0
    try:
        out_for_repair = load_bus_dataset(OUTPUT_FILE)
        repaired_from_output = _repair_secured_snapshot_polylines(secured, out_for_repair)
    except Exception:
        repaired_from_output = 0
    if repaired_from_output > 0:
        dirty = True

    # Canonicalize secure movement geometry orientation (nodes/polyline vs stop direction).
    normalized_mvs = _normalize_secure_snapshot_geometry(secured)
    if normalized_mvs > 0:
        dirty = True

    if dirty:
        secured_meta = secured.get("metadata") if isinstance(secured.get("metadata"), dict) else {}
        secured_meta.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        secured_meta["lock_count"] = len(secured.get("locked_ids") or [])
        if repaired_from_output > 0:
            secured_meta["secure_polyline_repaired_at"] = datetime.now().isoformat(timespec="seconds")
            secured_meta["secure_polyline_repaired_count"] = int(repaired_from_output)
        if normalized_mvs > 0:
            secured_meta["secure_geometry_normalized_at"] = datetime.now().isoformat(timespec="seconds")
            secured_meta["secure_geometry_normalized_movements"] = int(normalized_mvs)
        secured["metadata"] = secured_meta
        backup_json_file(SECURED_FILE)
        atomic_write_json(SECURED_FILE, secured)
        # Update mtime after write to keep cache consistent
        try:
            _SECURED_DATA_CACHE["mtime"] = os.path.getmtime(SECURED_FILE)
        except OSError:
            _SECURED_DATA_CACHE["mtime"] = 0
    else:
        _SECURED_DATA_CACHE["mtime"] = mtime

    _SECURED_DATA_CACHE["payload"] = secured
    return secured



def _secure_file_signature(path: Path):
    try:
        st = path.stat()
        return f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
    except Exception:
        return "missing"


def _get_cache_signature():
    parts = [
        _secure_file_signature(INPUT_FILE),
        _secure_file_signature(OUTPUT_FILE),
        _secure_file_signature(TT_OUTPUT_FILE),
        _secure_file_signature(SECURED_FILE),
    ]
    # Include backfill toggles because they can mutate files on first call.
    parts.append(f"bf:{int(bool(_EMBEDDED_BACKFILL_DONE))}{int(bool(_TT_EMBEDDED_BACKFILL_DONE))}{int(bool(_TERMINAL_TIME_BACKFILL_DONE))}")
    return "|".join(parts)


def load_raptor_metadata_payload():
    """Lightweight map/search metadata without initializing the RAPTOR solver."""
    bundle_file = raptor_data_dir / "raptor_bundle.json"
    village_file = BASE_DIR / "Villages_data" / "Villages_data_identified.json"
    sig = f"{_secure_file_signature(bundle_file)}|{_secure_file_signature(village_file)}"
    cached = _RAPTOR_METADATA_CACHE.get("payload")
    if _RAPTOR_METADATA_CACHE.get("sig") == sig and cached:
        return cached

    villages = []
    if village_file.exists():
        with open(village_file, "r", encoding="utf-8") as f:
            villages = json.load(f)

    stops = []
    if bundle_file.exists():
        with open(bundle_file, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        stops = ((bundle.get("data") or {}).get("stops") or [])

    index = []
    for v in villages:
        index.append({
            "name": v.get("name"),
            "unique_label": v.get("unique_label"),
            "type": "VILLAGE",
            "lat": v.get("lat"),
            "lng": v.get("lon"),
            "lon": v.get("lon"),
            "block": v.get("block"),
            "population_2011": v.get("population_2011"),
            "nearby": v.get("nearby_villages"),
            "corridor": v.get("nearest_corridor"),
            "stop": v.get("nearest_stop")
        })

    for s in stops:
        stop_kind = s.get("type")
        index.append({
            "name": s.get("name"),
            "unique_label": s.get("name"),
            "type": "STOP",
            "stop_kind": stop_kind,
            "stop_id": s.get("stop_id"),
            "lat": s.get("lat"),
            "lng": s.get("lng"),
            "corridor": s.get("corridor"),
            "is_junction": bool(s.get("is_junction")),
            "distance_to_village": s.get("distance_to_village"),
            "subtitle": "Main Bus Stop" if stop_kind == "CURATED" else "RAPTOR Stop"
        })

    payload = {
        "status": "success",
        "results": index,
        "counts": {
            "stops": len(stops),
            "villages": len(villages),
            "total": len(index)
        },
        "source": "raptor_metadata_cache"
    }
    _RAPTOR_METADATA_CACHE["sig"] = sig
    _RAPTOR_METADATA_CACHE["payload"] = payload
    return payload


def load_secured_data_for_proximity():
    sig = _secure_file_signature(SECURED_FILE)
    cached_sig = _SECURE_PROX_DATA_CACHE.get("sig")
    cached_payload = _SECURE_PROX_DATA_CACHE.get("payload")
    if cached_sig == sig and is_secure_dataset(cached_payload):
        return cached_payload, sig
    payload = load_secured_data()
    prewarm_proximity_engine(payload, cache_key=sig)
    _SECURE_PROX_DATA_CACHE["sig"] = sig
    _SECURE_PROX_DATA_CACHE["payload"] = payload
    return payload, sig
def build_secure_registry(secured_data):
    registry = {}
    for bus in secured_data.get("buses", []) or []:
        bus_name = bus.get("bus_name")
        reg_no = bus.get("reg_no")
        _, _, fleet_id = normalize_bus_identity(bus_name, reg_no)
        registry[fleet_id] = {
            "bus_id": fleet_id,
            "bus_name": bus_name,
            "reg_no": reg_no,
            "locked": True,
            "secured_at": secured_data.get("metadata", {}).get("updated_at"),
        }
    # Ensure registry also reflects lock ids even if snapshot payload is absent.
    for lid in secured_data.get("locked_ids", []) or []:
        lid = str(lid or "").strip()
        if not lid or lid in registry:
            continue
        bus_name = lid
        reg_no = ""
        if lid.endswith(")") and " (" in lid:
            maybe_name, maybe_reg = lid.rsplit(" (", 1)
            maybe_reg = maybe_reg[:-1].strip()
            if maybe_name.strip():
                bus_name = maybe_name.strip()
            reg_no = maybe_reg
        registry[lid] = {
            "bus_id": lid,
            "bus_name": bus_name,
            "reg_no": reg_no,
            "locked": True,
            "secured_at": secured_data.get("metadata", {}).get("updated_at"),
        }
    return registry


def is_locked_bus(bus_name, reg_no, locked_ids):
    return normalize_locked_id(bus_name, reg_no) in locked_ids


def upsert_secure_snapshot(secured_data, input_data, output_data, bus_idx):
    if not isinstance(secured_data, dict):
        secured_data = {"metadata": {}, "locked_ids": [], "buses": []}
    bus = (input_data.get("buses") or [])[bus_idx]
    fleet_id = normalize_locked_id(bus.get("bus_name"), bus.get("reg_no"))
    snapshot = secure_bus_snapshot(input_data, output_data, bus_idx)
    secure_buses = [b for b in (secured_data.get("buses") or []) if normalize_locked_id(b.get("bus_name"), b.get("reg_no")) != fleet_id]
    secure_buses.append(snapshot)
    locked_ids = set(secured_data.get("locked_ids") or [])
    locked_ids.add(fleet_id)
    secured_data["locked_ids"] = sorted(locked_ids)
    secured_data["buses"] = secure_buses
    secured_meta = secured_data.get("metadata") if isinstance(secured_data.get("metadata"), dict) else {}
    secured_meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    secured_meta["lock_count"] = len(secured_data["locked_ids"])
    secured_data["metadata"] = secured_meta
    return secured_data


def find_bus(dataset, bus_name, reg_no):
    clean_name, parsed_reg, fleet_id = normalize_bus_identity(bus_name, reg_no)
    buses = dataset.get("buses", []) or []
    if parsed_reg:
        for idx, bus in enumerate(buses):
            b_name, b_reg, b_id = normalize_bus_identity(bus.get("bus_name"), bus.get("reg_no"))
            if b_reg == parsed_reg or b_id == fleet_id:
                return idx, bus, b_id
        return None, None, fleet_id

    name_matches = []
    for idx, bus in enumerate(buses):
        b_name, b_reg, b_id = normalize_bus_identity(bus.get("bus_name"), bus.get("reg_no"))
        if b_id == fleet_id:
            return idx, bus, b_id
        if b_name == clean_name:
            name_matches.append((idx, bus, b_id))
    if len(name_matches) == 1:
        return name_matches[0]
    if name_matches:
        return name_matches[0]
    return None, None, fleet_id


def find_movement_index(bus, trip_id, direction):
    movements = bus.get("movements", []) or []
    for i, mv in enumerate(movements):
        if trip_id and str(mv.get("trip_id") or "").strip() == str(trip_id).strip():
            return i
    if direction:
        matches = [
            i for i, mv in enumerate(movements)
            if str(mv.get("direction") or "").strip().lower() == str(direction).strip().lower()
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _resolve_movement(bus, trip_id=None, direction=None):
    if not isinstance(bus, dict):
        return None, None
    movements = bus.get("movements", []) or []
    mv_idx = find_movement_index(bus, trip_id, direction)
    if mv_idx is None and len(movements) == 1:
        mv_idx = 0
    if mv_idx is None or mv_idx < 0 or mv_idx >= len(movements):
        return None, None
    return mv_idx, movements[mv_idx]


def _extract_tt_stops(stops):
    items = []
    for s in stops or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "arrival_time": s.get("arrival_time"),
                "departure_time": s.get("departure_time"),
                "stop_type": s.get("stop_type"),
                "isWaypoint": bool(s.get("isWaypoint")),
            }
        )
    return items


def _resolve_trip_timetable(input_data, output_data, tt_data, secured_data, bus_name, reg_no, trip_id=None, direction=None):
    ordered = [("tt", tt_data), ("polyline", output_data), ("secure", secured_data), ("input", input_data)]
    for source_name, dataset in ordered:
        if not isinstance(dataset, dict):
            continue
        _, bus, _ = find_bus(dataset, bus_name, reg_no)
        if bus is None:
            continue
        mv_idx, movement = _resolve_movement(bus, trip_id, direction)
        if movement is None:
            continue
        return source_name, mv_idx, movement, _extract_tt_stops(movement.get("stops") or [])
    return None, None, None, []


def _bus_status_for_id(bus_id, secure_ids, tt_ids, polyline_ids):
    if bus_id in secure_ids:
        return "SECURE"
    if bus_id in tt_ids:
        return "TTHITL"
    if bus_id in polyline_ids:
        return "PHITL"
    return "INPUT"


def apply_trip_snapshot_to_input(input_data, snapshot):
    trip_meta = snapshot.get("trip") or {}
    markers = trip_meta.get("markers") or []
    bus_idx, bus, bus_id = find_bus(input_data, snapshot.get("bus_name"), snapshot.get("reg_no"))
    if bus is None:
        return None, None, {"status": "missing_bus", "fleet_id": bus_id}

    # This line was missing! It passes the data to the surgery function
    return apply_spatial_surgery_to_bus(bus, bus_idx, bus_id, trip_meta, markers)


def sync_deduplicated_movements(bus, rep_mv_idx, out_bus=None):
    """
    DEDUPLICATION SYNC ENGINE (Core Logic)
    Propagates geometry and stops from a representative movement to its dropped siblings.
    Supports mathematical reversal (flip_polyline) for opposing directions.
    If out_bus is provided, also updates the output structure.
    Returns: List of indices synced.
    """
    if not isinstance(bus, dict) or rep_mv_idx >= len(bus.get("movements", [])):
        return []
    
    synced_indices = []
    
    rep_mv = bus["movements"][rep_mv_idx]
    rep_trip_id = rep_mv.get("trip_id")
    rep_dir = str(rep_mv.get("direction")).strip().upper()
    
    dedupe = bus.get("movement_dedupe") or {}
    groups = dedupe.get("duplicate_groups") or []
    
    sync_count = 0
    for group in groups:
        # Check if this movement is the representative
        is_rep = (group.get("representative_idx") == rep_mv_idx or 
                  group.get("representative_trip_id") == rep_trip_id)
        
        if not is_rep:
            continue
            
        dropped_ids = group.get("dropped_trip_ids") or []
        for d_id in dropped_ids:
            d_idx = find_movement_index(bus, d_id, None)
            if d_idx is None or d_idx == rep_mv_idx:
                continue
            
            dropped_mv = bus["movements"][d_idx]
            d_dir = str(dropped_mv.get("direction")).strip().upper()
            needs_flip = (d_dir != rep_dir)
            
            # 1. Mirror/Flip Stops
            if not needs_flip:
                dropped_mv["stops"] = [dict(s) for s in rep_mv["stops"]]
            else:
                rev_stops = [dict(s) for s in rep_mv["stops"][::-1]]
                if rev_stops:
                    rev_stops[0]["stop_type"] = "ORIGIN"
                    rev_stops[-1]["stop_type"] = "DESTINATION"
                    for s in rev_stops[1:-1]:
                        s["stop_type"] = "INTERMEDIARY"
                dropped_mv["stops"] = rev_stops

            dropped_mv["origin"] = dropped_mv["stops"][0]["name"]
            dropped_mv["destination"] = dropped_mv["stops"][-1]["name"]

            # 2. Mirror/Flip Route (HANDICAP PROTOCOL)
            rep_route = rep_mv.get("route")
            if isinstance(rep_route, dict):
                # We do NOT copy geometry; only keep reference.
                d_route = {
                    "polyline": None,  # HANDICAPPED
                    "nodes": [],
                    "redundant_of": rep_trip_id,
                    "signature": group.get("signature")
                }
                dropped_mv["route"] = d_route
            
            # Propagate to output if provided
            if out_bus is not None and d_idx < len(out_bus.get("movements", [])):
                out_bus["movements"][d_idx].update(dropped_mv)
            
            synced_indices.append(d_idx)
            print(f"[DEDUPE SYNC] Synced {d_id} from {rep_trip_id} (flip={needs_flip})", flush=True)

    return synced_indices

def apply_spatial_surgery_to_bus(bus, bus_idx, bus_id, trip_meta, markers):
    """
    Surgically applies coordinate overrides to a specific trip within a bus.
    Preserves existing rich metadata (confidence, candidates) by patching.
    """
    mv_idx = find_movement_index(bus, trip_meta.get("trip_id"), trip_meta.get("direction"))
    if mv_idx is None:
        return bus_idx, None, {"status": "missing_trip", "fleet_id": bus_id}

    movement = bus["movements"][mv_idx]
    
    # 1. Map existing rich stop objects by normalized name for patching
    old_stops_map = {}
    for s in movement.get("stops", []) or []:
        key = normalize_stop_name(s.get("name"))
        if key:
            old_stops_map[key] = s

    # 2. Gather bus-wide fallback coordinates
    bus_coord_map = {}
    for mv in bus.get("movements", []) or []:
        for s in mv.get("stops", []) or []:
            key = normalize_stop_name(s.get("name"))
            lat, lng = s.get("lat"), s.get("lng")
            if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                bus_coord_map[key] = (float(lat), float(lng))
        rt = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        for n in rt.get("nodes", []) or []:
            key = normalize_stop_name(n.get("name"))
            lat, lng = n.get("lat"), n.get("lng")
            if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                bus_coord_map[key] = (float(lat), float(lng))

    # 3. Build new stops list by patching rich old stops
    new_stops = []
    for m in markers:
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        
        key = normalize_stop_name(name)
        # Surgical Patch: Use existing rich stop if possible
        if key in old_stops_map:
            item = dict(old_stops_map[key])
        else:
            item = {"name": name}
        
        # Apply spatial overrides from UI
        if m.get("isWaypoint"):
            item["isWaypoint"] = True
        if m.get("lat") is not None:
            item["lat"] = m.get("lat")
        if m.get("lng") is not None:
            item["lng"] = m.get("lng")
        
        # Backfill coordinates if still missing
        if ("lat" not in item or "lng" not in item) and key in bus_coord_map:
            item["lat"], item["lng"] = bus_coord_map[key]
            
        new_stops.append(item)

    if len(new_stops) < 2:
        return bus_idx, mv_idx, {"status": "invalid_stops", "fleet_id": bus_id}

    # 4. Apply update to target movement
    movement["stops"] = new_stops
    movement["origin"] = new_stops[0]["name"]
    movement["destination"] = new_stops[-1]["name"]

    # --- REMOVED LEGACY DEDUPLICATION SYNC HERE ---
    # We no longer force Trip 2 to copy Trip 1's stops.
    # The UI sends them independently, preserving their unique Timetables!

    return bus_idx, mv_idx, {"status": "ok", "fleet_id": bus_id}


def _normalize_staged_stop_overrides(staged_stops):
    overrides = {}
    if not isinstance(staged_stops, dict):
        return overrides
    for raw_name, payload in staged_stops.items():
        key = normalize_stop_name(raw_name)
        if not key or not isinstance(payload, dict):
            continue
        lat = payload.get("lat")
        lng = payload.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        new_name = str(payload.get("newName") or raw_name or "").strip()
        overrides[key] = {
            "lat": float(lat),
            "lng": float(lng),
            "newName": new_name,
        }
    return overrides


def apply_staged_stop_overrides_to_bus(bus, staged_overrides):
    if not isinstance(bus, dict) or not isinstance(staged_overrides, dict) or not staged_overrides:
        return 0

    updates = 0
    for mv in bus.get("movements", []) or []:
        for s in mv.get("stops", []) or []:
            key = normalize_stop_name(s.get("name"))
            if key not in staged_overrides:
                continue
            ov = staged_overrides[key]
            s["lat"] = ov["lat"]
            s["lng"] = ov["lng"]
            if ov.get("newName"):
                s["name"] = ov["newName"]
            updates += 1
        route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        for n in route.get("nodes", []) or []:
            key = normalize_stop_name(n.get("name"))
            if key not in staged_overrides:
                continue
            ov = staged_overrides[key]
            n["lat"] = ov["lat"]
            n["lng"] = ov["lng"]
            if ov.get("newName"):
                n["name"] = ov["newName"]
            updates += 1
        if (mv.get("stops") or []):
            mv["origin"] = (mv["stops"][0].get("name") or mv.get("origin"))
            mv["destination"] = (mv["stops"][-1].get("name") or mv.get("destination"))
    return updates


def ensure_output_bus_structure(output_data, input_bus):
    out_idx, out_bus, _ = find_bus(output_data, input_bus.get("bus_name"), input_bus.get("reg_no"))
    if out_bus is None:
        out_bus = {
            "bus_name": input_bus.get("bus_name"),
            "reg_no": input_bus.get("reg_no"),
            "primary_hub": input_bus.get("primary_hub"),
            "movements": []
        }
        output_data.setdefault("buses", []).append(out_bus)
        out_idx = len(output_data["buses"]) - 1
    while len(out_bus.get("movements", [])) < len(input_bus.get("movements", [])):
        out_bus.setdefault("movements", []).append({})
    return out_idx, out_bus


def recompute_for_targets(input_data, output_data, target_map):
    updated_routes = []
    recompute_report = []

    for bus_idx, movement_indices in target_map.items():
        bus = input_data["buses"][bus_idx]
        # Treat edits as bus-wide coordinate truth by propagating known coords
        # to same-named stops across all movements before recompute.
        # propagate_bus_stop_coordinates(bus)  # DISABLED: Protect Manual Truth Bank from ghosting corruption
        _, out_bus = ensure_output_bus_structure(output_data, bus)
        
        synced_indices = set()
        target_stop_coords = {}
        for mi in sorted(movement_indices):
            if mi in synced_indices:
                continue
            
            mv = (bus.get("movements") or [])[mi]
            for s in mv.get("stops", []) or []:
                key = normalize_stop_name(s.get("name"))
                lat = s.get("lat")
                lng = s.get("lng")
                if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    target_stop_coords[key] = (float(lat), float(lng))
        print(
            f"[HITL SYNC] bus_input_summary bus={bus.get('reg_no') or bus.get('bus_name')} "
            f"target_movements={len(sorted(movement_indices))} target_unique_stop_coords={len(target_stop_coords)}",
            flush=True,
        )
        bus_report = {
            "fleet_id": normalize_bus_identity(bus.get("bus_name"), bus.get("reg_no"))[2],
            "source": "PHITL",
            "target_unique_stop_coords": len(target_stop_coords),
            "movements": [],
        }
        print(
            f"[HITL SYNC] Running Full-Bus Deduplication Router for {bus.get('reg_no') or bus.get('bus_name')}",
            flush=True,
        )

        # 1. RUN THE NEW BUS-LEVEL ROUTER (Handles Handicaps natively!)
        new_movements = router.compute_bus_movement_routes(bus, force_refresh=True)

        for mi, new_mv in enumerate(new_movements):
            existing_out_movement = out_bus["movements"][mi] if mi < len(out_bus["movements"]) else {}
            if not isinstance(existing_out_movement, dict):
                existing_out_movement = {}

            # Preserve temporal data (arrival/departure) and UI states
            out_movement = dict(existing_out_movement)
            out_movement.update(new_mv) 

            # Rescue the routing validation block if it exists
            old_route = existing_out_movement.get("route") or {}
            if "validation" in old_route and "route" in out_movement:
                 if isinstance(out_movement["route"], dict):
                    out_movement["route"]["validation"] = old_route["validation"]

            enrich_stops_from_nodes(out_movement)
            enrich_movement_kinematics(out_movement, bus_context=bus)
            out_bus["movements"][mi] = out_movement
            
            route_entry = build_route_entry(bus, out_movement)
            updated_routes.append(route_entry)

            route_data = out_movement.get("route", {})
            meta = route_data.get("computeMeta", {})

            bus_report["movements"].append(
                {
                    "trip_id": new_mv.get("trip_id"),
                    "direction": new_mv.get("direction"),
                    "status": route_data.get("status", "ok" if route_data.get("master_geometry") else "handicapped"),
                    "stops": len(new_mv.get("stops") or []),
                    "nodes": len(route_data.get("nodes") or []),
                    "is_master": route_data.get("master_geometry", False),
                    "api_calls": int(meta.get("api_calls", 0)),
                    "api_elapsed_ms": int(meta.get("api_elapsed_ms", 0)),
                }
            )
        # harmonize_bus_stop_coordinates(bus)  # DISABLED: Protect Manual Truth Bank from ghosting corruption
        # harmonize_bus_stop_coordinates(out_bus) # DISABLED: Protect Manual Truth Bank from ghosting corruption
        unique_named_nodes = {}
        for mv in out_bus.get("movements", []) or []:
            route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
            for n in route.get("nodes") or []:
                key = normalize_stop_name(n.get("name"))
                if key and isinstance(n.get("lat"), (int, float)) and isinstance(n.get("lng"), (int, float)):
                    unique_named_nodes[key] = (float(n.get("lat")), float(n.get("lng")))
        print(
            f"[HITL SYNC] bus_summary bus={bus.get('reg_no') or bus.get('bus_name')} "
            f"movements={len(bus.get('movements') or [])} unique_named_stops={len(unique_named_nodes)} "
            f"target_unique_stop_coords={len(target_stop_coords)}",
            flush=True,
        )
        bus_report["unique_named_stops"] = len(unique_named_nodes)
        recompute_report.append(bus_report)

    return updated_routes, recompute_report


def parse_selected_bus_ids(selected_ids, input_data):
    targets = set()
    for sid in selected_ids or []:
        _, reg, fleet = normalize_bus_identity(sid, None)
        for i, bus in enumerate(input_data.get("buses", []) or []):
            _, b_reg, b_fleet = normalize_bus_identity(bus.get("bus_name"), bus.get("reg_no"))
            if (reg and reg == b_reg) or fleet == b_fleet or str(sid).strip() == bus.get("bus_name"):
                targets.add(i)
    return targets


def collect_zero_api_movements(recompute_report):
    failures = []
    for bus_report in recompute_report or []:
        fleet_id = bus_report.get("fleet_id") or "unknown_bus"
        for mv in bus_report.get("movements") or []:
            api_calls = int(mv.get("api_calls") or 0)
            stops = int(mv.get("stops") or 0)
            status = str(mv.get("status") or "").strip().lower()
            # Non-fatal when movement is explicitly invalid/blocked by guards.
            if status in {"invalid", "loop_guard_blocked"}:
                continue
            if stops >= 2 and api_calls <= 0:
                failures.append(
                    {
                        "fleet_id": fleet_id,
                        "trip_id": mv.get("trip_id"),
                        "direction": mv.get("direction"),
                        "stops": stops,
                        "status": mv.get("status"),
                        "api_calls": api_calls,
                    }
                )
    return failures


def collect_status_movements(recompute_report, target_status):
    items = []
    target_status = str(target_status or "").strip().lower()
    for bus_report in recompute_report or []:
        fleet_id = bus_report.get("fleet_id") or "unknown_bus"
        for mv in bus_report.get("movements") or []:
            status = str(mv.get("status") or "").strip().lower()
            if status != target_status:
                continue
            items.append(
                {
                    "fleet_id": fleet_id,
                    "trip_id": mv.get("trip_id"),
                    "direction": mv.get("direction"),
                    "stops": int(mv.get("stops") or 0),
                    "nodes": int(mv.get("nodes") or 0),
                    "api_calls": int(mv.get("api_calls") or 0),
                    "status": mv.get("status"),
                }
            )
    return items



def _normalize_corridor_text(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _reverse_corridor_text(value):
    parts = [part.strip() for part in str(value or "").split("-") if str(part or "").strip()]
    if len(parts) < 2:
        return ""
    return _normalize_corridor_text("-".join(parts[::-1]))


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dlat = p2 - p1
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _polyline_needs_flip_for_nodes(polyline, nodes):
    if not polyline or not isinstance(nodes, list) or len(nodes) < 2:
        return False
    try:
        pts = router._decode_polyline(polyline)
    except Exception:
        return False
    if not isinstance(pts, list) or len(pts) < 2:
        return False

    first_node = next((n for n in nodes if isinstance(n, dict) and isinstance(n.get("lat"), (int, float)) and isinstance(n.get("lng"), (int, float))), None)
    last_node = next((n for n in reversed(nodes) if isinstance(n, dict) and isinstance(n.get("lat"), (int, float)) and isinstance(n.get("lng"), (int, float))), None)
    if first_node is None or last_node is None:
        return False

    p0 = pts[0]
    p1 = pts[-1]
    same = _haversine_m(p0[0], p0[1], first_node["lat"], first_node["lng"]) + _haversine_m(p1[0], p1[1], last_node["lat"], last_node["lng"])
    flipped = _haversine_m(p0[0], p0[1], last_node["lat"], last_node["lng"]) + _haversine_m(p1[0], p1[1], first_node["lat"], first_node["lng"])
    return (flipped + 50.0) < same


def _normalize_movement_geometry_orientation(movement):
    if not isinstance(movement, dict):
        return False
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
    poly = route.get("polyline")
    stops = movement.get("stops") if isinstance(movement.get("stops"), list) else []
    changed = False

    # 1) Align route nodes with stop order if they are reversed.
    stop_names = [normalize_stop_name(s.get("name")) for s in stops if isinstance(s, dict) and str(s.get("name") or "").strip()]
    node_names = [normalize_stop_name(n.get("name")) for n in nodes if isinstance(n, dict) and str(n.get("name") or "").strip()]
    if len(stop_names) >= 2 and len(node_names) >= 2:
        if stop_names[0] == node_names[-1] and stop_names[-1] == node_names[0]:
            route["nodes"] = [dict(n) if isinstance(n, dict) else n for n in list(reversed(nodes))]
            nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
            changed = True

    # 2) Align encoded polyline orientation with (possibly corrected) node order.
    if poly and len(nodes) >= 2 and _polyline_needs_flip_for_nodes(poly, nodes):
        route["polyline"] = flip_polyline(poly)
        changed = True

    if changed:
        movement["route"] = route
    return changed


def _normalize_secure_snapshot_geometry(secured):
    if not isinstance(secured, dict):
        return 0
    changed_count = 0
    for bus in secured.get("buses", []) or []:
        if not isinstance(bus, dict):
            continue
        for mv in bus.get("movements", []) or []:
            if _normalize_movement_geometry_orientation(mv):
                changed_count += 1
    return changed_count




def _point_to_segment_distance_m(lat, lng, a_lat, a_lng, b_lat, b_lng):
    lat_to_m = 111320.0
    lng_to_m = 111320.0 * math.cos(math.radians(float(lat)))
    dx = (float(b_lng) - float(a_lng)) * lng_to_m
    dy = (float(b_lat) - float(a_lat)) * lat_to_m
    px = (float(lng) - float(a_lng)) * lng_to_m
    py = (float(lat) - float(a_lat)) * lat_to_m
    length_sq = dx * dx + dy * dy
    frac = 0.0
    if length_sq > 0:
        frac = max(0.0, min(1.0, (px * dx + py * dy) / length_sq))
    proj_lng = float(a_lng) + frac * (float(b_lng) - float(a_lng))
    proj_lat = float(a_lat) + frac * (float(b_lat) - float(a_lat))
    return _haversine_m(float(lat), float(lng), proj_lat, proj_lng)


def _route_polyline_anchor_mismatch(route, max_anchor_m=1800.0):
    if not isinstance(route, dict):
        return True
    poly = route.get("polyline")
    nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
    if not poly or len(nodes) < 2:
        return True
    try:
        pts = router._decode_polyline(poly)
    except Exception:
        return True
    if not isinstance(pts, list) or len(pts) < 2:
        return True
    anchors = [
        n for n in nodes
        if isinstance(n, dict)
        and isinstance(n.get("lat"), (int, float))
        and isinstance(n.get("lng"), (int, float))
    ]
    if len(anchors) < 2:
        return False
    for a in anchors:
        best = None
        for i in range(len(pts) - 1):
            d = _point_to_segment_distance_m(a["lat"], a["lng"], pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
            if best is None or d < best:
                best = d
        if best is None or best > float(max_anchor_m):
            return True
    return False


def _repair_secured_snapshot_polylines(secured, output_data):
    if not isinstance(secured, dict) or not isinstance(output_data, dict):
        return 0
    repaired = 0
    for s_bus in secured.get("buses", []) or []:
        if not isinstance(s_bus, dict):
            continue
        _, o_bus, _ = find_bus(output_data, s_bus.get("bus_name"), s_bus.get("reg_no"))
        if not isinstance(o_bus, dict):
            continue
        o_moves = o_bus.get("movements", []) or []
        for idx, s_mv in enumerate(s_bus.get("movements", []) or []):
            if not isinstance(s_mv, dict):
                continue
            s_route = s_mv.get("route") if isinstance(s_mv.get("route"), dict) else {}
            s_bad = _route_polyline_anchor_mismatch(s_route)

            trip_id = str(s_mv.get("trip_id") or "").strip()
            o_mv = None
            if trip_id:
                o_mv = next((m for m in o_moves if isinstance(m, dict) and str(m.get("trip_id") or "").strip() == trip_id), None)
            if o_mv is None and idx < len(o_moves) and isinstance(o_moves[idx], dict):
                o_mv = o_moves[idx]
            if not isinstance(o_mv, dict):
                continue
            o_route = o_mv.get("route") if isinstance(o_mv.get("route"), dict) else {}
            o_bad = _route_polyline_anchor_mismatch(o_route)
            if (not o_bad) and (s_bad or not s_route.get("polyline")):
                s_mv["route"] = {
                    "polyline": o_route.get("polyline") or "",
                    "nodes": o_route.get("nodes") or [],
                    **{k: v for k, v in o_route.items() if k not in {"polyline", "nodes"}},
                }
                repaired += 1
    return repaired
def _hydrate_snapshot_polylines(movements):
    if not isinstance(movements, list) or not movements:
        return 0

    candidates = []
    for mv in movements:
        if not isinstance(mv, dict):
            continue
        route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        poly = route.get("polyline")
        if not poly:
            continue
        candidates.append(
            {
                "polyline": poly,
                "nodes": route.get("nodes") if isinstance(route.get("nodes"), list) else [],
                "corridor_id": _normalize_corridor_text(mv.get("corridor_id")),
                "corridor_sig": _normalize_corridor_text(mv.get("corridor_signature")),
                "origin": normalize_stop_name(mv.get("origin")),
                "destination": normalize_stop_name(mv.get("destination")),
                "direction": str(mv.get("direction") or "").strip().upper(),
            }
        )

    if not candidates:
        return 0

    hydrated = 0
    for mv in movements:
        if not isinstance(mv, dict):
            continue
        route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        if route.get("polyline"):
            continue

        cid = _normalize_corridor_text(mv.get("corridor_id"))
        sig = _normalize_corridor_text(mv.get("corridor_signature"))
        sig_rev = _reverse_corridor_text(mv.get("corridor_signature"))
        origin = normalize_stop_name(mv.get("origin"))
        destination = normalize_stop_name(mv.get("destination"))
        direction = str(mv.get("direction") or "").strip().upper()

        best = None
        best_score = -1
        best_flip = False
        for cand in candidates:
            score = -1
            needs_flip = False
            if cid and cand.get("corridor_id") == cid:
                score = 100
            elif sig and cand.get("corridor_sig") == sig:
                score = 90
            elif sig_rev and cand.get("corridor_sig") == sig_rev:
                score = 85
                needs_flip = True
            elif origin and destination and cand.get("origin") == origin and cand.get("destination") == destination:
                score = 80
            elif origin and destination and cand.get("origin") == destination and cand.get("destination") == origin:
                score = 78
                needs_flip = True

            if direction and cand.get("direction") and direction != cand.get("direction"):
                needs_flip = not needs_flip
                if score >= 0:
                    score -= 1

            if score > best_score:
                best_score = score
                best = cand
                best_flip = needs_flip

        if not best or best_score < 0:
            continue

        poly = best.get("polyline")
        if best_flip:
            poly = flip_polyline(poly)
        if not poly:
            continue

        route["polyline"] = poly
        if not route.get("nodes") and isinstance(best.get("nodes"), list) and best.get("nodes"):
            if best_flip:
                route["nodes"] = [dict(node) for node in list(reversed(best["nodes"]))]
            else:
                route["nodes"] = [dict(node) for node in best["nodes"]]
        mv["route"] = route
        hydrated += 1

    # Orientation correction pass: keep polyline direction aligned with node order.
    for mv in movements:
        if not isinstance(mv, dict):
            continue
        route = mv.get("route") if isinstance(mv.get("route"), dict) else {}
        poly = route.get("polyline")
        nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
        if not poly or len(nodes) < 2:
            continue
        if _polyline_needs_flip_for_nodes(poly, nodes):
            route["polyline"] = flip_polyline(poly)
            mv["route"] = route

    return hydrated
def secure_bus_snapshot(input_data, output_data, bus_idx):
    in_bus = input_data.get("buses", [])[bus_idx]
    _, out_bus, _ = find_bus(output_data, in_bus.get("bus_name"), in_bus.get("reg_no"))
    snapshot = dict(in_bus)
    out_movements = out_bus.get("movements", []) if isinstance(out_bus, dict) else []
    merged_movements = []
    for i, in_mv in enumerate(in_bus.get("movements", []) or []):
        merged = {}
        out_mv = out_movements[i] if i < len(out_movements) and isinstance(out_movements[i], dict) else {}
        if isinstance(in_mv, dict):
            merged.update(in_mv)
        if isinstance(out_mv, dict):
            # Output is authoritative for secured snapshot geometry/enrichment.
            merged.update(out_mv)
        if "route" not in merged:
            merged["route"] = {"polyline": "", "nodes": []}
        merged_movements.append(merged)
    _hydrate_snapshot_polylines(merged_movements)
    snapshot["movements"] = merged_movements
    return snapshot


@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "route_verification_map.html")


@app.route("/route_verification_map.html")
def route_map():
    return send_from_directory(str(BASE_DIR), "route_verification_map.html")


@app.route("/Background/<path:filename>")
def serve_background(filename):
    background_dir = BASE_DIR.parent / "Background"
    return send_from_directory(str(background_dir), filename)



@app.route("/ZGemma_files/Test_Dataset/<path:filename>")
def serve_test_dataset(filename):
    dataset_dir = PROJECT_ROOT / "ZGemma_files" / "Test_Dataset"
    return send_from_directory(str(dataset_dir), filename)


@app.route("/api/config", methods=["GET"])
def api_config():
    key = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY") or ""
    return jsonify({"status": "success", "google_maps_api_key": key})


@app.route("/get_cache", methods=["GET"])
def get_cache():
    sig = _get_cache_signature()
    with _GET_CACHE_DATA_LOCK:
        cached_sig = _GET_CACHE_DATA_CACHE.get("sig")
        cached_payload = _GET_CACHE_DATA_CACHE.get("payload")
        
        # If we have a payload (even if sig doesn't match perfectly), we return it
        # to avoid blocking. The background warmup will update it eventually.
        if isinstance(cached_payload, dict):
            # Inject warmup status into the payload
            response_payload = dict(cached_payload)
            response_payload["warmup_status"] = WARMUP_STATE
            response_payload["cache_is_stale"] = (cached_sig != sig)
            return jsonify(response_payload)

    # Fallback if no cache at all (very first boot ever)
    return jsonify({
        "status": "warming",
        "message": "Server is warming up cache in background.",
        "warmup_status": WARMUP_STATE,
        "verified_routes": [],
        "secure_verified_routes": []
    })


@app.route("/get_trip_timetable", methods=["POST"])
def get_trip_timetable():
    data = request.get_json(silent=True) or {}
    bus_name = data.get("bus_name")
    reg_no = data.get("reg_no")
    trip_id = data.get("trip_id")
    direction = data.get("direction")

    if not str(bus_name or "").strip():
        return jsonify({"status": "error", "message": "bus_name is required"}), 400

    # Heavy backfills are now handled in background_warmup()
    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    tt_data = load_bus_dataset(TT_OUTPUT_FILE)
    secured_data = load_secured_data()
    _secure_sig = None # Not needed for timetable
    secure_ids = set(secured_data.get("locked_ids") or [])
    polyline_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (output_data.get("buses") or [])}
    tt_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (tt_data.get("buses") or [])}

    _, input_bus, fleet_id = find_bus(input_data, bus_name, reg_no)
    if input_bus is None:
        return jsonify({"status": "error", "message": f"Bus not found: {bus_name}"}), 404

    source, mv_idx, movement, stops = _resolve_trip_timetable(
        input_data, output_data, tt_data, secured_data, bus_name, reg_no, trip_id=trip_id, direction=direction
    )
    if movement is None:
        return jsonify({"status": "error", "message": "Trip not found for timetable source resolution"}), 404
    if isinstance(stops, list) and len(stops) > 0 and isinstance(stops[0], dict):
        stops[0]["arrival_time"] = None
    if isinstance(stops, list) and len(stops) > 1 and isinstance(stops[-1], dict):
        stops[-1]["departure_time"] = None

    status = _bus_status_for_id(fleet_id, secure_ids, tt_ids, polyline_ids)
    return jsonify(
        {
            "status": "success",
            "source": source,
            "fleet_id": fleet_id,
            "bus_name": input_bus.get("bus_name"),
            "reg_no": input_bus.get("reg_no"),
            "trip_id": movement.get("trip_id"),
            "direction": movement.get("direction"),
            "movement_index": mv_idx,
            "editable": status in {"INPUT", "PHITL", "TTHITL", "SECURE"},
            "bus_status": status,
            "stops": stops,
        }
    )


@app.route("/get_trip_analysis", methods=["POST"])
def get_trip_analysis():
    data = request.get_json(silent=True) or {}
    bus_name = data.get("bus_name")
    reg_no = data.get("reg_no")
    trip_id = data.get("trip_id")
    direction = data.get("direction")

    if not str(bus_name or "").strip():
        return jsonify({"status": "error", "message": "bus_name is required"}), 400

    # Heavy backfills are now handled in background_warmup()
    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    tt_data = load_bus_dataset(TT_OUTPUT_FILE)
    secured_data, _secure_sig = load_secured_data_for_proximity()
    secure_ids = set(secured_data.get("locked_ids") or [])
    polyline_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (output_data.get("buses") or [])}
    tt_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (tt_data.get("buses") or [])}

    _, input_bus, fleet_id = find_bus(input_data, bus_name, reg_no)
    if input_bus is None:
        return jsonify({"status": "error", "message": f"Bus not found: {bus_name}"}), 404

    bus_status = _bus_status_for_id(fleet_id, secure_ids, tt_ids, polyline_ids)
    source_name, mv_idx, movement, _ = _resolve_trip_timetable(
        input_data, output_data, tt_data, secured_data, bus_name, reg_no, trip_id=trip_id, direction=direction
    )
    if movement is None:
        return jsonify({"status": "error", "message": "Trip not found"}), 404

    # Analysis source follows timetable precedence resolved above: TT -> PHITL -> SECURE -> INPUT.
    source_map = {"tt": tt_data, "polyline": output_data, "secure": secured_data, "input": input_data}
    src_dataset = source_map.get(source_name, input_data)
    _, src_bus, _ = find_bus(src_dataset, bus_name, reg_no)
    analysis_bus = src_bus if src_bus is not None else input_bus
    analysis_movement = movement
    analysis_source = f"embedded_{source_name or 'resolved'}"

    if not movement_has_embedded_kinematics(analysis_movement):
        enrich_movement_kinematics(analysis_movement, bus_context=analysis_bus)
        analysis_source = "embedded_live_fallback"

    analysis = analysis_from_movement(analysis_movement, bus_name=analysis_bus.get("bus_name"), reg_no=analysis_bus.get("reg_no"))
    if not analysis.get("available"):
        route_nodes = movement_route(analysis_movement).get("nodes") or []
        stops = analysis_movement.get("stops") or []
        stops_with_coords = [
            s for s in (stops if isinstance(stops, list) else [])
            if isinstance(s, dict) and isinstance(s.get("lat"), (int, float)) and isinstance(s.get("lng"), (int, float))
        ]
        fallback_rows = stops_with_coords if len(stops_with_coords) >= 2 else (route_nodes if isinstance(route_nodes, list) else [])
        analysis = compute_trip_fallback_kinematics(
            fallback_rows,
            corridor_signature=analysis_movement.get("corridor_signature"),
            origin=analysis_movement.get("origin"),
            destination=analysis_movement.get("destination"),
        )
        analysis_source = analysis.get("source") if analysis.get("available") else "unavailable"

    return jsonify(
        {
            "status": "success",
            "bus_id": fleet_id,
            "bus_status": bus_status,
            "trip_id": movement.get("trip_id"),
            "direction": movement.get("direction"),
            "source_trip_dataset": source_name,
            "analysis_source": analysis_source,
            "analysis": analysis,
        }
    )


@app.route("/get_secure_proximity", methods=["POST"])
def get_secure_proximity():
    data = request.get_json(silent=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return jsonify({"status": "error", "message": "lat and lng are required numeric values"}), 400

    secured_data, secure_sig = load_secured_data_for_proximity()
    result = resolve_secure_proximity(
        secured_data,
        float(lat),
        float(lng),
        now_minutes=data.get("now_minutes"),
        cache_key=secure_sig,
    )
    if not result.get("available"):
        return jsonify({"status": "error", "message": result.get("reason", "proximity unavailable"), "result": result}), 404
    return jsonify({"status": "success", "result": result})


@app.route("/get_journey_planner_proximity", methods=["POST"])
def get_journey_planner_proximity():
    data = request.get_json(silent=True) or {}
    o_lat = data.get("origin_lat")
    o_lng = data.get("origin_lng")
    d_lat = data.get("dest_lat")
    d_lng = data.get("dest_lng")

    if not all(isinstance(x, (int, float)) for x in [o_lat, o_lng, d_lat, d_lng]):
        return jsonify({"status": "error", "message": "origin and dest coordinates are required numeric values"}), 400

    secured_data, secure_sig = load_secured_data_for_proximity()
    from Analyses.proximity_backend import resolve_journey_planner_proximity
    result = resolve_journey_planner_proximity(
        secured_data,
        float(o_lat),
        float(o_lng),
        float(d_lat),
        float(d_lng),
        now_minutes=data.get("now_minutes"),
        cache_key=secure_sig,
    )
    if not result.get("available"):
        return jsonify({"status": "error", "message": result.get("reason", "journey proximity unavailable"), "result": result}), 404
        
    return jsonify({"status": "success", "result": result})


@app.route("/commit_tt_overrides", methods=["POST"])
def commit_tt_overrides():
    ensure_terminal_time_backfill()
    data = request.get_json(silent=True) or {}
    bus_name = data.get("bus_name")
    reg_no = data.get("reg_no")
    trip_id = data.get("trip_id")
    direction = data.get("direction")
    stops_payload = data.get("stops") or data.get("markers") or []

    if not str(bus_name or "").strip():
        return jsonify({"status": "error", "message": "bus_name is required"}), 400
    if not isinstance(stops_payload, list) or len(stops_payload) < 2:
        return jsonify({"status": "error", "message": "Need at least 2 stop time rows"}), 400

    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_bus_dataset(OUTPUT_FILE)
    tt_data = load_bus_dataset(TT_OUTPUT_FILE)
    secured_data, _secure_sig = load_secured_data_for_proximity()
    locked_ids = set(secured_data.get("locked_ids") or [])
    polyline_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (output_data.get("buses") or [])}
    tt_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (tt_data.get("buses") or [])}

    bus_idx, input_bus, fleet_id = find_bus(input_data, bus_name, reg_no)
    if input_bus is None:
        return jsonify({"status": "error", "message": f"Bus not found: {bus_name}"}), 404
    bus_status = _bus_status_for_id(fleet_id, locked_ids, tt_ids, polyline_ids)
    if bus_status not in {"INPUT", "PHITL", "TTHITL", "SECURE"}:
        return jsonify(
            {
                "status": "error",
                "message": f"TTHITL sync allowed only for INPUT/PHITL/TTHITL/SECURE buses. Current status: {bus_status}",
                "bus_status": bus_status,
                "bus_id": fleet_id,
            }
        ), 409

    # Build baseline bus snapshot: polyline output -> TT overlay -> input.
    _, base_tt_bus, _ = find_bus(tt_data, bus_name, reg_no)
    _, base_output_bus, _ = find_bus(output_data, bus_name, reg_no)
    if base_output_bus is not None:
        base_bus = json.loads(json.dumps(base_output_bus))
        if base_tt_bus is not None:
            _overlay_tt_times(base_bus, base_tt_bus)
    elif base_tt_bus is not None:
        base_bus = json.loads(json.dumps(base_tt_bus))
    else:
        base_bus = json.loads(json.dumps(input_bus))

    mv_idx, movement = _resolve_movement(base_bus, trip_id=trip_id, direction=direction)
    if movement is None:
        return jsonify({"status": "error", "message": "Target trip not found for TT overwrite"}), 404

    normalized_rows = []
    by_name = {}
    for i, row in enumerate(stops_payload):
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name") or "").strip()
        if not nm:
            continue
        arrival = row.get("arrival_time", row.get("arrival"))
        departure = row.get("departure_time", row.get("departure"))
        item = {
            "idx": i,
            "name": nm,
            "key": normalize_stop_name(nm),
            "arrival_time": arrival,
            "departure_time": departure,
        }
        normalized_rows.append(item)
        by_name[item["key"]] = item

    if len(normalized_rows) < 2:
        return jsonify({"status": "error", "message": "No valid stop rows with names supplied"}), 400

    stops = movement.get("stops") or []
    if not isinstance(stops, list):
        stops = []
        movement["stops"] = stops

    for idx, stop in enumerate(stops):
        if not isinstance(stop, dict):
            continue
        key = normalize_stop_name(stop.get("name"))
        row = by_name.get(key)
        if row is None and idx < len(normalized_rows):
            row = normalized_rows[idx]
        if row is None:
            continue
        stop["arrival_time"] = row.get("arrival_time")
        stop["departure_time"] = row.get("departure_time")

    if len(stops) > 0 and isinstance(stops[0], dict):
        stops[0]["arrival_time"] = None
    if len(stops) > 1 and isinstance(stops[-1], dict):
        stops[-1]["departure_time"] = None

    route = movement.get("route")
    if isinstance(route, dict):
        first_stop_key = normalize_stop_name(stops[0].get("name")) if len(stops) > 0 and isinstance(stops[0], dict) else ""
        last_stop_key = normalize_stop_name(stops[-1].get("name")) if len(stops) > 1 and isinstance(stops[-1], dict) else ""
        for node in route.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            row = by_name.get(normalize_stop_name(node.get("name")))
            if row is None:
                continue
            node["arrival_time"] = row.get("arrival_time")
            node["departure_time"] = row.get("departure_time")
            node_key = normalize_stop_name(node.get("name"))
            if first_stop_key and node_key == first_stop_key:
                node["arrival_time"] = None
            if last_stop_key and node_key == last_stop_key:
                node["departure_time"] = None

    # Recompute embedded distance/speed physics on updated timetable truth.
    enrich_bus_kinematics(base_bus)

    tt_idx, _, _ = find_bus(tt_data, base_bus.get("bus_name"), base_bus.get("reg_no"))
    if tt_idx is None:
        tt_data.setdefault("buses", []).append(base_bus)
    else:
        tt_data["buses"][tt_idx] = base_bus

    now_iso = datetime.now().isoformat(timespec="seconds")
    tt_meta = tt_data.get("metadata") if isinstance(tt_data.get("metadata"), dict) else {}
    tt_meta["generated_at"] = now_iso
    tt_meta["source_file"] = str(INPUT_FILE)
    tt_meta["source"] = "HITL TT sync"
    tt_meta["total_buses"] = len(tt_data.get("buses") or [])
    tt_meta["analysis_engine"] = "hitl_embedded_kinematics_v1 + speed_physics_v1"
    tt_data["metadata"] = tt_meta

    backup_json_file(TT_OUTPUT_FILE)
    atomic_write_json(TT_OUTPUT_FILE, tt_data)

    return jsonify(
        {
            "status": "success",
            "message": "TTHITL overwrite committed.",
            "bus_id": fleet_id,
            "bus_name": base_bus.get("bus_name"),
            "reg_no": base_bus.get("reg_no"),
            "trip_id": movement.get("trip_id"),
            "direction": movement.get("direction"),
            "movement_index": mv_idx,
            "tt_synced": True,
        }
    )



@app.route("/api/raptor/metadata", methods=["GET"])
def get_raptor_metadata():
    """Returns a unified search index for villages and transit stops."""
    try:
        return jsonify(load_raptor_metadata_payload())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/raptor/solve", methods=["POST"])
def solve_raptor_route():
    """Executes the McRAPTOR solver - discovers ALL reasonable journey options."""
    try:
        def _coerce_minutes(value, default=None):
            if value is None or value == "":
                return default
            try:
                mins = int(float(value))
            except (TypeError, ValueError):
                return default
            return max(0, min(1439, mins))

        def _leg_sig(leg):
            return (
                leg.get("type"),
                leg.get("from_stop") or leg.get("from"),
                leg.get("to_stop") or leg.get("to"),
                leg.get("trip_id") or leg.get("bus_name")
            )

        def _option_sig(opt):
            itin = opt.get("itinerary") or []
            return tuple(_leg_sig(leg) for leg in itin)

        def _prune_profile_options(options, mode="depart_after", anchor_mins=None):
            if not options:
                return []
            best_by_sig = {}
            for opt in options:
                sig = _option_sig(opt)
                prev = best_by_sig.get(sig)
                if prev is None:
                    best_by_sig[sig] = opt
                    continue
                if mode == "arrive_before":
                    prev_key = (
                        abs((prev.get("arrival_mins", 10**9) - prev.get("departure_mins", -1)) - prev.get("duration_mins", 10**9)),
                        -(prev.get("departure_mins", -1)),
                        prev.get("duration_mins", 10**9),
                        prev.get("transfers", 99),
                    )
                    curr_key = (
                        abs((opt.get("arrival_mins", 10**9) - opt.get("departure_mins", -1)) - opt.get("duration_mins", 10**9)),
                        -(opt.get("departure_mins", -1)),
                        opt.get("duration_mins", 10**9),
                        opt.get("transfers", 99),
                    )
                else:
                    prev_key = (
                        prev.get("arrival_mins", 10**9),
                        prev.get("duration_mins", 10**9),
                        prev.get("transfers", 99),
                    )
                    curr_key = (
                        opt.get("arrival_mins", 10**9),
                        opt.get("duration_mins", 10**9),
                        opt.get("transfers", 99),
                    )
                if curr_key < prev_key:
                    best_by_sig[sig] = opt
            deduped = [o for o in best_by_sig.values() if 0 < o.get("duration_mins", 10**9) <= 12 * 60]
            if not deduped:
                return []
            if mode == "arrive_before":
                deduped.sort(key=lambda x: (
                    -(x.get("departure_mins", -1)),
                    x.get("duration_mins", 10**9),
                    x.get("arrival_mins", 10**9),
                    x.get("transfers", 99),
                ))
                latest_departure = deduped[0].get("departure_mins", 0)
                min_duration = min(o.get("duration_mins", 10**9) for o in deduped)
                min_departure = max(0, latest_departure - 180)
                max_duration = min(480, max(180, min_duration + 120))
                bounded = [
                    o for o in deduped
                    if o.get("departure_mins", -1) >= min_departure
                    and o.get("duration_mins", 10**9) <= max_duration
                ]
            elif mode == "all_day":
                deduped.sort(key=lambda x: (
                    x.get("departure_mins", 10**9),
                    x.get("duration_mins", 10**9),
                    x.get("transfers", 99),
                ))
                best_duration = deduped[0].get("duration_mins", 0) if deduped else 0
                max_duration = min(480, max(180, best_duration + 180))
                bounded = [
                    o for o in deduped
                    if o.get("duration_mins", 10**9) <= max_duration
                ]
            else:
                deduped.sort(key=lambda x: (
                    x.get("arrival_mins", 10**9),
                    x.get("duration_mins", 10**9),
                    x.get("transfers", 99),
                ))
                best_arrival = deduped[0].get("arrival_mins", 10**9)
                best_duration = deduped[0].get("duration_mins", 0)
                max_arrival = min(24 * 60, best_arrival + 180)
                max_duration = min(480, max(180, best_duration + 120))
                bounded = [
                    o for o in deduped
                    if o.get("arrival_mins", 10**9) <= max_arrival
                    and o.get("duration_mins", 10**9) <= max_duration
                ]
            if not bounded:
                bounded = deduped
            if mode == "arrive_before":
                bounded.sort(key=lambda x: (
                    abs(x.get("arrival_mins", 10**9) - (anchor_mins if anchor_mins is not None else x.get("arrival_mins", 10**9))),
                    -x.get("departure_mins", -1),
                    x.get("duration_mins", 10**9),
                    x.get("transfers", 99),
                ))
            elif mode == "all_day":
                bounded.sort(key=lambda x: (
                    x.get("departure_mins", 10**9),
                    x.get("duration_mins", 10**9),
                    x.get("transfers", 99),
                ))
            else:
                bounded.sort(key=lambda x: (
                    x.get("arrival_mins", 10**9),
                    x.get("duration_mins", 10**9),
                    x.get("transfers", 99),
                    x.get("departure_mins", 10**9),
                ))

            shortlisted = []
            seen_buckets = set()
            for opt in bounded:
                bucket = (opt.get("departure_mins", 0) // 30) if mode == "all_day" else ((opt.get("departure_mins", 0) // 20) if mode == "depart_after" else (opt.get("arrival_mins", 0) // 20))
                max_in_bucket = 1 if mode == "all_day" else 6
                max_total = 24 if mode == "all_day" else 12
                
                # We can have multiple options per bucket if they are distinct routes, but we limit it
                if bucket in seen_buckets and len(shortlisted) >= max_total / 2:
                    pass # Allow it but not strictly block, actually let's just use it to spread
                
                if bucket not in seen_buckets:
                    seen_buckets.add(bucket)
                shortlisted.append(opt)
                if len(shortlisted) >= max_total:
                    break
            return shortlisted

        data = request.get_json(silent=True) or {}
        o_lat = data.get("origin_lat")
        o_lng = data.get("origin_lng")
        d_lat = data.get("dest_lat")
        d_lng = data.get("dest_lng")
        if None in [o_lat, o_lng, d_lat, d_lng]:
            return jsonify({"status": "error", "message": "Missing coordinates"}), 400

        time_mode = str(data.get("time_mode") or "arrive_before").strip().lower()
        depart_after = _coerce_minutes(data.get("departure_mins"), 240)
        arrive_before = _coerce_minutes(data.get("arrive_before_mins"), 1320)
        window_start = _coerce_minutes(data.get("time_window_start"), 240)
        window_end = _coerce_minutes(data.get("time_window_end"), 1320)

        if time_mode == "arrive_before":
            window_start = _coerce_minutes(data.get("time_window_start"), 240)
            window_end = arrive_before
        elif time_mode == "all_day":
            window_start = 240
            window_end = 1380
            depart_after = 240
        else:
            time_mode = "depart_after"
            window_start = depart_after
            window_end = _coerce_minutes(data.get("time_window_end"), 1320)

        if window_end < window_start:
            window_end = window_start

        router_instance = get_raptor_router()
        if not router_instance:
             return jsonify({"status": "error", "message": "RAPTOR engine not available"}), 500

        result = router_instance.solve_all_options(o_lat, o_lng, d_lat, d_lng,
                                                  time_window_start=window_start,
                                                  time_window_end=window_end)
        if result.get("status") == "SUCCESS" and result.get("options"):
            if time_mode == "arrive_before":
                result["options"] = [
                    opt for opt in result["options"]
                    if opt.get("arrival_mins", 10**9) <= arrive_before
                ]
            else:
                result["options"] = [
                    opt for opt in result["options"]
                    if opt.get("departure_mins", -1) >= depart_after
                ]
            result["options"] = _prune_profile_options(
                result["options"],
                mode=time_mode,
                anchor_mins=arrive_before if time_mode == "arrive_before" else depart_after,
            )
            if not result["options"]:
                result["status"] = "NO_ROUTE"
            result["constraint"] = {
                "mode": time_mode,
                "departure_mins": depart_after if time_mode == "depart_after" else None,
                "arrive_before_mins": arrive_before if time_mode == "arrive_before" else None,
                "time_window_start": window_start,
                "time_window_end": window_end,
            }

        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/recompute_preview", methods=["POST"])
def recompute_preview():
    data = request.get_json(silent=True) or {}
    markers = data.get("markers") or []
    if markers:
        stops = []
        for m in markers:
            nm = str(m.get("name") or "").strip()
            if not nm:
                continue
            item = {"name": nm, "isWaypoint": bool(m.get("isWaypoint"))}
            if m.get("lat") is not None:
                item["lat"] = m.get("lat")
            if m.get("lng") is not None:
                item["lng"] = m.get("lng")
            if m.get("arrival_time"):
                item["arrival_time"] = m.get("arrival_time")
            if m.get("departure_time"):
                item["departure_time"] = m.get("departure_time")
            stops.append(item)
    else:
        stops = [{"name": str(s).strip()} for s in (data.get("stops") or []) if str(s).strip()]
    if len(stops) < 2:
        return jsonify({"status": "error", "message": "Need at least 2 stops"}), 400
    movement = {
        "trip_id": data.get("trip_id"),
        "direction": data.get("direction"),
        "origin": str(stops[0].get("name") or "").strip(),
        "destination": str(stops[-1].get("name") or "").strip(),
        "stops": stops,
    }
    result = router.compute_verified_route(movement) or {"polyline": "", "nodes": []}

    # Re-attach TT metadata from input to calculated nodes.
    time_map = {normalize_stop_name(s.get("name")): s for s in stops}
    for n in result.get("nodes", []):
        key = normalize_stop_name(n.get("name"))
        if key in time_map:
            n["arrival_time"] = time_map[key].get("arrival_time")
            n["departure_time"] = time_map[key].get("departure_time")

    result["status"] = "success"
    return jsonify(result)



@app.route("/recompute_bulk", methods=["POST"])
def recompute_bulk():
    data = request.get_json(silent=True) or {}
    input_data = load_bus_dataset(INPUT_FILE)
    existing_output = load_bus_dataset(OUTPUT_FILE)
    existing_output_count = len(existing_output.get("buses") or []) if isinstance(existing_output, dict) else 0
    output_data = load_output_for_write(input_data)
    secured_data, _secure_sig = load_secured_data_for_proximity()
    locked_ids = set(secured_data.get("locked_ids") or [])

    selected_bus_indices = parse_selected_bus_ids(data.get("bus_names") or [], input_data)
    if not selected_bus_indices:
        return jsonify({"status": "error", "message": "No matching buses selected."}), 400

    target_map = {}
    for bi in selected_bus_indices:
        movement_count = len((input_data["buses"][bi].get("movements") or []))
        target_map[bi] = set(range(movement_count))

    if not target_map:
        return jsonify({"status": "error", "message": "No valid buses selected."}), 400

    if hasattr(router, "route_result_cache") and isinstance(router.route_result_cache, dict):
        router.route_result_cache.clear()

    t0 = time.perf_counter()
    updated_routes, recompute_report = recompute_for_targets(input_data, output_data, target_map)
    recompute_elapsed_s = round(time.perf_counter() - t0, 2)
    total_api_calls = sum(
        int((m.get("api_calls") or 0))
        for b in recompute_report
        for m in (b.get("movements") or [])
    )
    zero_api_movements = collect_zero_api_movements(recompute_report)
    invalid_movements = collect_status_movements(recompute_report, "invalid")

    if zero_api_movements:
        return jsonify(
            {
                "status": "error",
                "message": "Recompute aborted: one or more movements did not hit Routes API.",
                "zero_api_movements": zero_api_movements,
                "recompute_report": recompute_report,
            }
        ), 400

    if total_api_calls <= 0:
        return jsonify(
            {
                "status": "error",
                "message": "Recompute produced zero API calls. Likely missing stop coordinates in selected buses.",
                "recompute_report": recompute_report,
                "invalid_movements": invalid_movements,
                "zero_api_movements": zero_api_movements,
            }
        ), 400
    if not updated_routes:
        return jsonify(
            {
                "status": "error",
                "message": "Recompute produced no updated routes. Sync aborted; output not written.",
                "recompute_report": recompute_report,
            }
        ), 400

    normalize_terminal_times_in_dataset(output_data)

    output_data["metadata"] = {
        **(output_data.get("metadata") or {}),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(INPUT_FILE),
        "source": "HITL bulk recompute",
        "analysis_engine": "hitl_embedded_kinematics_v1 + speed_physics_v1",
    }
    secure_refreshed = []
    for bi in sorted(target_map.keys()):
        bus = input_data["buses"][bi]
        if is_locked_bus(bus.get("bus_name"), bus.get("reg_no"), locked_ids):
            secured_data = upsert_secure_snapshot(secured_data, input_data, output_data, bi)
            secure_refreshed.append(normalize_locked_id(bus.get("bus_name"), bus.get("reg_no")))
    new_output_count = len(output_data.get("buses") or [])
    # Guardrail: partial sync must never shrink overall output bus inventory.
    if existing_output_count > 0 and len(selected_bus_indices) < len(input_data.get("buses") or []) and new_output_count < existing_output_count:
        return jsonify(
            {
                "status": "error",
                "message": "Safety guard blocked write: partial recompute would shrink output bus inventory.",
                "existing_output_buses": existing_output_count,
                "new_output_buses": new_output_count,
            }
        ), 409
    backup_json_file(OUTPUT_FILE)
    atomic_write_json(OUTPUT_FILE, output_data)
    if secure_refreshed:
        backup_json_file(SECURED_FILE)
        atomic_write_json(SECURED_FILE, secured_data)
    return jsonify(
        {
            "status": "success",
            "updated_routes": updated_routes,
            "recompute_report": recompute_report,
            "secure_refreshed": secure_refreshed,
            "recompute_elapsed_s": recompute_elapsed_s,
            "api_calls": total_api_calls,
            "warnings": {
                "zero_api_movements": zero_api_movements,
                "invalid_movements": invalid_movements,
            },
        }
    )





@app.route("/secure_bus", methods=["POST"])
def secure_bus():
    data = request.get_json(silent=True) or {}
    bus_name = data.get("bus_name")
    reg_no = data.get("reg_no")

    input_data = load_bus_dataset(INPUT_FILE)
    output_data = load_output_for_write(input_data)
    secured_data, _secure_sig = load_secured_data_for_proximity()

    bus_idx, bus, fleet_id = find_bus(input_data, bus_name, reg_no)
    if bus is None:
        return jsonify({"status": "error", "message": f"Bus not found for secure lock: {fleet_id}"}), 404

    already_locked = fleet_id in set(secured_data.get("locked_ids") or [])
    secured_data = upsert_secure_snapshot(secured_data, input_data, output_data, bus_idx)
    backup_json_file(SECURED_FILE)
    atomic_write_json(SECURED_FILE, secured_data)

    return jsonify(
        {
            "status": "success",
            "message": (f"Secure snapshot refreshed for {fleet_id}" if already_locked else f"Secured {fleet_id}"),
            "secure_registry": build_secure_registry(secured_data),
            "locked_ids": secured_data["locked_ids"],
        }
    )


@app.route("/unified_surgical_commit", methods=["POST"])
def unified_surgical_commit():
    data = request.get_json(silent=True) or {}
    staged_stops = data.get("stagedStops", {}) or {}
    staged_trips = data.get("stagedTrips", []) or []
    selected_bus_ids = data.get("selected_bus_ids", []) or []

    input_data = load_bus_dataset(INPUT_FILE)
    existing_output = load_bus_dataset(OUTPUT_FILE)
    existing_output_count = len(existing_output.get("buses") or []) if isinstance(existing_output, dict) else 0
    output_data = load_output_for_write(input_data)
    secured_data, _secure_sig = load_secured_data_for_proximity()
    locked_ids = set(secured_data.get("locked_ids") or [])

    target_map = defaultdict(set)
    touched_bus_indices = set()
    commit_notes = []
    blocked_buses = []
    staged_overrides = _normalize_staged_stop_overrides(staged_stops)

    for snapshot in staged_trips:
        bus_idx_check, bus_check, bus_id_check = find_bus(input_data, snapshot.get("bus_name"), snapshot.get("reg_no"))
        if bus_check is None:
            commit_notes.append({"status": "missing_bus", "fleet_id": bus_id_check})
            continue

        bus_idx, mv_idx, note = apply_trip_snapshot_to_input(input_data, snapshot)
        commit_notes.append(note)
        if bus_idx is not None and mv_idx is not None:
            target_map[bus_idx].add(mv_idx)
            touched_bus_indices.add(bus_idx)

    # Any bus touched by staged trip edits should be fully recomputed (all movements),
    # so output contains complete bus snapshot, not only edited movement fragments.
    for bi in sorted(touched_bus_indices):
        movement_count = len(input_data["buses"][bi].get("movements") or [])
        if bi not in target_map:
            target_map[bi] = set()
        target_map[bi].update(range(movement_count))

    # If selected buses are specified, include full recompute for them unless already narrowed by staged trips.
    selected_bus_indices = parse_selected_bus_ids(selected_bus_ids, input_data)
    for bi in selected_bus_indices:
        movement_count = len(input_data["buses"][bi].get("movements") or [])
        if bi not in target_map:
            target_map[bi] = set()
        # Always recompute full bus when it is selected for commit sync.
        target_map[bi].update(range(movement_count))

    # Apply staged stop overrides to targeted buses (drag/rename truth source),
    # even when some trip snapshots have incomplete marker coordinates.
    override_targets = set(target_map.keys()) | set(selected_bus_indices)
    if staged_overrides and not override_targets:
        # Fallback: if only staged stop edits were provided, scan all unlocked buses.
        override_targets = set(range(len(input_data.get("buses", []) or [])))
    override_summary = []
    for bi in sorted(override_targets):
        bus = input_data["buses"][bi]
        updates = apply_staged_stop_overrides_to_bus(bus, staged_overrides)
        if updates <= 0:
            continue
        movement_count = len(bus.get("movements") or [])
        if bi not in target_map:
            target_map[bi] = set()
        target_map[bi].update(range(movement_count))
        touched_bus_indices.add(bi)
        override_summary.append(
            {
                "fleet_id": normalize_bus_identity(bus.get("bus_name"), bus.get("reg_no"))[2],
                "updates": updates,
            }
        )
    if override_summary:
        commit_notes.append({"status": "staged_stop_overrides_applied", "buses": override_summary})

    if not target_map:
        return jsonify({"status": "error", "message": "No valid staged targets to recompute.", "commit_notes": commit_notes, "blocked_buses": sorted(set(blocked_buses))}), 400

    if hasattr(router, "route_result_cache") and isinstance(router.route_result_cache, dict):
        router.route_result_cache.clear()

    t0 = time.perf_counter()
    updated_routes, recompute_report = recompute_for_targets(input_data, output_data, target_map)
    recompute_elapsed_s = round(time.perf_counter() - t0, 2)
    total_api_calls = sum(
        int((m.get("api_calls") or 0))
        for b in recompute_report
        for m in (b.get("movements") or [])
    )
    zero_api_movements = collect_zero_api_movements(recompute_report)
    invalid_movements = collect_status_movements(recompute_report, "invalid")

    if zero_api_movements:
        return jsonify(
            {
                "status": "error",
                "message": "Sync aborted: one or more movements did not hit Routes API.",
                "zero_api_movements": zero_api_movements,
                "recompute_report": recompute_report,
                "commit_notes": commit_notes,
                "blocked_buses": sorted(set(blocked_buses)),
            }
        ), 400

    if total_api_calls <= 0:
        return jsonify(
            {
                "status": "error",
                "message": "Sync produced zero API calls. Likely missing stop coordinates in selected buses.",
                "recompute_report": recompute_report,
                "invalid_movements": invalid_movements,
                "zero_api_movements": zero_api_movements,
                "commit_notes": commit_notes,
                "blocked_buses": sorted(set(blocked_buses)),
            }
        ), 400
    if not updated_routes:
        return jsonify(
            {
                "status": "error",
                "message": "Sync produced no updated routes. Commit aborted; output not written.",
                "recompute_report": recompute_report,
                "commit_notes": commit_notes,
                "blocked_buses": sorted(set(blocked_buses)),
            }
        ), 400

    normalize_terminal_times_in_dataset(input_data)
    normalize_terminal_times_in_dataset(output_data)

    now_iso = datetime.now().isoformat(timespec="seconds")
    input_meta = input_data.get("metadata") if isinstance(input_data.get("metadata"), dict) else {}
    input_meta["updated_at"] = now_iso
    input_meta["source"] = "HITL commit"
    input_data["metadata"] = input_meta

    output_meta = output_data.get("metadata") if isinstance(output_data.get("metadata"), dict) else {}
    output_meta["generated_at"] = now_iso
    output_meta["source_file"] = str(INPUT_FILE)
    output_meta["source"] = "HITL sync"
    output_meta["analysis_engine"] = "hitl_embedded_kinematics_v1 + speed_physics_v1"
    output_data["metadata"] = output_meta
    secure_refreshed = []
    for bi in sorted(target_map.keys()):
        bus = input_data["buses"][bi]
        if is_locked_bus(bus.get("bus_name"), bus.get("reg_no"), locked_ids):
            secured_data = upsert_secure_snapshot(secured_data, input_data, output_data, bi)
            secure_refreshed.append(normalize_locked_id(bus.get("bus_name"), bus.get("reg_no")))

    new_output_count = len(output_data.get("buses") or [])
    partial_commit = len(target_map) < len(input_data.get("buses") or [])
    # Guardrail: targeted sync must not reduce full output inventory.
    if existing_output_count > 0 and partial_commit and new_output_count < existing_output_count:
        return jsonify(
            {
                "status": "error",
                "message": "Safety guard blocked write: targeted sync would shrink output bus inventory.",
                "existing_output_buses": existing_output_count,
                "new_output_buses": new_output_count,
                "commit_notes": commit_notes,
                "blocked_buses": sorted(set(blocked_buses)),
            }
        ), 409

    backup_json_file(INPUT_FILE)
    backup_json_file(OUTPUT_FILE)
    atomic_write_json(INPUT_FILE, input_data)
    atomic_write_json(OUTPUT_FILE, output_data)
    if secure_refreshed:
        backup_json_file(SECURED_FILE)
        atomic_write_json(SECURED_FILE, secured_data)

    return jsonify(
        {
            "status": "success",
            "sync_label": "PHITL",
            "updated_routes": updated_routes,
            "recompute_report": recompute_report,
            "truth_bank": {},
            "secure_registry": build_secure_registry(secured_data),
            "secure_refreshed": secure_refreshed,
            "blocked_buses": sorted(set(blocked_buses)),
            "recompute_elapsed_s": recompute_elapsed_s,
            "api_calls": total_api_calls,
            "commit_notes": commit_notes,
            "warnings": {
                "zero_api_movements": zero_api_movements,
                "invalid_movements": invalid_movements,
            },
        }
    )


def save_precomputed_cache(payload, sig):
    """Saves the hot cache to disk for instant restart."""
    try:
        cache_bundle = {
            "sig": sig,
            "payload": payload,
            "saved_at": datetime.now().isoformat()
        }
        atomic_write_json(PRECOMPUTED_CACHE_FILE, cache_bundle)
        print(f"[CACHE] Saved precomputed cache to {PRECOMPUTED_CACHE_FILE.name}", flush=True)
    except Exception as e:
        print(f"[CACHE ERROR] Failed to save: {e}", flush=True)

def load_precomputed_cache():
    """Loads cache from disk if it exists and matches current signatures."""
    global _GET_CACHE_DATA_CACHE
    if not PRECOMPUTED_CACHE_FILE.exists():
        return False
    
    try:
        bundle = load_json(PRECOMPUTED_CACHE_FILE, None)
        if bundle and "sig" in bundle and "payload" in bundle:
            # We don't check signature strictly on boot so it's instant, 
            # but background warmup will eventually overwrite it if stale.
            with _GET_CACHE_DATA_LOCK:
                _GET_CACHE_DATA_CACHE["sig"] = bundle["sig"]
                _GET_CACHE_DATA_CACHE["payload"] = bundle["payload"]
                _GET_CACHE_DATA_CACHE["built_at"] = bundle.get("saved_at")
            print(f"[CACHE] Restored precomputed cache (saved at {bundle.get('saved_at')})", flush=True)
            return True
    except Exception as e:
        print(f"[CACHE ERROR] Failed to load: {e}", flush=True)
    return False

def background_warmup():
    """
    Heavy synchronous work performed in background to keep startup instant.
    Progressively warms up the cache.
    """
    global WARMUP_STATE, _GET_CACHE_DATA_CACHE
    WARMUP_STATE["started"] = True
    print("[WARMUP] Starting background hydration...", flush=True)

    try:
        # Phase 1: Backfills
        WARMUP_STATE["phase"] = "terminal_time_backfill"
        ensure_terminal_time_backfill()
        WARMUP_STATE["progress"] = 20

        WARMUP_STATE["phase"] = "embedded_kinematics"
        ensure_embedded_kinematics_backfill()
        WARMUP_STATE["progress"] = 40

        WARMUP_STATE["phase"] = "tt_embedded"
        ensure_tt_embedded_backfill()
        WARMUP_STATE["progress"] = 60

        # Phase 2: Proximity Prewarm
        WARMUP_STATE["phase"] = "proximity_engine"
        load_secured_data_for_proximity()
        WARMUP_STATE["progress"] = 80

        # Phase 3: Build Final Cache
        WARMUP_STATE["phase"] = "final_cache"
        # We trigger a dummy get_cache internal logic build
        # This will populate _GET_CACHE_DATA_CACHE
        # (We basically need the logic from get_cache here without the Flask context)
        dataset, output_dataset, route_source = load_route_view_dataset()
        tt_output = load_bus_dataset(TT_OUTPUT_FILE)
        
        for tt_bus in tt_output.get("buses", []) or []:
            if not isinstance(tt_bus, dict): continue
            _, view_bus, _ = find_bus(dataset, tt_bus.get("bus_name"), tt_bus.get("reg_no"))
            if view_bus is None: continue
            _overlay_tt_times(view_bus, tt_bus)
            enrich_bus_kinematics(view_bus)

        routes = dataset_to_verified_routes(dataset)
        output_routes = dataset_to_verified_routes(output_dataset)
        secured_data = load_secured_data()
        secure_registry = build_secure_registry(secured_data)
        secure_ids = set(secure_registry.keys()) | set(secured_data.get("locked_ids") or [])
        polyline_output = load_bus_dataset(OUTPUT_FILE)
        polyline_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (polyline_output.get("buses") or [])}
        tt_ids = {normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))[2] for b in (tt_output.get("buses") or [])}

        hitl_status = {}
        for b in dataset.get("buses") or []:
            _, _, b_id = normalize_bus_identity(b.get("bus_name"), b.get("reg_no"))
            if b_id in secure_ids: hitl_status[b_id] = "SECURE"
            elif b_id in tt_ids: hitl_status[b_id] = "TTHITL"
            elif b_id in polyline_ids: hitl_status[b_id] = "PHITL"
            else: hitl_status[b_id] = "INPUT"

        hitl_storage = buses_to_hitl_storage(output_routes)
        payload = {
            "status": "success",
            "hitl_status": hitl_status,
            "warmup_ready": True,
            "secure_bus_ids": sorted(list(secure_ids)),
            "tt_hitl_ids": sorted(list(tt_ids)),
            "polyline_hitl_ids": sorted(list(polyline_ids)),
            "secure_registry": secure_registry,
            "verified_routes": routes,
            "secure_verified_routes": dataset_to_verified_routes(secured_data),
            "route_source": route_source,
            "metadata": dataset.get("metadata") if isinstance(dataset, dict) else {},
            "hitl_storage": hitl_storage,
        }

        sig = _get_cache_signature()
        with _GET_CACHE_DATA_LOCK:
            _GET_CACHE_DATA_CACHE["sig"] = sig
            _GET_CACHE_DATA_CACHE["payload"] = payload
            _GET_CACHE_DATA_CACHE["built_at"] = datetime.now().isoformat(timespec="seconds")

        save_precomputed_cache(payload, sig)

        WARMUP_STATE["ready"] = True
        WARMUP_STATE["phase"] = "ready"
        WARMUP_STATE["progress"] = 100
        print("[WARMUP] Complete. Cache is hot.", flush=True)

    except Exception as e:
        WARMUP_STATE["error"] = str(e)
        print(f"[WARMUP ERROR] {e}", flush=True)

if __name__ == "__main__":
    print("Starting Flask server...")
    
    # Try to load previous cache for instant availability
    load_precomputed_cache()
    
    # Start background warmup
    threading.Thread(target=background_warmup, daemon=True).start()
    
    print("Background warmup started. Server will be responsive immediately.")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
