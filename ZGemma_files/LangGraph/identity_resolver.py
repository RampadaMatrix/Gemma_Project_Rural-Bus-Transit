import json
import os
import re
from typing import Any, Dict, List, Optional


OCR_REG_TRANSLATION = str.maketrans({
    "I": "1",
    "L": "1",
    "O": "0",
    "Q": "0",
})


def normalize_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def normalize_reg(value: Any) -> str:
    token = normalize_token(value)
    if not token or token.startswith("UNKNOWN"):
        return ""
    return token


def reg_variants(value: Any) -> set:
    token = normalize_reg(value)
    if not token:
        return set()
    return {token, token.translate(OCR_REG_TRANSLATION)}


def bus_label(bus: Dict[str, Any]) -> str:
    name = str(bus.get("bus_name") or bus.get("name") or "UNKNOWN_BUS").strip()
    reg = str(bus.get("reg_no") or "").strip()
    return f"{name} ({reg})" if reg else name


def extract_buses_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("buses"), list):
        return [b for b in payload["buses"] if isinstance(b, dict)]
    if isinstance(payload, dict) and ("bus_name" in payload or "movements" in payload):
        return [payload]
    if isinstance(payload, list):
        buses: List[Dict[str, Any]] = []
        for item in payload:
            buses.extend(extract_buses_from_payload(item))
        return buses
    return []


def stop_sequence(bus: Dict[str, Any]) -> List[str]:
    seq: List[str] = []
    for movement in bus.get("movements") or []:
        if not isinstance(movement, dict):
            continue
        origin = normalize_token(movement.get("origin"))
        if origin:
            seq.append(origin)
        for stop in movement.get("stops") or []:
            if isinstance(stop, dict):
                token = normalize_token(stop.get("name"))
                if token:
                    seq.append(token)
        destination = normalize_token(movement.get("destination"))
        if destination:
            seq.append(destination)

    seen = set()
    unique_seq = []
    for token in seq:
        if token not in seen:
            unique_seq.append(token)
            seen.add(token)
    return unique_seq


def _is_subsequence(left: List[str], right: List[str]) -> bool:
    if not left:
        return False
    right_iter = iter(right)
    return all(any(item == candidate for candidate in right_iter) for item in left)


def route_similarity(left: List[str], right: List[str]) -> float:
    if not left or not right:
        return 0.0
    if _is_subsequence(left, right) or _is_subsequence(right, left):
        return 1.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / (max(len(left_set), len(right_set)) or 1)


def _load_json(path: str) -> Any:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return json.load(handle)


def _buses_from_locked_ids(locked_ids: Any) -> List[Dict[str, Any]]:
    buses: List[Dict[str, Any]] = []
    if not isinstance(locked_ids, list):
        return buses
    for item in locked_ids:
        if not isinstance(item, str):
            continue
        match = re.match(r"\s*(.*?)\s*\(([^()]+)\)\s*$", item)
        if not match:
            continue
        buses.append({"bus_name": match.group(1).strip(), "reg_no": match.group(2).strip()})
    return buses


def load_registry_buses(registry_paths: List[str]) -> List[Dict[str, Any]]:
    buses: List[Dict[str, Any]] = []
    for path in registry_paths:
        try:
            data = _load_json(path)
        except Exception:
            continue
        buses.extend(extract_buses_from_payload(data))
        if isinstance(data, dict):
            buses.extend(_buses_from_locked_ids(data.get("locked_ids")))
    return buses


ACTIVE_AUDIT_STATUSES = {
    "STAGE_1_PENDING",
    "WAITING_FOR_HITL",
    "DISCOVERY_PENDING",
}


def _bus_matches(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_regs = reg_variants(left.get("reg_no"))
    right_regs = reg_variants(right.get("reg_no"))
    if left_regs and right_regs and left_regs & right_regs:
        return True

    left_name = normalize_token(left.get("bus_name") or left.get("name"))
    right_name = normalize_token(right.get("bus_name") or right.get("name"))
    if not left_name or left_name != right_name:
        return False

    left_seq = stop_sequence(left)
    right_seq = stop_sequence(right)
    if not left_seq or not right_seq:
        return True
    return route_similarity(left_seq, right_seq) >= 0.70


def resolve_active_audit_state(new_bus: Dict[str, Any], project_root: str) -> Dict[str, Any]:
    """Return active Stage-1/HITL state for a bus that should not be called a failed duplicate."""
    root = os.path.abspath(project_root)
    stage_path = os.path.join(root, "Polyline_Drawing_Pipeline", "Stage_1_data.json")
    state_path = os.path.join(root, "pipeline_state.json")

    try:
        stage_buses = extract_buses_from_payload(_load_json(stage_path))
    except Exception:
        stage_buses = []

    for queued_bus in stage_buses:
        if _bus_matches(new_bus, queued_bus):
            return {
                "action": "audit_active",
                "status": "QUEUED_FOR_STAGE_1",
                "reason": "stage_1_queue_match",
                "matched_bus": queued_bus,
            }

    try:
        state_data = _load_json(state_path)
    except Exception:
        state_data = None

    state_buses = state_data.get("buses") if isinstance(state_data, dict) else {}
    if not isinstance(state_buses, dict):
        return {"action": "none", "status": "", "reason": "no_active_audit_state", "matched_bus": None}

    new_regs = reg_variants(new_bus.get("reg_no"))
    new_name = normalize_token(new_bus.get("bus_name") or new_bus.get("name"))

    for reg, info in state_buses.items():
        if not isinstance(info, dict):
            continue
        status = str(info.get("status") or "").strip().upper()
        if status not in ACTIVE_AUDIT_STATUSES:
            continue

        state_bus = {
            "bus_name": info.get("name"),
            "reg_no": reg,
        }
        state_regs = reg_variants(reg)
        state_name = normalize_token(info.get("name"))
        if (new_regs and state_regs and new_regs & state_regs) or (new_name and state_name == new_name):
            return {
                "action": "audit_active",
                "status": status,
                "reason": "pipeline_state_match",
                "matched_bus": state_bus,
            }

    return {"action": "none", "status": "", "reason": "no_active_audit_state", "matched_bus": None}


def active_audit_message(bus: Dict[str, Any], audit_state: Dict[str, Any]) -> str:
    label = bus_label(bus)
    status = str(audit_state.get("status") or "").upper()
    if status == "WAITING_FOR_HITL":
        return f"{label} is already ready for Human Audit. No duplicate Stage-1 enqueue was needed."
    if status == "DISCOVERY_PENDING":
        return f"{label} has already been secured in HITL and is rebuilding discovery data."
    return f"{label} is already queued in the Stage-1/HITL audit flow. No duplicate enqueue was needed."


def resolve_bus_identity(new_bus: Dict[str, Any], registry_buses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministically decide whether an extracted bus already exists."""
    new_reg_variants = reg_variants(new_bus.get("reg_no"))
    new_name = normalize_token(new_bus.get("bus_name") or new_bus.get("name"))
    new_seq = stop_sequence(new_bus)

    if new_reg_variants:
        for existing in registry_buses:
            if new_reg_variants & reg_variants(existing.get("reg_no")):
                return {
                    "action": "duplicate",
                    "confidence": 1.0,
                    "reason": "registration_match",
                    "matched_bus": existing,
                }

    same_name_candidates = [
        existing
        for existing in registry_buses
        if new_name and normalize_token(existing.get("bus_name") or existing.get("name")) == new_name
    ]

    best_match: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for existing in same_name_candidates:
        score = route_similarity(new_seq, stop_sequence(existing))
        if score > best_score:
            best_score = score
            best_match = existing

    if best_match and best_score >= 0.70:
        return {
            "action": "duplicate",
            "confidence": round(best_score, 3),
            "reason": "name_route_match",
            "matched_bus": best_match,
        }

    if same_name_candidates and not new_seq and len(same_name_candidates) == 1:
        return {
            "action": "duplicate",
            "confidence": 0.75,
            "reason": "unique_name_match_without_route",
            "matched_bus": same_name_candidates[0],
        }

    return {
        "action": "new",
        "confidence": 0.0,
        "reason": "no_registry_match",
        "matched_bus": None,
    }


def find_duplicate_bus(new_bus: Dict[str, Any], registry_paths: List[str]) -> Optional[Dict[str, Any]]:
    result = resolve_bus_identity(new_bus, load_registry_buses(registry_paths))
    return result if result.get("action") == "duplicate" else None
