import os
import requests
import json
import math
import time
import copy
import re
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# --- API KEY MANAGEMENT ---
def load_api_key():
    env_key = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY")
    if env_key:
        return env_key
    
    # Try to load from ASecrets/secrets_config.py
    try:
        root_dir = Path(__file__).resolve().parent.parent
        secrets_dir = root_dir / "ASecrets"
        if str(secrets_dir) not in sys.path:
            sys.path.append(str(secrets_dir))
        from secrets_config import GOOGLE_MAPS_API_KEY # type: ignore
        return GOOGLE_MAPS_API_KEY
    except (ImportError, AttributeError):
        return ""

API_KEY = load_api_key()
if not API_KEY or "PASTE_YOUR_API_KEY_HERE" in API_KEY:
    raise RuntimeError("Missing Google API key. Please set GOOGLE_MAPS_API_KEY in your environment or update secrets_config.py in QNew_Stage_1_data.")

MAJOR_HUBS = {
    "Purulia": (23.330910, 86.361153),
    "Lalpur": (23.306109, 86.632902),
    "Hura": (23.305957, 86.651702),
    "Bankura": (23.241948, 87.033408),
    "Simulia": (23.301649, 86.357604),
    "Chakaltore": (23.241611, 86.353913),
    "Raghunathpur": (23.535333, 86.661074),
    "Barabazar": (23.028380, 86.370578),
    "Durgapur (Station)": (23.5135, 87.3151),
    "Durgapur Bus Stand": (23.494028, 87.316176),
    "Medinipur": (22.427865, 87.315685),
    "Manbazar Bus Stand": (23.056700, 86.653099),
    "Manbazar": (23.056700, 86.653099),
    "Jhargram": (22.4533, 86.9859),
    "Jhalda": (23.3644, 85.9734),
    "Kharbona": (23.33756, 86.92149),
    "Kharbana": (23.33756, 86.92149),
    "Chas": (23.4099731, 86.2263281),
    "Chas Mor": (23.4099731, 86.2263281),
    "Lakshanpur": (23.340218, 86.568819),
    "Gopalnagar": (23.131627, 86.591655),
    "Rampur": (23.322855, 87.368153),
    "Bhairabdanga": (23.325370, 87.314653),
    "shanka": (23.517385, 86.623179),
    "Dhadka": (22.793061, 86.506643),
    "ramgarh": (23.630057, 85.513386),
    "sidri" : (23.042373, 86.495481),
    "Arrah" : (23.365298, 86.871536),
    "Nadiha" : (23.495223, 86.403008),
    "Surulya" : (23.301649, 86.357604),
    "Kaluhar" : (23.301649, 86.357604),
    "Hatirampur":(23.062051808863664, 86.87854035928439),
    "Bardhaman":(23.252366478512247, 87.86590479553794),
    "Dhanbad":(23.79355037278209, 86.42755004456703),
    



}

VERIFIED_TRANSIT_ANCHORS = {
    # Frequently ambiguous WB towns where text search can return a school,
    # village, or homonymous locality far away from the bus corridor.
    "Haldia": (22.066115, 88.076968),
    "Mecheda": (22.399358, 87.858424),
    "Mechegram": (22.409063, 87.749017),
    "Ghatal": (22.662390, 87.733990),
    "Chandrakona Town": (22.731520, 87.516090),
    "Garbeta": (22.862900, 87.354594),
    "Bishnupur": (23.073550, 87.319910),
}

class PuruliaTransitRouter:
    """
    Implements the Automated Transit Engine (Viterbi HMM).
    - Uses dynamic programming to find globally optimal coordinate sequences.
    - Sliding Anchors with Hijack Protection to discard extreme outliers.
    - Vector-Shifted Biasing to mathematically look ahead toward the next hub.
    - Uses TRAFFIC_AWARE TWO_WHEELER routing with strict 'via: True'.
    """
    def __init__(self, api_key, cache_file=None, offline=True):
        self.api_key = api_key
        self.places_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        self.routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        self.cache_file = cache_file
        self.offline = offline
        self.coordinate_cache = self.load_verified_cache()
        self.route_result_cache = {}
        self.stop_aliases = self._build_stop_aliases()


    def _build_stop_aliases(self):
        aliases = {}
        for hub_name in list(MAJOR_HUBS.keys()) + list(VERIFIED_TRANSIT_ANCHORS.keys()):
            key = self._normalize_stop_key(hub_name)
            aliases[key] = hub_name.strip()
        aliases.update({
            "kharbana": "Kharbona",
            "kharbona": "Kharbona",
            "chas mor": "Chas",
            "manbazar bus stand": "Manbazar",
            "durgapur bus stand": "Durgapur (Station)",
        })
        return aliases

    def _normalize_stop_key(self, stop_name):
        text = str(stop_name or "").strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text

    def _canonicalize_stop_name(self, stop_name):
        key = self._normalize_stop_key(stop_name)
        if not key:
            return ""
        return self.stop_aliases.get(key, str(stop_name).strip())

    def _contains_purulia(self, stop_names):
        return any(self._normalize_stop_key(name) == "purulia" for name in (stop_names or []))

    def _normalize_direction(self, direction, origin, destination, is_purulia_centric):
        d = str(direction or "").strip()
        d_up = d.upper()
        if d_up in ("UP", "DOWN"):
            return {"ok": is_purulia_centric, "canonical": d_up, "type": "ud"}
        if d.lower().startswith("towards "):
            target = d[8:].strip()
            if is_purulia_centric:
                return {"ok": False, "canonical": None, "type": "towards"}
            if self._normalize_stop_key(target) != self._normalize_stop_key(destination):
                return {"ok": False, "canonical": None, "type": "towards"}
            return {"ok": True, "canonical": f"TOWARDS:{self._normalize_stop_key(target)}", "type": "towards"}
        return {"ok": False, "canonical": None, "type": "unknown"}

    def _build_route_signature(self, marker_records):
        parts = [self._normalize_stop_key((m or {}).get("name")) for m in (marker_records or [])]
        parts = [p for p in parts if p]
        return "|".join(parts)

    def _clone_route_result(self, route_result):
        return copy.deepcopy(route_result)

    def _reverse_cached_result(self, route_result):
        if not route_result:
            return None
        cloned = self._clone_route_result(route_result)
        if cloned.get("polyline"):
            points = self._decode_polyline(cloned["polyline"])
            points.reverse()
            cloned["polyline"] = self._encode_polyline(points)
        nodes = list(cloned.get("nodes") or [])
        nodes.reverse()
        for node in nodes:
            flags = list(node.get("flags") or [])
            if "reverse_reuse" not in flags:
                flags.append("reverse_reuse")
            node["flags"] = flags
        cloned["nodes"] = nodes
        cloned["reused_reverse"] = True
        return cloned

    def _normalize_marker_records(self, stop_names):
        raw_marker_records = []
        if stop_names and isinstance(stop_names[0], dict):
            raw_marker_records = [dict(m or {}) for m in stop_names if str((m or {}).get("name") or "").strip()]
        else:
            raw_marker_records = [{"name": str(name).strip()} for name in (stop_names or []) if str(name).strip()]

        normalized_marker_records = []
        for marker in raw_marker_records:
            marker_meta = dict(marker)
            clean_name = self._canonicalize_stop_name(marker_meta.get("name"))
            if not clean_name:
                continue
            marker_meta["name"] = clean_name
            if normalized_marker_records and self._normalize_stop_key(normalized_marker_records[-1]["name"]) == self._normalize_stop_key(clean_name):
                continue
            normalized_marker_records.append(marker_meta)
        return normalized_marker_records

    def validate_movement(self, movement, all_movements=None, movement_index=0):
        all_movements = all_movements or []
        movement = movement or {}
        stops = movement.get("stops") or []
        stop_names = [self._canonicalize_stop_name((s or {}).get("name")) for s in stops if self._canonicalize_stop_name((s or {}).get("name"))]
        origin = self._canonicalize_stop_name(movement.get("origin"))
        destination = self._canonicalize_stop_name(movement.get("destination"))
        direction = movement.get("direction")
        issues = []

        if len(stop_names) < 2:
            issues.append("Movement has fewer than 2 valid stops.")
        if stop_names:
            if origin and self._normalize_stop_key(origin) != self._normalize_stop_key(stop_names[0]):
                issues.append(f"Origin mismatch: '{origin}' != first stop '{stop_names[0]}'")
            if destination and self._normalize_stop_key(destination) != self._normalize_stop_key(stop_names[-1]):
                issues.append(f"Destination mismatch: '{destination}' != last stop '{stop_names[-1]}'")

        if movement_index > 0 and movement_index < len(all_movements):
            prev_dest = self._canonicalize_stop_name((all_movements[movement_index - 1] or {}).get("destination"))
            if prev_dest and origin and self._normalize_stop_key(prev_dest) != self._normalize_stop_key(origin):
                issues.append(f"Continuity mismatch: previous destination '{prev_dest}' != current origin '{origin}'")

        purulia_centric = self._contains_purulia(stop_names)
        d_info = self._normalize_direction(direction, origin, destination, purulia_centric)
        if not d_info["ok"]:
            if purulia_centric:
                issues.append("Purulia-centric movement must use UP/DOWN.")
            else:
                issues.append("Non-Purulia movement must use 'towards <Destination>'.")
        elif d_info["type"] == "ud":
            if d_info["canonical"] == "UP" and self._normalize_stop_key(destination) != "purulia":
                issues.append("UP movement must end at Purulia.")
            if d_info["canonical"] == "DOWN" and self._normalize_stop_key(origin) != "purulia":
                issues.append("DOWN movement must start from Purulia.")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "direction_canonical": d_info.get("canonical"),
            "is_purulia_centric": purulia_centric,
            "normalized_stops": stop_names
        }

    def compute_verified_movement_route(self, movement, all_movements=None, movement_index=0, bus_reg=None):
        all_movements = all_movements or []
        validation = self.validate_movement(movement, all_movements=all_movements, movement_index=movement_index)
        if not validation["ok"]:
            print(f"      [VALIDATION] Movement rejected: {validation['issues']}", flush=True)
            return {
                "polyline": "",
                "distanceMeters": 0,
                "nodes": [],
                "validation": validation
            }

        endpoint_popularity = {}
        for mv in all_movements:
            o = self._canonicalize_stop_name((mv or {}).get("origin"))
            d = self._canonicalize_stop_name((mv or {}).get("destination"))
            if o:
                endpoint_popularity[self._normalize_stop_key(o)] = int(endpoint_popularity.get(self._normalize_stop_key(o), 0)) + 1
            if d:
                endpoint_popularity[self._normalize_stop_key(d)] = int(endpoint_popularity.get(self._normalize_stop_key(d), 0)) + 1

        stop_records = []
        for s in (movement.get("stops") or []):
            rec = dict(s or {})
            rec["__direction"] = movement.get("direction")
            rec["__origin"] = movement.get("origin")
            rec["__destination"] = movement.get("destination")
            rec["__trip_id"] = movement.get("trip_id")
            rec["__endpoint_popularity"] = endpoint_popularity
            stop_records.append(rec)
        result = self.compute_verified_route(stop_records)
        if result is None:
            return {"polyline": "", "distanceMeters": 0, "nodes": [], "validation": validation}
        result["validation"] = validation
        return result

    def _movement_sequence_for_signature(self, movement):
        movement = movement or {}
        seq = []
        for s in (movement.get("stops") or []):
            # SKIP WAYPOINTS to ensure service-path matching
            if s.get("isWaypoint") is True:
                continue
            
            raw_name = str(s.get("name") or "").strip().lower()
            if raw_name.startswith("wp") or "wp_" in raw_name:
                continue

            name = self._canonicalize_stop_name(s.get("name"))
            if not name:
                continue
            key = self._normalize_stop_key(name)
            if seq and seq[-1] == key:
                continue
            seq.append(key)
        
        if seq:
            return seq
        
        # Fallback to origin-destination only if stops array is empty or purely waypoints
        origin = self._normalize_stop_key(self._canonicalize_stop_name(movement.get("origin")))
        destination = self._normalize_stop_key(self._canonicalize_stop_name(movement.get("destination")))
        return [x for x in [origin, destination] if x]

    def get_unique_movement_plan(self, bus_record):
        bus_record = bus_record or {}
        movements = list(bus_record.get("movements") or [])
        def representative_score(movement):
            movement = movement or {}
            route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
            nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
            node_count = len(nodes)
            poly_ok = 1 if route.get("polyline") else 0
            valid_stop_count = len([s for s in (movement.get("stops") or []) if self._canonicalize_stop_name((s or {}).get("name"))])
            conf_vals = [
                float(n.get("confidence"))
                for n in nodes
                if isinstance(n, dict) and isinstance(n.get("confidence"), (int, float))
            ]
            avg_conf = (sum(conf_vals) / len(conf_vals)) if conf_vals else 0.0
            # Higher is better: keep richer movement shapes first.
            return (valid_stop_count, node_count, poly_ok, avg_conf)

        groups = {}
        order = []
        for idx, movement in enumerate(movements):
            seq = self._movement_sequence_for_signature(movement)
            if len(seq) < 2:
                key = f"__raw__{idx}"
            else:
                fwd = ">".join(seq)
                rev = ">".join(reversed(seq))
                key = rev if rev < fwd else fwd
            if key not in groups:
                groups[key] = {
                    "representative_idx": idx,
                    "representative_trip_id": movement.get("trip_id"),
                    "representative_score": representative_score(movement),
                    "member_indices": [],
                    "member_trip_ids": [],
                }
                order.append(key)
            else:
                cur_score = representative_score(movement)
                rep_score = groups[key]["representative_score"]
                # Prefer higher-quality representative; tie-breaker is earliest index.
                if cur_score > rep_score:
                    groups[key]["representative_idx"] = idx
                    groups[key]["representative_trip_id"] = movement.get("trip_id")
                    groups[key]["representative_score"] = cur_score
            groups[key]["member_indices"].append(idx)
            groups[key]["member_trip_ids"].append(movement.get("trip_id"))

        reps = [groups[k]["representative_idx"] for k in order]
        duplicate_groups = []
        for k in order:
            g = groups[k]
            if len(g["member_indices"]) > 1:
                dropped_pairs = [
                    (idx, trip_id)
                    for idx, trip_id in zip(g["member_indices"], g["member_trip_ids"])
                    if idx != g["representative_idx"]
                ]
                duplicate_groups.append({
                    "signature": k,
                    "representative_idx": g["representative_idx"],
                    "representative_trip_id": g["representative_trip_id"],
                    "representative_score": g["representative_score"],
                    "dropped_indices": [p[0] for p in dropped_pairs],
                    "dropped_trip_ids": [p[1] for p in dropped_pairs],
                })
        return {
            "representative_indices": reps,
            "groups": groups,
            "duplicate_groups": duplicate_groups,
            "original_movement_count": len(movements),
            "unique_movement_count": len(reps),
            "deduped_movement_count": max(len(movements) - len(reps), 0),
        }

    def compute_bus_movement_routes(self, bus_record):
        bus_record = bus_record or {}
        movements = list(bus_record.get("movements") or [])
        plan = self.get_unique_movement_plan(bus_record)
        
        # 1. Route only the representative of each unique group
        unique_results = {}
        for k, group in plan["groups"].items():
            idx = group["representative_idx"]
            movement = movements[idx]
            
            actual_sig = ">".join(self._movement_sequence_for_signature(movement))
            log_sig = f"'{actual_sig}'"
            if actual_sig != k:
                log_sig += f" (Group Key: {k})"

            print(f"      - [SMART] Routing representative {idx} for signature: {log_sig}", flush=True)
            route_res = self.compute_verified_movement_route(
                movement,
                all_movements=movements,
                movement_index=idx,
                bus_reg=bus_record.get("reg_no")
            )
            unique_results[k] = {
                "res": route_res,
                "fwd": actual_sig,
                "rep_trip_id": movement.get("trip_id")
            }

        # 2. Re-hydrate all movements using the Handicap Protocol
        final_movements = []
        for idx, movement in enumerate(movements):
            trip_id = movement.get("trip_id")
            seq = self._movement_sequence_for_signature(movement)
            fwd = ">".join(seq)
            rev = ">".join(reversed(seq))
            key = rev if rev < fwd else fwd
            
            group_data = unique_results.get(key)
            if not group_data:
                final_movements.append(movement)
                continue
            
            is_rep = (group_data["rep_trip_id"] == trip_id)
            
            # --- HANDICAP LOGIC ---
            if is_rep:
                # Master trip keeps full data
                route_copy = copy.deepcopy(group_data["res"])
                route_copy["master_geometry"] = True
            else:
                # Handicapped trip REUSES the polyline from the master!
                # We do NOT set it to None anymore to prevent UI straight-line fallbacks.
                rep_res = group_data["res"]
                route_copy = copy.deepcopy(rep_res)
                route_copy["master_geometry"] = False
                route_copy["redundant_of"] = group_data["rep_trip_id"]
                route_copy["signature"] = key
                
                # If the sequence is mathematically reversed from the master, reverse the nodes AND polyline
                if fwd != group_data["fwd"]:
                    route_copy["nodes"].reverse()
                    route_copy["reused_reverse"] = True
                    
                    # Reverse the polyline string itself!
                    if route_copy.get("polyline"):
                        pts = self._decode_polyline(route_copy["polyline"])
                        pts.reverse()
                        route_copy["polyline"] = self._encode_polyline(pts)
                        
                    for node in route_copy["nodes"]:
                        if "flags" not in node:
                            node["flags"] = []
                        if "reverse_reuse" not in node["flags"]:
                            node["flags"].append("reverse_reuse")
            
            route_copy["validation"] = self.validate_movement(
                movement, 
                all_movements=movements, 
                movement_index=idx
            )
            
            final_movements.append({
                "trip_id": trip_id,
                "direction": movement.get("direction"),
                "origin": movement.get("origin"),
                "destination": movement.get("destination"),
                "stops": movement.get("stops") or [],
                "route": route_copy
            })
            
        return final_movements, plan

    def _get_hub_match(self, stop_name):
        clean_name = self._normalize_stop_key(self._canonicalize_stop_name(stop_name))
        for hub_name, coords in MAJOR_HUBS.items():
            if self._normalize_stop_key(hub_name) == clean_name:
                return coords
        for anchor_name, coords in VERIFIED_TRANSIT_ANCHORS.items():
            if self._normalize_stop_key(anchor_name) == clean_name:
                return coords
        return None

    def _is_hub(self, stop_name):
        return self._get_hub_match(stop_name) is not None

    def _haversine(self, lat1, lon1, lat2, lon2):
        R = 6371.0 # km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    def _parse_clock_time(self, time_str):
        if not time_str:
            return None
        try:
            dt = datetime.strptime(str(time_str).strip(), "%I:%M %p")
            return dt.hour * 60 + dt.minute
        except Exception:
            return None

    def _elapsed_hours(self, start_min, end_min):
        if start_min is None or end_min is None:
            return None
        delta = end_min - start_min
        if delta < 0:
            delta += 24 * 60
        if delta <= 0:
            return None
        return delta / 60.0

    def _apply_temporal_feasibility_filter(
        self,
        candidates,
        prev_anchor=None,
        prev_time_min=None,
        curr_time_min=None,
        dest_anchor=None,
        dest_time_min=None,
        avg_speed_kmph=25.0,
        max_speed_kmph=50.0,
        is_origin_hub=False,
        is_dest_hub=False
    ):
        if not candidates:
            return candidates

        def candidate_penalty(c):
            penalty = 0.0
            # Previous -> current segment feasibility
            if prev_anchor:
                d_prev = self._haversine(prev_anchor[0], prev_anchor[1], c[0], c[1])
                if prev_time_min is not None and curr_time_min is not None:
                    if is_origin_hub or is_dest_hub:
                        # Relaxation: Hubs often have long cargo/passenger wait times
                        penalty += d_prev * 0.5
                    else:
                        hrs = self._elapsed_hours(prev_time_min, curr_time_min)
                        if hrs:
                            max_d = (hrs * max_speed_kmph * 1.35) + 2.0
                            avg_d = max(hrs * avg_speed_kmph, 1e-6)
                            if d_prev > max_d:
                                penalty += 5000.0 + (d_prev - max_d) * 120.0
                            penalty += abs(d_prev - avg_d) * 2.0
                else:
                    penalty += d_prev * 0.8

            # Current -> destination segment feasibility
            if dest_anchor and curr_time_min is not None and dest_time_min is not None:
                # destination hub check (is_dest_hub refers to current stop, 
                # but we also care if the final destination is a hub)
                if is_dest_hub:
                    pass 
                else:
                    d_dest = self._haversine(c[0], c[1], dest_anchor[0], dest_anchor[1])
                    hrs2 = self._elapsed_hours(curr_time_min, dest_time_min)
                    if hrs2:
                        max_d2 = (hrs2 * max_speed_kmph * 1.35) + 3.0
                        avg_d2 = max(hrs2 * avg_speed_kmph, 1e-6)
                        if d_dest > max_d2:
                            penalty += 5000.0 + (d_dest - max_d2) * 100.0
                        penalty += abs(d_dest - avg_d2) * 1.5


            return penalty

        ranked = sorted(candidates, key=candidate_penalty)
        best_pen = candidate_penalty(ranked[0])
        feasible = [c for c in ranked if candidate_penalty(c) < 5000.0]
        if feasible:
            return feasible[:3]
        # If none strictly feasible, keep closest by temporal penalty to avoid empty collapse.
        return ranked[:1] if best_pen < float("inf") else candidates[:1]

    def _is_long_hop_anomaly(self, prev_node, curr_coord, prev_marker=None, curr_marker=None, is_hub_segment=False):
        if not prev_node or not curr_coord:
            return False

        d_prev = self._haversine(prev_node["lat"], prev_node["lng"], curr_coord[0], curr_coord[1])
        prev_time = self._parse_clock_time((prev_marker or {}).get("departure_time") or (prev_marker or {}).get("arrival_time"))
        curr_time = self._parse_clock_time((curr_marker or {}).get("arrival_time") or (curr_marker or {}).get("departure_time"))
        elapsed_hours = self._elapsed_hours(prev_time, curr_time)

        if elapsed_hours:
            max_reasonable = max(32.0, elapsed_hours * 55.0 * 1.35 + 6.0)
            if is_hub_segment:
                max_reasonable += 18.0
            return d_prev > max_reasonable

        # With no usable schedule, only flag truly suspicious gaps. Rural and
        # intercity services commonly have 30-50 km sections between named stops.
        static_limit = 70.0 if is_hub_segment else 52.0
        return d_prev > static_limit

    def _encode_polyline(self, points):
        """Encode a list of (lat, lng) pairs into a Google polyline string."""
        result = []
        prev_lat = 0
        prev_lng = 0

        for lat, lng in points:
            lat_e5 = int(round(lat * 1e5))
            lng_e5 = int(round(lng * 1e5))

            for value in (lat_e5 - prev_lat, lng_e5 - prev_lng):
                value = ~(value << 1) if value < 0 else (value << 1)
                while value >= 0x20:
                    result.append(chr((0x20 | (value & 0x1F)) + 63))
                    value >>= 5
                result.append(chr(value + 63))

            prev_lat = lat_e5
            prev_lng = lng_e5

        return "".join(result)

    def _decode_polyline(self, encoded_polyline):
        """Decode a Google polyline string into a list of (lat, lng) pairs."""
        points = []
        index = 0
        lat = 0
        lng = 0

        while index < len(encoded_polyline):
            shift = 0
            result = 0
            while True:
                b = ord(encoded_polyline[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta_lat = ~(result >> 1) if result & 1 else (result >> 1)
            lat += delta_lat

            shift = 0
            result = 0
            while True:
                b = ord(encoded_polyline[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta_lng = ~(result >> 1) if result & 1 else (result >> 1)
            lng += delta_lng

            points.append((lat / 1e5, lng / 1e5))

        return points

    def load_verified_cache(self):
        if self.cache_file and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # SOTA: Return the overrides mapping directly
                    return data.get("manual_coordinate_overrides", {})
            except: return {}
        return {}

    def reload_cache(self):
        """Explicitly reloads the coordinate cache from disk."""
        print(f"[ROUTER] Reloading coordinate cache from {self.cache_file}...", flush=True)
        self.coordinate_cache = self.load_verified_cache()

    def _snap_to_nearest_road(self, coord):
        return coord

    def _calculate_transition_cost(self, prev_cand, curr_cand, prev_vec, is_hub_segment, is_curr_hub, global_origin=None, global_dest=None):
        dist = self._haversine(prev_cand[0], prev_cand[1], curr_cand[0], curr_cand[1])
        cost = dist

        if dist > 20.0 and not is_hub_segment:
            cost += 500.0
        if dist > 8.0 and not is_hub_segment:
            cost += 120.0
        if dist > 12.0 and not is_hub_segment:
            cost += 220.0

        if prev_vec:
            curr_vec = (curr_cand[0] - prev_cand[0], curr_cand[1] - prev_cand[1])
            mag_prev = math.sqrt(prev_vec[0]**2 + prev_vec[1]**2)
            mag_curr = math.sqrt(curr_vec[0]**2 + curr_vec[1]**2)
            if mag_prev > 1e-9 and mag_curr > 1e-9:
                dot_prod = prev_vec[0] * curr_vec[0] + prev_vec[1] * curr_vec[1]
                cos_theta = dot_prod / (mag_prev * mag_curr)
                if cos_theta < -0.5:
                    cost += 1000.0

        if not is_curr_hub and global_origin and global_dest:
            dist_direct = self._haversine(global_origin[0], global_origin[1], global_dest[0], global_dest[1])
            dist_via = self._haversine(global_origin[0], global_origin[1], curr_cand[0], curr_cand[1]) + \
                       self._haversine(curr_cand[0], curr_cand[1], global_dest[0], global_dest[1])
            if dist_direct > 5.0 and dist_via > (dist_direct * 1.5):
                cost += 5000.0
        
        return cost

    def _filter_locality_candidates(self, candidates, prev_anchor=None, next_anchor=None):
        if not candidates or not prev_anchor or not next_anchor:
            return candidates
        direct = self._haversine(prev_anchor[0], prev_anchor[1], next_anchor[0], next_anchor[1])
        if direct <= 0.1:
            return candidates
        filtered = []
        for cand in candidates:
            via = self._haversine(prev_anchor[0], prev_anchor[1], cand[0], cand[1]) + \
                  self._haversine(cand[0], cand[1], next_anchor[0], next_anchor[1])
            if via <= max((direct * 1.8), direct + 8.0):
                filtered.append(cand)
        return filtered if filtered else candidates

    def _corridor_deviation_km(self, point, start_anchor, end_anchor):
        if not point or not start_anchor or not end_anchor:
            return 0.0

        lat_scale = 111.0
        lng_scale = 111.0 * math.cos(math.radians((start_anchor[0] + end_anchor[0]) / 2.0))
        ax, ay = start_anchor[1] * lng_scale, start_anchor[0] * lat_scale
        bx, by = end_anchor[1] * lng_scale, end_anchor[0] * lat_scale
        px, py = point[1] * lng_scale, point[0] * lat_scale
        vx, vy = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        denom = vx * vx + vy * vy
        if denom <= 1e-9:
            return self._haversine(start_anchor[0], start_anchor[1], point[0], point[1])

        t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
        proj_x, proj_y = ax + t * vx, ay + t * vy
        return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)

    def _filter_corridor_candidates(self, candidates, prev_anchor=None, next_anchor=None):
        if not candidates or not prev_anchor or not next_anchor:
            return candidates

        direct = self._haversine(prev_anchor[0], prev_anchor[1], next_anchor[0], next_anchor[1])
        if direct < 5.0:
            return candidates

        scored = []
        for cand in candidates:
            via = self._haversine(prev_anchor[0], prev_anchor[1], cand[0], cand[1]) + \
                  self._haversine(cand[0], cand[1], next_anchor[0], next_anchor[1])
            detour = via - direct
            deviation = self._corridor_deviation_km(cand, prev_anchor, next_anchor)
            scored.append((detour, deviation, cand))

        detour_limit = max(18.0, direct * 0.75)
        deviation_limit = max(12.0, direct * 0.35)
        filtered = [
            cand for detour, deviation, cand in scored
            if detour <= detour_limit and deviation <= deviation_limit
        ]
        if filtered:
            return filtered

        # Keep the best geometric fallback instead of collapsing to the first
        # Google result, which may be a far-away homonym.
        scored.sort(key=lambda x: (x[0], x[1]))
        return [scored[0][2]]

    def _prefer_closest_to_previous(self, candidates, prev_anchor=None, next_anchor=None):
        if not candidates or not prev_anchor:
            return candidates
        ranked = sorted(
            candidates,
            key=lambda c: self._haversine(prev_anchor[0], prev_anchor[1], c[0], c[1])
        )
        if len(ranked) <= 1:
            return ranked

        d0 = self._haversine(prev_anchor[0], prev_anchor[1], ranked[0][0], ranked[0][1])
        d1 = self._haversine(prev_anchor[0], prev_anchor[1], ranked[1][0], ranked[1][1])

        # If nearest is clearly better than runner-up, lock to nearest only.
        if d1 - d0 >= 2.5 or (d1 > 0 and d0 / d1 <= 0.45):
            return [ranked[0]]

        # Otherwise keep a small nearby window to preserve robustness.
        keep = []
        for c in ranked:
            dc = self._haversine(prev_anchor[0], prev_anchor[1], c[0], c[1])
            if dc <= d0 + 4.0:
                keep.append(c)
        return keep if keep else ranked[:2]

    def _rank_candidates_by_sequence(self, candidates, prev_anchor=None, next_anchor=None):
        if not candidates:
            return candidates
        if not prev_anchor and not next_anchor:
            return candidates

        def score(c):
            s = 0.0
            if prev_anchor:
                d_prev = self._haversine(prev_anchor[0], prev_anchor[1], c[0], c[1])
                s += d_prev * 2.2
                if d_prev > 12.0:
                    s += 40.0
            if next_anchor:
                d_next = self._haversine(c[0], c[1], next_anchor[0], next_anchor[1])
                s += d_next * 1.0
            if prev_anchor and next_anchor:
                direct = self._haversine(prev_anchor[0], prev_anchor[1], next_anchor[0], next_anchor[1])
                via = self._haversine(prev_anchor[0], prev_anchor[1], c[0], c[1]) + self._haversine(c[0], c[1], next_anchor[0], next_anchor[1])
                if direct > 0.1 and via > (direct * 1.8):
                    s += 80.0
            return s

        ranked = sorted(candidates, key=score)

        if prev_anchor and len(ranked) > 1:
            d0 = self._haversine(prev_anchor[0], prev_anchor[1], ranked[0][0], ranked[0][1])
            d1 = self._haversine(prev_anchor[0], prev_anchor[1], ranked[1][0], ranked[1][1])
            if d1 - d0 >= 2.0:
                return [ranked[0]]
            return ranked[:3]
        return ranked[:3]

    def _apply_endpoint_popularity_bias(self, candidates, adjacent_anchor=None, endpoint_popularity=None):
        if not candidates or len(candidates) <= 1:
            return candidates

        endpoint_popularity = endpoint_popularity or {}

        popular_hubs = []
        for hub_name in list(MAJOR_HUBS.keys()) + list(VERIFIED_TRANSIT_ANCHORS.keys()):
            key = self._normalize_stop_key(hub_name)
            count = int(endpoint_popularity.get(key, 0))
            coords = self._get_hub_match(hub_name)
            if coords and count > 0:
                popular_hubs.append((coords, count))

        if not popular_hubs:
            return candidates

        # Keep the bias small: it only breaks ties among ambiguous endpoint geocodes.
        def score(c):
            base = 0.0
            if adjacent_anchor:
                base += self._haversine(adjacent_anchor[0], adjacent_anchor[1], c[0], c[1])
            best_hub = min(
                (self._haversine(c[0], c[1], h[0][0], h[0][1]) / (1.0 + math.log1p(float(h[1])))) for h in popular_hubs
            )
            return base + (best_hub * 0.35)

        return sorted(candidates, key=score)

    def get_coordinate_candidates(self, stop_name, bias_coord=None, radius=20000, fallback_queries=None, collect_all_queries=False):
        query_norm = str(stop_name).strip().lower()
        
        # SOTA: Normalizing Cache Lookup for Surgical waypoints
        cache_hit = None
        for k, v in self.coordinate_cache.items():
            if str(k).strip().lower() == query_norm:
                cache_hit = v
                break

        if cache_hit:
            coords = cache_hit
            return {"points": [(coords["lat"], coords["lng"]) if isinstance(coords, dict) else tuple(coords)], "ambiguity": False}

        hub_coords = self._get_hub_match(stop_name)
        if hub_coords: return {"points": [hub_coords], "ambiguity": False}
        
        if self.offline:
            return {"points": [], "ambiguity": False}
        
        candidates = []
        queries_to_run = fallback_queries if fallback_queries else [f"{stop_name}, West Bengal"]
        
        for query_str in queries_to_run:
            params = {"query": query_str, "key": self.api_key}
            if bias_coord: params["locationbias"] = f"circle:{radius}@{bias_coord[0]},{bias_coord[1]}"
                
            try:
                resp = requests.get(self.places_url, params=params).json()
                if resp.get("status") == "OK" and resp.get("results"):
                    for res in resp["results"][:5]:
                        loc = res["geometry"]["location"]
                        cand_coord = (loc["lat"], loc["lng"])
                        
                        # --- STRICT GEOMETRIC FILTER ---
                        # Reject any candidate > Radius + 10km from the search center
                        if bias_coord:
                            dist = self._haversine(bias_coord[0], bias_coord[1], cand_coord[0], cand_coord[1])
                            if dist > (radius / 1000.0) + 10.0:
                                continue 
                        
                        candidates.append(cand_coord)
                    
                    if candidates:
                        print(f"      [GEOCODE SUCCESS] Used: '{query_str}'", flush=True)
                        if not collect_all_queries:
                            break
            except: pass

        unique_candidates = []
        for c in candidates:
            if not any(abs(c[0] - uc[0]) < 0.001 and abs(c[1] - uc[1]) < 0.001 for uc in unique_candidates):
                unique_candidates.append(c)

        if bias_coord and len(unique_candidates) > 1:
            unique_candidates.sort(
                key=lambda c: self._haversine(bias_coord[0], bias_coord[1], c[0], c[1])
            )

        return {"points": unique_candidates, "ambiguity": len(unique_candidates) > 1}

    def compute_verified_route(self, stop_names):
        if isinstance(stop_names, dict) and "stops" in stop_names:
            return self.compute_verified_movement_route(stop_names)
        if not stop_names or len(stop_names) < 2:
            return None

        marker_records = self._normalize_marker_records(stop_names)
        endpoint_popularity = {}
        if marker_records and isinstance(marker_records[0], dict):
            endpoint_popularity = dict((marker_records[0].get("__endpoint_popularity") or {}))

        stop_names = [str(m.get("name")).strip() for m in marker_records if str(m.get("name") or "").strip()]
        if len(stop_names) < 2:
            return None
        recovery_pass = max(int((m.get("__recovery_pass") or 0)) for m in marker_records) if marker_records else 0

        forward_signature = self._build_route_signature(marker_records)
        reverse_signature = "|".join(reversed(forward_signature.split("|"))) if forward_signature else ""
        if forward_signature in self.route_result_cache:
            cached = self._clone_route_result(self.route_result_cache[forward_signature])
            cached["cache_hit"] = True
            cached["cache_mode"] = "exact"
            return cached
        if reverse_signature and reverse_signature in self.route_result_cache:
            reversed_cached = self._reverse_cached_result(self.route_result_cache[reverse_signature])
            if reversed_cached:
                reversed_cached["cache_hit"] = True
                reversed_cached["cache_mode"] = "reverse"
                return reversed_cached
            
        # 1. Candidate Generation with Vector-Shifted Sliding Anchors
        stops_candidates = []
        
        dest_hint_data = self.get_coordinate_candidates(stop_names[-1], radius=60000)
        global_dest_hint = dest_hint_data["points"][0] if dest_hint_data["points"] else None
        current_anchor = None
        failed_hops = 0
        recent_stop_coords = {}
        
        for i, name in enumerate(stop_names):
            # --- STATE-AWARE SEARCH (WEST BENGAL VS JHARKHAND) ---
            JHARKHAND_HUBS = ["Ranchi", "Tatanagar", "Chas", "Dhanbad", "Bokaro", "Chandankyari", "Sikirabad", "Tulin", "Netajipur", "Muri"]
            region = "Jharkhand" if any(jh.lower() in name.lower() for jh in JHARKHAND_HUBS) else "West Bengal"
            
            # --- TRANSIT SUFFIX CASCADE ---
            # Using "Mor" as requested for local matching
            district_queries = [
                f"{name}, Bankura district, West Bengal",
                f"{name}, Paschim Medinipur district, West Bengal",
                f"{name}, Purba Medinipur district, West Bengal",
                f"{name}, Hooghly district, West Bengal",
            ] if region == "West Bengal" else []
            queries_default = [
                f"{name} Mor, {region}",
                f"{name} Bus Stand, {region}",
                f"{name} bus depot, {region}",
                f"{name} High School, {region}",
                f"{name}, {region}",
                *district_queries
            ]
            queries_recovery = [
                f"{name} Bus Stand, {region}",
                f"{name} bus depot, {region}",
                *district_queries,
                f"{name} High School, {region}",
                f"{name} High School",
                f"{name} School, {region}",
                f"{name} Mor, {region}",
                f"{name}, Purulia, West Bengal",
                f"{name}, Purulia",
                f"{name}, {region}",
                f"{name}"
            ]
            queries_to_try = queries_recovery if recovery_pass > 0 else queries_default

            if self._is_hub(name):
                current_anchor = self._get_hub_match(name)
                failed_hops, current_radius = 0, 15000
            else:
                current_radius = min(15000 + (failed_hops * 10000), 40000) # Capped at 40km

            # --- FORWARD-SHIFTED BIASING ---
            next_hub_coord = None
            for j in range(i + 1, len(stop_names)):
                if self._is_hub(stop_names[j]):
                    next_hub_coord = self._get_hub_match(stop_names[j])
                    break
            if not next_hub_coord: next_hub_coord = global_dest_hint
            next_stop_hint = None
            if i + 1 < len(stop_names):
                next_name = stop_names[i + 1]
                next_stop_hint = self._get_hub_match(next_name)
                if not next_stop_hint:
                    n_recent = recent_stop_coords.get(self._normalize_stop_key(next_name))
                    if n_recent:
                        next_stop_hint = n_recent.get("coord")
            if not next_stop_hint:
                next_stop_hint = next_hub_coord

            active_bias_coord = current_anchor
            if recovery_pass > 0:
                active_bias_coord = None
            elif next_hub_coord and name != stop_names[-1] and current_anchor and self._is_hub(name):
                # Pull search net slightly toward the next hub only after we already
                # have a plausible local anchor. Biasing an unanchored search toward
                # a distant downstream hub causes whole route prefixes to collapse
                # into placeholders near that hub.
                active_bias_coord = (
                    current_anchor[0] + (next_hub_coord[0] - current_anchor[0]) * 0.20,
                    current_anchor[1] + (next_hub_coord[1] - current_anchor[1]) * 0.20
                )

            recent_coord = None
            recent_info = recent_stop_coords.get(self._normalize_stop_key(name))
            if recent_info and (i - recent_info["idx"]) <= 4:
                recent_coord = recent_info["coord"]

            res = {"points": [], "ambiguity": False}
            lookup_attempts = []
            lookup_attempts.append((active_bias_coord, current_radius, queries_to_try))
            if active_bias_coord is not None:
                lookup_attempts.append((None, current_radius, queries_to_try))
            lookup_attempts.append((None, min(current_radius + 10000, 60000), queries_recovery))

            for attempt_idx, (attempt_bias, attempt_radius, attempt_queries) in enumerate(lookup_attempts):
                res = self.get_coordinate_candidates(
                    name,
                    bias_coord=attempt_bias,
                    radius=attempt_radius,
                    fallback_queries=attempt_queries,
                    collect_all_queries=(recovery_pass > 0 or attempt_idx == len(lookup_attempts) - 1)
                )
                if res.get("points"):
                    res["points"] = self._filter_locality_candidates(
                        res["points"],
                        prev_anchor=current_anchor,
                        next_anchor=next_stop_hint
                    )
                    res["points"] = self._filter_corridor_candidates(
                        res["points"],
                        prev_anchor=current_anchor,
                        next_anchor=next_stop_hint
                    )
                    res["points"] = self._rank_candidates_by_sequence(
                        res["points"],
                        prev_anchor=current_anchor,
                        next_anchor=next_stop_hint
                    )
                    res["points"] = self._prefer_closest_to_previous(
                        res["points"],
                        prev_anchor=current_anchor,
                        next_anchor=next_stop_hint
                    )

                    if i == 0:
                        res["points"] = self._apply_endpoint_popularity_bias(
                            res["points"],
                            adjacent_anchor=next_stop_hint,
                            endpoint_popularity=endpoint_popularity
                        )
                    elif i == len(stop_names) - 1:
                        res["points"] = self._apply_endpoint_popularity_bias(
                            res["points"],
                            adjacent_anchor=current_anchor,
                            endpoint_popularity=endpoint_popularity
                        )
                    prev_time_min = self._parse_clock_time((marker_records[i - 1].get("departure_time") or marker_records[i - 1].get("arrival_time")) if i > 0 else None)
                    curr_time_min = self._parse_clock_time(marker_records[i].get("arrival_time") or marker_records[i].get("departure_time"))
                    dest_time_min = self._parse_clock_time(marker_records[-1].get("arrival_time") or marker_records[-1].get("departure_time"))
                    res["points"] = self._apply_temporal_feasibility_filter(
                        res["points"],
                        prev_anchor=current_anchor,
                        prev_time_min=prev_time_min,
                        curr_time_min=curr_time_min,
                        dest_anchor=global_dest_hint,
                        dest_time_min=dest_time_min,
                        avg_speed_kmph=25.0,
                        max_speed_kmph=50.0,
                        is_origin_hub=(i > 0 and self._is_hub(stop_names[i-1])),
                        is_dest_hub=self._is_hub(name)
                    )

                    res["ambiguity"] = len(res["points"]) > 1
                    if current_anchor and res["points"] and (name.lower() == "gobindapur" or len(res["points"]) > 1):
                        dbg = []
                        for cand in res["points"][:3]:
                            dbg.append(f"{self._haversine(current_anchor[0], current_anchor[1], cand[0], cand[1]):.1f}km@({cand[0]:.5f},{cand[1]:.5f})")
                        print(f"      - [CAND-RANK] {name}: " + " | ".join(dbg), flush=True)
                    # If nearest candidate is still far from previous stop, keep trying stronger query mix.
                    if current_anchor and res["points"] and not self._is_hub(name):
                        best = res["points"][0]
                        d_prev = self._haversine(current_anchor[0], current_anchor[1], best[0], best[1])
                        if d_prev > 18.0 and attempt_idx < (len(lookup_attempts) - 1):
                            print(f"      - [RETRY] {name}: nearest candidate still far ({d_prev:.1f}km), escalating query strategy.", flush=True)
                            continue
                    if attempt_idx > 0:
                        print(f"      - [RETRY OK] {name}: recovered on attempt {attempt_idx + 1}.", flush=True)
                    break
            if recent_coord:
                existing = res["points"] if res.get("points") else []
                if not any(abs(recent_coord[0] - p[0]) < 1e-6 and abs(recent_coord[1] - p[1]) < 1e-6 for p in existing):
                    res["points"] = [recent_coord] + existing
                    res["ambiguity"] = len(res["points"]) > 1
            
            if res["points"]:
                print(f"      - [OK] {name}: Found {len(res['points'])} cand in {current_radius//1000}km net.", flush=True)
                stops_candidates.append({"name": name, "candidates": res["points"], "ambiguity": res["ambiguity"], "is_hub": self._is_hub(name)})
                
                # Update anchor only if result is physically plausible (Anchor Hijack Protection)
                if current_anchor:
                    if self._haversine(current_anchor[0], current_anchor[1], res["points"][0][0], res["points"][0][1]) < 25.0:
                        current_anchor = res["points"][0]
                        failed_hops = 0
                    else: failed_hops += 1
                else: current_anchor = res["points"][0]
                recent_stop_coords[self._normalize_stop_key(name)] = {"coord": res["points"][0], "idx": i}
            else:
                print(f"      - [SKIP] {name}: No local candidate. Interpolating next...", flush=True)
                failed_hops += 1
                fallback_coord = current_anchor or next_hub_coord or global_dest_hint
                if fallback_coord is None and stops_candidates:
                    fallback_coord = stops_candidates[-1]["candidates"][0]
                if fallback_coord is None:
                    fallback_coord = (0.0, 0.0)
                stops_candidates.append({
                    "name": name,
                    "candidates": [fallback_coord],
                    "ambiguity": True,
                    "is_hub": self._is_hub(name),
                    "placeholder": True,
                    "ignored": True
                })
                
        if len(stops_candidates) < 2: return None

        # 2. Trellis Initialization
        trellis = [[] for _ in range(len(stops_candidates))]
        for cand in stops_candidates[0]["candidates"]:
            trellis[0].append({"cost": 0.0, "prev_idx": None, "vec": None})

        global_origin = stops_candidates[0]["candidates"][0]
        global_dest = stops_candidates[-1]["candidates"][0]

        # 3. Forward Pass (Viterbi Recursion)
        for i in range(1, len(stops_candidates)):
            is_hub_segment = stops_candidates[i-1]["is_hub"] or stops_candidates[i]["is_hub"]
            is_curr_hub = stops_candidates[i]["is_hub"]
            
            for curr_idx, curr_cand in enumerate(stops_candidates[i]["candidates"]):
                min_path_cost = float('inf')
                best_prev_idx = 0
                best_vec = None
                
                for prev_idx, prev_state in enumerate(trellis[i-1]):
                    prev_cand = stops_candidates[i-1]["candidates"][prev_idx]
                    trans_cost = self._calculate_transition_cost(
                        prev_cand, curr_cand, prev_state["vec"], is_hub_segment, is_curr_hub, global_origin, global_dest
                    )
                    # Sequence-aware continuity pressure: non-hub local hops should stay geographically compact.
                    hop_km = self._haversine(prev_cand[0], prev_cand[1], curr_cand[0], curr_cand[1])
                    if not is_hub_segment and i > 0 and i < (len(stops_candidates) - 1):
                        if hop_km > 8.0:
                            trans_cost += 180.0
                        if hop_km > 15.0:
                            trans_cost += 350.0
                    total_cost = prev_state["cost"] + trans_cost
                    
                    if total_cost < min_path_cost:
                        min_path_cost = total_cost
                        best_prev_idx = prev_idx
                        best_vec = (curr_cand[0] - prev_cand[0], curr_cand[1] - prev_cand[1])
                
                trellis[i].append({"cost": min_path_cost, "prev_idx": best_prev_idx, "vec": best_vec})

        # 4. Backward Pass (Path Reconstruction)
        best_path_indices = []
        last_step = trellis[-1]
        min_final_cost = float('inf')
        last_best_idx = 0
        
        for idx, state in enumerate(last_step):
            if state["cost"] < min_final_cost:
                min_final_cost = state["cost"]
                last_best_idx = idx
        
        curr_backtrack_idx = last_best_idx
        for i in range(len(stops_candidates) - 1, -1, -1):
            best_path_indices.append(curr_backtrack_idx)
            curr_backtrack_idx = trellis[i][curr_backtrack_idx]["prev_idx"]
        
        best_path_indices.reverse()

        # 5. Build Result Nodes
        verified_coords = []
        for i, cand_idx in enumerate(best_path_indices):
            stop_info = stops_candidates[i]
            marker_meta = marker_records[i] if i < len(marker_records) else {}
            selected_cand = stop_info["candidates"][cand_idx]
            final_coord = self._snap_to_nearest_road(selected_cand)
            
            flags = []
            conf = 95
            is_constant = False
            marker_conf = marker_meta.get("confidence")
            marker_ignored = bool(marker_meta.get("ignored"))
            marker_is_waypoint = bool(marker_meta.get("isWaypoint"))
            marker_hitl = bool(marker_meta.get("hitlVerified") or marker_meta.get("manualEdited"))
            is_placeholder = bool(stop_info.get("placeholder"))
            
            if stop_info["is_hub"]:
                conf = 100
                flags.append("hub_snap")
                is_constant = True
            elif stop_info["ambiguity"]:
                flags.append("ambiguity")
                conf -= 20

            if marker_ignored or is_placeholder:
                flags.append("ignored")
                if is_placeholder and "placeholder" not in flags:
                    flags.append("placeholder")
                conf = 0
                marker_ignored = True
            elif isinstance(marker_conf, (int, float)) and not marker_hitl and not marker_is_waypoint:
                if marker_conf <= 20:
                    flags.append("low_confidence")
                    conf = min(conf, int(marker_conf))

            if i > 0:
                prev_node = verified_coords[-1]
                d_prev = self._haversine(prev_node["lat"], prev_node["lng"], final_coord[0], final_coord[1])
                prev_marker_meta = marker_records[i - 1] if i - 1 < len(marker_records) else {}
                is_hub_segment = bool(prev_node.get("is_constant") or stop_info["is_hub"])
                if self._is_long_hop_anomaly(
                    prev_node,
                    final_coord,
                    prev_marker=prev_marker_meta,
                    curr_marker=marker_meta,
                    is_hub_segment=is_hub_segment
                ):
                    flags.append("long_hop")
                    conf -= 15
                
                prev_vec = trellis[i-1][best_path_indices[i-1]]["vec"]
                curr_vec = (final_coord[0] - prev_node["lat"], final_coord[1] - prev_node["lng"])
                if prev_vec:
                    mag_p = math.sqrt(prev_vec[0]**2 + prev_vec[1]**2)
                    mag_c = math.sqrt(curr_vec[0]**2 + curr_vec[1]**2)
                    if mag_p > 1e-9 and mag_c > 1e-9:
                        if (prev_vec[0] * curr_vec[0] + prev_vec[1] * curr_vec[1]) / (mag_p * mag_c) < -0.7:
                            flags.append("backtrack")
                            conf -= 30

            verified_coords.append({
                "name": stop_info["name"],
                "lat": final_coord[0],
                "lng": final_coord[1],
                "confidence": conf,
                "flags": flags,
                "is_constant": is_constant,
                "candidates": stop_info["candidates"],
                "ignored": marker_ignored,
                "placeholder": is_placeholder,
                "isWaypoint": marker_is_waypoint,
                "hitl_verified": marker_hitl,
                "manualEdited": marker_hitl
            })

        # Phase 3: Post-Processing -> Detour Flagging
        def is_very_low_confidence(node):
            return (
                not node.get("ignored")
                and not node.get("isWaypoint")
                and not node.get("hitl_verified")
                and int(node.get("confidence") or 0) <= 20
            )

        routable_coords = [node for node in verified_coords if not node.get("ignored") and not is_very_low_confidence(node)]
        analysis_coords = routable_coords if len(routable_coords) >= 2 else verified_coords

        total_seq_dist = sum(
            self._haversine(analysis_coords[i]["lat"], analysis_coords[i]["lng"],
                            analysis_coords[i + 1]["lat"], analysis_coords[i + 1]["lng"])
            for i in range(len(analysis_coords) - 1)
        )

        direct_dist = self._haversine(
            analysis_coords[0]["lat"], analysis_coords[0]["lng"],
            analysis_coords[-1]["lat"], analysis_coords[-1]["lng"]
        )
        
        if direct_dist > 0.1 and total_seq_dist > 2.4 * direct_dist:
            for i in range(1, len(analysis_coords) - 1):
                node = analysis_coords[i]
                prev_node = analysis_coords[i - 1]
                next_node = analysis_coords[i + 1]
                via = self._haversine(prev_node["lat"], prev_node["lng"], node["lat"], node["lng"]) + \
                      self._haversine(node["lat"], node["lng"], next_node["lat"], next_node["lng"])
                direct = self._haversine(prev_node["lat"], prev_node["lng"], next_node["lat"], next_node["lng"])
                is_node_detour = direct > 0.1 and via > max(direct * 2.2, direct + 18.0)
                has_prior_geometry_flag = any(flag in node["flags"] for flag in ("backtrack", "long_hop", "severe_outlier"))
                if not self._is_hub(node["name"]) and (is_node_detour or has_prior_geometry_flag):
                    if "loop_suspect" not in node["flags"]:
                        node["flags"].append("loop_suspect")
                        node["confidence"] -= 20

        loopish_nodes = sum(1 for node in analysis_coords[1:-1] if any(flag in node["flags"] for flag in ("backtrack", "long_hop", "loop_suspect", "severe_outlier")))
        if len(analysis_coords) >= 6 and direct_dist > 0.1 and total_seq_dist > (direct_dist * 20.0) and loopish_nodes >= 3:
            print("      [ROUTER] Rejecting loop-suspect route before polyline generation.", flush=True)
            if recovery_pass < 2:
                print(f"      [ROUTER] Recovery pass {recovery_pass + 1}: retrying with stronger suffix fallback.", flush=True)
                retry_records = [dict(m) for m in marker_records]
                for m in retry_records:
                    m["__recovery_pass"] = recovery_pass + 1
                return self.compute_verified_route(retry_records)
            return {"polyline": "", "distanceMeters": 0, "nodes": verified_coords}

        # Phase 4: "Drop and Ignore" Outlier Pruning
        valid_routing_indices = [0]
        
        for i in range(1, len(verified_coords) - 1):
            curr_node = verified_coords[i]
            prev_kept_idx = valid_routing_indices[-1]
            prev_kept_node = verified_coords[prev_kept_idx]
            next_node = verified_coords[i + 1]

            if curr_node.get("ignored"):
                curr_node["ignored_for_routing"] = True
                if "ignored" not in curr_node["flags"]:
                    curr_node["flags"].append("ignored")
                curr_node["confidence"] = 0
                continue

            if is_very_low_confidence(curr_node):
                curr_node["ignored_for_routing"] = True
                if "low_confidence" not in curr_node["flags"]:
                    curr_node["flags"].append("low_confidence")
                continue
            
            if self._is_hub(curr_node["name"]):
                valid_routing_indices.append(i)
                continue
            
            dist_via = (self._haversine(prev_kept_node["lat"], prev_kept_node["lng"], curr_node["lat"], curr_node["lng"]) +
                        self._haversine(curr_node["lat"], curr_node["lng"], next_node["lat"], next_node["lng"]))
            dist_direct = self._haversine(prev_kept_node["lat"], prev_kept_node["lng"], next_node["lat"], next_node["lng"])
            prev_marker_meta = marker_records[prev_kept_idx] if prev_kept_idx < len(marker_records) else {}
            curr_marker_meta = marker_records[i] if i < len(marker_records) else {}
            next_marker_meta = marker_records[i + 1] if i + 1 < len(marker_records) else {}
            prev_to_curr_ok = not self._is_long_hop_anomaly(
                prev_kept_node,
                (curr_node["lat"], curr_node["lng"]),
                prev_marker=prev_marker_meta,
                curr_marker=curr_marker_meta,
                is_hub_segment=bool(prev_kept_node.get("is_constant") or curr_node.get("is_constant"))
            )
            curr_to_next_ok = not self._is_long_hop_anomaly(
                curr_node,
                (next_node["lat"], next_node["lng"]),
                prev_marker=curr_marker_meta,
                curr_marker=next_marker_meta,
                is_hub_segment=bool(curr_node.get("is_constant") or next_node.get("is_constant"))
            )
            schedule_supported = prev_to_curr_ok and curr_to_next_ok and int(curr_node.get("confidence") or 0) >= 70
            
            is_severe_outlier = (
                (dist_via > max(dist_direct * 2.0, dist_direct + 12.0))
                and not schedule_supported
            )
            
            if is_severe_outlier:
                curr_node["ignored_for_routing"] = True
                if "severe_outlier" not in curr_node["flags"]:
                    curr_node["flags"].append("severe_outlier")
                curr_node["confidence"] = 0
            else:
                valid_routing_indices.append(i)
        
        if len(verified_coords) > 1:
            valid_routing_indices.append(len(verified_coords) - 1)

        coords = verified_coords
        
        # --- CHUNKED "VIA" ROUTING ENFORCEMENT ---
        routing_coords = [verified_coords[i] for i in valid_routing_indices]
        print(f"      [CHUNK-DEBUG] Routing {len(routing_coords)} stops with CHUNK_SIZE=25", flush=True)
        
        def request_route_geometry(origin_node, dest_node, intermediates, tag):
            if self.offline:
                return "", 0

            payload = {
                "origin": {"location": {"latLng": {"latitude": origin_node["lat"], "longitude": origin_node["lng"]}}},
                "destination": {"location": {"latLng": {"latitude": dest_node["lat"], "longitude": dest_node["lng"]}}},
                "intermediates": intermediates,
                "travelMode": "TWO_WHEELER",
                "routingPreference": "TRAFFIC_UNAWARE"
            }
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "routes.distanceMeters,routes.polyline.encodedPolyline"
            }

            print(f"      - Requesting {tag}...", flush=True)
            print(f"      - PAYLOAD: {json.dumps(payload)}", flush=True)

            chunk_poly = ""
            chunk_distance = 0
            for attempt in range(5):
                try:
                    r = requests.post(self.routes_url, json=payload, headers=headers, timeout=30)
                    if r.status_code == 200:
                        resp = r.json()
                        if "routes" in resp and len(resp["routes"]) > 0:
                            route_data = resp["routes"][0]
                            chunk_poly = route_data.get("polyline", {}).get("encodedPolyline", "")
                            chunk_distance = route_data.get("distanceMeters", 0)
                            if chunk_poly:
                                print(f"      - {tag} successful: {chunk_distance}m", flush=True)
                            else:
                                print(f"      - [WARNING] {tag} returned empty polyline: {resp}", flush=True)
                        else:
                            print(f"      - [WARNING] No routes found in response: {resp}", flush=True)
                        break
                    else:
                        print(f"      - [ERROR] API Status {r.status_code}: {r.text}", flush=True)
                        time.sleep(2 ** attempt)
                except Exception as e:
                    print(f"      - [ERROR] Exception: {e}", flush=True)
                    time.sleep(2 ** attempt)
            return chunk_poly, chunk_distance

        if len(routing_coords) < 2:
            if recovery_pass < 2:
                print(f"      [ROUTER] Recovery pass {recovery_pass + 1}: insufficient routing points, retrying.", flush=True)
                retry_records = [dict(m) for m in marker_records]
                for m in retry_records:
                    m["__recovery_pass"] = recovery_pass + 1
                return self.compute_verified_route(retry_records)
            return {"polyline": "", "distanceMeters": 0, "nodes": coords}

        final_poly_points = []
        total_distance = 0
        
        CHUNK_SIZE = 25
        for i in range(0, len(routing_coords) - 1, CHUNK_SIZE - 1):
            chunk = routing_coords[i : i + CHUNK_SIZE]
            if len(chunk) < 2:
                break

            origin_node = chunk[0]
            dest_node = chunk[-1]
            intermediates = []
            for node in chunk[1:-1]:
                intermediates.append({
                    "location": {"latLng": {"latitude": node["lat"], "longitude": node["lng"]}},
                    "via": True
                })

            tag = f"Chunk {i//(CHUNK_SIZE-1) + 1} ({len(chunk)} nodes)"
            chunk_poly, dist_m = request_route_geometry(origin_node, dest_node, intermediates, tag)

            if not chunk_poly:
                print(f"      - [FALLBACK] {tag} failed, retrying as smaller legs.", flush=True)
                chunk_poly_points = []
                chunk_distance = 0
                for j in range(len(chunk) - 1):
                    leg_origin = chunk[j]
                    leg_dest = chunk[j + 1]
                    leg_tag = f"Leg {j + 1}/{len(chunk) - 1} of {tag}"
                    leg_poly, leg_dist = request_route_geometry(leg_origin, leg_dest, [], leg_tag)
                    if leg_poly:
                        leg_points = self._decode_polyline(leg_poly)
                    else:
                        leg_points = [
                            (leg_origin["lat"], leg_origin["lng"]),
                            (leg_dest["lat"], leg_dest["lng"])
                        ]
                        if not leg_dist:
                            leg_dist = self._haversine(
                                leg_origin["lat"], leg_origin["lng"],
                                leg_dest["lat"], leg_dest["lng"]
                            ) * 1000.0

                    if chunk_poly_points and leg_points:
                        last_pt = chunk_poly_points[-1]
                        first_pt = leg_points[0]
                        if abs(last_pt[0] - first_pt[0]) < 1e-6 and abs(last_pt[1] - first_pt[1]) < 1e-6:
                            leg_points = leg_points[1:]
                    chunk_poly_points.extend(leg_points)
                    chunk_distance += leg_dist

                if chunk_poly_points:
                    chunk_poly = self._encode_polyline(chunk_poly_points)
                    dist_m = chunk_distance

            if chunk_poly:
                chunk_points = self._decode_polyline(chunk_poly)
                if final_poly_points and chunk_points:
                    last_pt = final_poly_points[-1]
                    first_pt = chunk_points[0]
                    if abs(last_pt[0] - first_pt[0]) < 1e-6 and abs(last_pt[1] - first_pt[1]) < 1e-6:
                        chunk_points = chunk_points[1:]
                final_poly_points.extend(chunk_points)
                total_distance += dist_m

        final_poly = self._encode_polyline(final_poly_points) if final_poly_points else ""
        if not final_poly and recovery_pass < 2:
            print(f"      [ROUTER] Recovery pass {recovery_pass + 1}: empty polyline, retrying with aggressive search.", flush=True)
            retry_records = [dict(m) for m in marker_records]
            for m in retry_records:
                m["__recovery_pass"] = recovery_pass + 1
            return self.compute_verified_route(retry_records)

        result = {
            "polyline": final_poly,
            "distanceMeters": total_distance,
            "nodes": coords
        }
        self.route_result_cache[forward_signature] = self._clone_route_result(result)
        return result


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = BASE_DIR / "Polyline_Drawing_Pipeline" / "BusData_Phase_1.json"
DEFAULT_OUT_POLYLINE = BASE_DIR / "Polyline_Drawing_Pipeline" / "BusData_Phase_1_polyline_stoppages.json"
DEFAULT_OUT_HITL = BASE_DIR / "HITL_Pipeline_new" / "BD_Phase1_HITL_input.json"
DEFAULT_BACKUP_DIR = BASE_DIR / "BackUP"


def backup_file(file_path: Path, backup_dir: Path, timestamp: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{file_path.stem}_{timestamp}{file_path.suffix or '.json'}"
    if file_path.exists():
        shutil.copy2(file_path, backup_path)
    else:
        backup_path.write_text("", encoding="utf-8")
    
    # --- ROLLING BACKUP (Keep last 10) ---
    try:
        pattern = f"{file_path.stem}_*{file_path.suffix or '.json'}"
        backups = sorted(list(backup_dir.glob(pattern)), key=lambda x: x.stat().st_mtime)
        while len(backups) > 10:
            oldest = backups.pop(0)
            oldest.unlink()
    except Exception as e:
        print(f"[BACKUP CLEANUP ERROR] {e}")
    # -------------------------------------
    
    return backup_path


def atomic_write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp_path.replace(path)
            return
        except (PermissionError, OSError):
            if attempt == 4: raise
            time.sleep(0.1 * (attempt + 1))


def maybe_load_resume_payload(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("buses"), list) and isinstance(data.get("metadata"), dict):
            return data
    except Exception:
        return None
    return None


def _bus_matches_filter(bus, selected_regs):
    if not selected_regs:
        return True
    reg = str((bus or {}).get("reg_no") or "").strip().upper()
    return reg in selected_regs


def run_batch_compute(
    source: Path,
    out_polyline: Path,
    out_hitl: Path,
    backup_dir: Path,
    selected_regs=None,
    resume=False,
):
    selected_regs = {str(r).strip().upper() for r in (selected_regs or []) if str(r).strip()}
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b1 = backup_file(out_polyline, backup_dir, ts)
    b2 = backup_file(out_hitl, backup_dir, ts)
    print(f"[BACKUP] {b1}", flush=True)
    print(f"[BACKUP] {b2}", flush=True)

    source_data = json.loads(source.read_text(encoding="utf-8"))
    source_buses = list(source_data.get("buses") or [])
    candidate_buses = [b for b in source_buses if _bus_matches_filter(b, selected_regs)]
    router = PuruliaTransitRouter(API_KEY)

    def init_payload():
        now_iso = datetime.now().isoformat(timespec="seconds")
        return {
            "metadata": {
                "source_file": str(source),
                "generated_at": now_iso,
                "updated_at": now_iso,
                "status": "in_progress",
                "total_buses": 0,
                "processed_buses": 0,
                "total_movements": 0,
                "success_movements": 0,
                "failed_movements": 0,
                "original_movement_count": 0,
                "unique_movement_count": 0,
                "deduped_movement_count": 0,
                "last_processed_reg_no": None,
                "progress_pct": 0.0,
            },
            "buses": [],
        }

    def bus_identity(bus):
        return (
            str((bus or {}).get("reg_no") or "").strip().upper(),
            self_name if (self_name := str((bus or {}).get("bus_name") or "").strip()) else "",
        )

    def upsert_bus(payload_obj, bus_obj):
        reg, name = bus_identity(bus_obj)
        for i, existing in enumerate(payload_obj.get("buses") or []):
            e_reg, e_name = bus_identity(existing)
            if reg and e_reg == reg:
                payload_obj["buses"][i] = bus_obj
                return
            if not reg and name and e_name == name:
                payload_obj["buses"][i] = bus_obj
                return
        payload_obj.setdefault("buses", []).append(bus_obj)

    def recompute_metadata(payload_obj):
        meta = payload_obj.get("metadata") if isinstance(payload_obj.get("metadata"), dict) else {}
        total_unique = 0
        total_original = 0
        total_deduped = 0
        success = 0
        failed = 0
        for b in payload_obj.get("buses") or []:
            ded = b.get("movement_dedupe") if isinstance(b.get("movement_dedupe"), dict) else {}
            total_original += int(ded.get("original_movement_count") or len(b.get("movements") or []))
            total_unique += int(ded.get("unique_movement_count") or len(b.get("movements") or []))
            total_deduped += int(ded.get("deduped_movement_count") or 0)
            for m in b.get("movements") or []:
                rt = m.get("route") or {}
                # Success = it generated a polyline OR it was intelligently handicapped
                if rt.get("polyline") or rt.get("redundant_of"):
                    success += 1
                else:
                    failed += 1
        meta["total_buses"] = len(payload_obj.get("buses") or [])
        meta["processed_buses"] = len(payload_obj.get("buses") or [])
        meta["total_movements"] = total_unique
        meta["original_movement_count"] = total_original
        meta["unique_movement_count"] = total_unique
        meta["deduped_movement_count"] = total_deduped
        meta["success_movements"] = success
        meta["failed_movements"] = failed
        payload_obj["metadata"] = meta

    start_index = 0
    payload = init_payload()
    existing = maybe_load_resume_payload(out_polyline)
    is_partial = bool(selected_regs)
    if existing and existing.get("metadata", {}).get("source_file") == str(source):
        if resume or is_partial:
            payload = existing
            if resume and not is_partial:
                start_index = len(payload.get("buses") or [])
                print(f"[RESUME] Continuing from bus index {start_index}", flush=True)
    elif resume:
        print("[RESUME] No valid checkpoint found; starting fresh.", flush=True)

    # Enable online routing for new movements
    router.offline = False

    if not resume and not is_partial:
        atomic_write_json(out_polyline, payload)
        atomic_write_json(out_hitl, payload)

    for bus_idx in range(start_index, len(candidate_buses)):
        bus = candidate_buses[bus_idx]
        print(f"[{bus_idx + 1}/{len(candidate_buses)}] Processing {bus.get('bus_name')} ({bus.get('reg_no')})", flush=True)

        unique_routes, plan = router.compute_bus_movement_routes(bus)
        bus_out = {
            "bus_name": bus.get("bus_name"),
            "reg_no": bus.get("reg_no"),
            "primary_hub": bus.get("primary_hub"),
            "movement_dedupe": {
                "original_movement_count": plan["original_movement_count"],
                "unique_movement_count": plan["unique_movement_count"],
                "deduped_movement_count": plan["deduped_movement_count"],
                "duplicate_groups": plan["duplicate_groups"],
            },
            "movements": unique_routes,
        }
        upsert_bus(payload, bus_out)
        payload["metadata"]["processed_buses"] = min(bus_idx + 1, len(candidate_buses))
        payload["metadata"]["last_processed_reg_no"] = bus.get("reg_no")
        payload["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        recompute_metadata(payload)
        payload["metadata"]["progress_pct"] = round(((bus_idx + 1) / max(len(candidate_buses), 1)) * 100.0, 2)
        atomic_write_json(out_polyline, payload)
        atomic_write_json(out_hitl, payload)

        print(
            f"[CHECKPOINT] processed={bus_idx + 1}/{len(candidate_buses)} "
            f"({payload['metadata']['progress_pct']}%)",
            flush=True
        )

    recompute_metadata(payload)
    payload["metadata"]["status"] = "completed"
    payload["metadata"]["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["metadata"]["updated_at"] = payload["metadata"]["generated_at"]
    payload["metadata"]["progress_pct"] = 100.0 if candidate_buses else 0.0
    atomic_write_json(out_polyline, payload)
    atomic_write_json(out_hitl, payload)

    print(f"[DONE] Wrote: {out_polyline}", flush=True)
    print(f"[DONE] Wrote: {out_hitl}", flush=True)
    print(
        f"[SUMMARY] buses={payload['metadata']['processed_buses']}/{payload['metadata']['total_buses']} "
        f"unique_movements={payload['metadata']['unique_movement_count']} "
        f"deduped={payload['metadata']['deduped_movement_count']} "
        f"success={payload['metadata']['success_movements']} "
        f"failed={payload['metadata']['failed_movements']}",
        flush=True
    )
    return payload


def main():
    parser = argparse.ArgumentParser(description="Compute unique bus polylines from source and write dual outputs.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--out-polyline", default=str(DEFAULT_OUT_POLYLINE))
    parser.add_argument("--out-hitl", default=str(DEFAULT_OUT_HITL))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint if available.")
    parser.add_argument("--all", action="store_true", help="Compute all buses.")
    parser.add_argument("--bus", action="append", default=[], help="Specific reg_no to compute; repeatable.")
    args = parser.parse_args()

    raw_bus_args = [str(b).strip() for b in (args.bus or []) if str(b).strip()]
        
    if any(b.lower() == "continue" for b in raw_bus_args):
        args.resume = True
        raw_bus_args = [b for b in raw_bus_args if b.lower() != "continue"]
        if not raw_bus_args:
            args.all = True
    selected_regs = raw_bus_args
    if not args.all and not selected_regs:
        args.all = True
    run_batch_compute(
        source=Path(args.source),
        out_polyline=Path(args.out_polyline),
        out_hitl=Path(args.out_hitl),
        backup_dir=Path(args.backup_dir),
        selected_regs=[] if args.all else selected_regs,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
