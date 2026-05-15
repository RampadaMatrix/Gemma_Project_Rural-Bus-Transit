import json
import os
import time
from datetime import datetime

import sys
from pathlib import Path
import requests

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
    "Barabazar": (23.028380, 8670578),
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
    "Pabra":(23.562004, 87.019752)
}


class PuruliaTransitRouter:
    """
    HITL-focused deterministic renderer.
    - Uses HITL-provided coordinates as source of truth (no geocoding).
    - Preserves stop sequence from input.
    - Draws route geometry via Directions API chunking + leg fallback.
    """

    def __init__(self, api_key):
        self.api_key = api_key
        self.routes_url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        self.route_result_cache = {}

    def _normalize_stop_key(self, stop_name):
        return " ".join(str(stop_name or "").strip().lower().split())

    def _encode_polyline(self, points):
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
            dlat = ~(result >> 1) if result & 1 else (result >> 1)
            lat += dlat

            shift = 0
            result = 0
            while True:
                b = ord(encoded_polyline[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            dlng = ~(result >> 1) if result & 1 else (result >> 1)
            lng += dlng
            points.append((lat / 1e5, lng / 1e5))
        return points

    def _build_route_signature(self, stops):
        parts = []
        for s in stops:
            key = self._normalize_stop_key(s.get("name"))
            lat = s.get("lat")
            lng = s.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                parts.append(f"{key}@{round(float(lat),5)},{round(float(lng),5)}")
            else:
                parts.append(key)
        return "|".join(parts)

    def _normalize_stops(self, stop_input):
        if isinstance(stop_input, dict) and isinstance(stop_input.get("stops"), list):
            raw = stop_input.get("stops") or []
        elif isinstance(stop_input, list) and stop_input and isinstance(stop_input[0], dict):
            raw = stop_input
        elif isinstance(stop_input, list):
            raw = [{"name": str(x).strip()} for x in stop_input if str(x).strip()]
        else:
            raw = []

        # --- NEW: THE RESCUE DICTIONARY ---
        # Look inside the existing 'route.nodes' object to see if coordinates are hiding there
        node_lookup = {}
        if isinstance(stop_input, dict):
            existing_nodes = stop_input.get("route", {}).get("nodes", [])
            for n in existing_nodes:
                node_lookup[self._normalize_stop_key(n.get("name"))] = n

        cleaned = []
        for s in raw:
            name = str((s or {}).get("name") or "").strip()
            if not name:
                continue

            # --- NEW: THE RESCUE OPERATION ---
            lat = s.get("lat")
            lng = s.get("lng")

            # If lat/lng are missing in 'stops', grab them from the 'route.nodes' lookup!
            if (lat is None or lng is None) and self._normalize_stop_key(name) in node_lookup:
                fallback_node = node_lookup[self._normalize_stop_key(name)]
                lat = fallback_node.get("lat")
                lng = fallback_node.get("lng")

            rec = {
                "name": name,
                "lat": lat,
                "lng": lng,
                "isWaypoint": bool(s.get("isWaypoint")),
            }
            if cleaned:
                prev = cleaned[-1]
                if self._normalize_stop_key(prev["name"]) == self._normalize_stop_key(rec["name"]):
                    same_coord = (
                        isinstance(prev.get("lat"), (int, float))
                        and isinstance(prev.get("lng"), (int, float))
                        and isinstance(rec.get("lat"), (int, float))
                        and isinstance(rec.get("lng"), (int, float))
                        and abs(float(prev["lat"]) - float(rec["lat"])) < 1e-9
                        and abs(float(prev["lng"]) - float(rec["lng"])) < 1e-9
                    )
                    if same_coord:
                        continue
            cleaned.append(rec)
        return cleaned

    def get_hub_coords(self, name):
        key = self._normalize_stop_key(name)
        for h_name, coords in MAJOR_HUBS.items():
            if self._normalize_stop_key(h_name) == key:
                return coords
        return None

    def _movement_sequence_for_signature(self, movement):
        stops = self._normalize_stops(movement)
        seq = []
        for s in stops:
            name = self._normalize_stop_key(s.get("name"))
            lat, lng = s.get("lat"), s.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                # Coordinate-aware HITL signature
                seq.append(f"{name}@{round(float(lat), 5)},{round(float(lng), 5)}")
            else:
                seq.append(name)
        return seq

    def get_unique_movement_plan(self, bus_record):
        bus_record = bus_record or {}
        movements = list(bus_record.get("movements") or [])

        def representative_score(movement):
            route = movement.get("route") or {}
            nodes = route.get("nodes") or []
            poly_ok = 1 if route.get("polyline") else 0
            return (len(nodes), poly_ok)

        groups, order = {}, []
        for idx, movement in enumerate(movements):
            seq = self._movement_sequence_for_signature(movement)
            if len(seq) < 2:
                key = f"__raw__{idx}"
            else:
                fwd, rev = ">".join(seq), ">".join(reversed(seq))
                key = rev if rev < fwd else fwd
            if key not in groups:
                groups[key] = {
                    "representative_idx": idx,
                    "representative_trip_id": movement.get("trip_id"),
                    "representative_score": representative_score(movement),
                    "member_indices": [idx],
                    "member_trip_ids": [movement.get("trip_id")],
                    "signature": key
                }
                order.append(key)
            else:
                cur_score = representative_score(movement)
                if cur_score > groups[key]["representative_score"]:
                    groups[key]["representative_idx"] = idx
                    groups[key]["representative_trip_id"] = movement.get("trip_id")
                    groups[key]["representative_score"] = cur_score
                groups[key]["member_indices"].append(idx)
                groups[key]["member_trip_ids"].append(movement.get("trip_id"))

        return {
            "representative_indices": [groups[k]["representative_idx"] for k in order],
            "groups": groups,
            "original_count": len(movements),
            "unique_count": len(order)
        }

    def _build_nodes_and_validate(self, stops, existing_nodes=None):
        nodes = []
        issues = []
        
        # Build node cache for patching if existing nodes are provided
        node_cache = {}
        if isinstance(existing_nodes, list):
            for n in existing_nodes:
                if isinstance(n, dict) and n.get("name"):
                    key = self._normalize_stop_key(n.get("name"))
                    node_cache[key] = n

        for s in stops:
            name = s.get("name")
            lat = s.get("lat")
            lng = s.get("lng")
            if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                issues.append(f"Missing coordinate for stop '{name}'.")
                continue
            
            key = self._normalize_stop_key(name)
            if key in node_cache:
                # Surgical Patch: Keep all rich metadata, update coordinates
                node = dict(node_cache[key])
                node["lat"] = float(lat)
                node["lng"] = float(lng)
                node["hitl_verified"] = True
                node["manualEdited"] = True
                nodes.append(node)
            else:
                # New Node fallback
                nodes.append(
                    {
                        "name": name,
                        "lat": float(lat),
                        "lng": float(lng),
                        "confidence": 100,
                        "flags": [],
                        "isWaypoint": bool(s.get("isWaypoint")),
                        "hitl_verified": True,
                        "manualEdited": True,
                    }
                )
        if len(nodes) < 2:
            issues.append("Need at least 2 stops with coordinates.")
        return nodes, issues

    def _is_loop_suspect(self, nodes):
        # Distance-based loop heuristics intentionally disabled for now.
        return False

    def _request_route_geometry(self, origin_node, dest_node, intermediates, metrics=None):
        base_origin = {"location": {"latLng": {"latitude": origin_node["lat"], "longitude": origin_node["lng"]}}}
        base_destination = {"location": {"latLng": {"latitude": dest_node["lat"], "longitude": dest_node["lng"]}}}

        def build_intermediates(via_required):
            items = []
            for n in intermediates or []:
                if not isinstance(n, dict):
                    continue
                loc = n.get("location")
                if not isinstance(loc, dict):
                    continue
                item = {"location": loc}
                if via_required:
                    item["via"] = True
                items.append(item)
            return items

        # Ordered from strictest to most permissive. This improves consistency when
        # a dragged point is slightly off-road or TWO_WHEELER coverage is patchy.
        strategies = [
            {"travelMode": "TWO_WHEELER", "via_required": True},
            {"travelMode": "DRIVE", "via_required": True},
            {"travelMode": "DRIVE", "via_required": False},
            {"travelMode": "TWO_WHEELER", "via_required": False},
        ]
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.polyline.encodedPolyline",
        }

        for s_idx, strategy in enumerate(strategies):
            payload = {
                "origin": base_origin,
                "destination": base_destination,
                "intermediates": build_intermediates(strategy["via_required"]),
                "travelMode": strategy["travelMode"],
                "routingPreference": "TRAFFIC_UNAWARE",
            }
            # retry transient failures for this strategy
            for attempt in range(2):
                try:
                    t0 = time.perf_counter()
                    if isinstance(metrics, dict):
                        metrics["api_calls"] = int(metrics.get("api_calls", 0)) + 1
                    print(
                        f"[ROUTES API] strategy={s_idx + 1}/{len(strategies)} mode={strategy['travelMode']} "
                        f"via_required={strategy['via_required']} attempt={attempt + 1} "
                        f"origin=({origin_node['lat']:.6f},{origin_node['lng']:.6f}) "
                        f"dest=({dest_node['lat']:.6f},{dest_node['lng']:.6f}) "
                        f"vias={len(payload['intermediates'])}",
                        flush=True,
                    )
                    r = requests.post(self.routes_url, json=payload, headers=headers, timeout=30)
                    elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
                    if isinstance(metrics, dict):
                        metrics["api_elapsed_ms"] = int(metrics.get("api_elapsed_ms", 0)) + elapsed_ms
                    if r.status_code == 200:
                        body = r.json()
                        routes = body.get("routes") or []
                        if routes:
                            route = routes[0]
                            poly = route.get("polyline", {}).get("encodedPolyline", "")
                            if isinstance(metrics, dict):
                                metrics["api_success"] = int(metrics.get("api_success", 0)) + 1
                            print(
                                f"[ROUTES API] success mode={strategy['travelMode']} via_required={strategy['via_required']} "
                                f"polyline={'yes' if poly else 'no'} elapsed_ms={elapsed_ms}",
                                flush=True,
                            )
                            return poly
                        # try next strategy when API returns 200 but no route.
                        print(
                            f"[ROUTES API] no route mode={strategy['travelMode']} via_required={strategy['via_required']}",
                            flush=True,
                        )
                        break
                    if isinstance(metrics, dict):
                        metrics["api_fail"] = int(metrics.get("api_fail", 0)) + 1
                    print(
                        f"[ROUTES API] non-200 status={r.status_code} mode={strategy['travelMode']} via_required={strategy['via_required']}",
                        flush=True,
                    )
                    # Retry transient statuses, otherwise move to next strategy.
                    if r.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                        time.sleep(1.5)
                        continue
                    break
                except Exception:
                    if isinstance(metrics, dict):
                        metrics["api_fail"] = int(metrics.get("api_fail", 0)) + 1
                    print(
                        f"[ROUTES API] exception mode={strategy['travelMode']} via_required={strategy['via_required']}",
                        flush=True,
                    )
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                    break
        return ""

    def _build_polyline_chunked(self, nodes, metrics=None):
        chunk_size = 25
        final_points = []

        for i in range(0, len(nodes) - 1, chunk_size - 1):
            chunk = nodes[i : i + chunk_size]
            if len(chunk) < 2:
                break
            origin = chunk[0]
            dest = chunk[-1]
            intermediates = [
                {
                    "location": {"latLng": {"latitude": n["lat"], "longitude": n["lng"]}},
                    "via": True,
                }
                for n in chunk[1:-1]
            ]
            poly = self._request_route_geometry(origin, dest, intermediates, metrics=metrics)
            chunk_points = self._decode_polyline(poly) if poly else []

            if not chunk_points:
                # Segment fallback for resilience.
                for j in range(len(chunk) - 1):
                    a = chunk[j]
                    b = chunk[j + 1]
                    leg_poly = self._request_route_geometry(a, b, [], metrics=metrics)
                    leg_points = self._decode_polyline(leg_poly) if leg_poly else [(a["lat"], a["lng"]), (b["lat"], b["lng"])]
                    if final_points and leg_points:
                        lp = final_points[-1]
                        fp = leg_points[0]
                        if abs(lp[0] - fp[0]) < 1e-6 and abs(lp[1] - fp[1]) < 1e-6:
                            leg_points = leg_points[1:]
                    final_points.extend(leg_points)
                continue

            if final_points and chunk_points:
                lp = final_points[-1]
                fp = chunk_points[0]
                if abs(lp[0] - fp[0]) < 1e-6 and abs(lp[1] - fp[1]) < 1e-6:
                    chunk_points = chunk_points[1:]
            final_points.extend(chunk_points)

        polyline = self._encode_polyline(final_points) if final_points else ""
        return polyline

    def compute_verified_movement_route(self, movement, all_movements=None, movement_index=0, force_refresh=False, existing_nodes=None):
        _ = all_movements
        _ = movement_index
        return self.compute_verified_route(movement, force_refresh=force_refresh, existing_nodes=existing_nodes)

    def compute_bus_movement_routes(self, bus_record, force_refresh=False):
        bus_record = bus_record or {}
        movements = list(bus_record.get("movements") or [])
        plan = self.get_unique_movement_plan(bus_record)
        import copy

        # 1. Route only representatives
        unique_results = {}
        for k, group in plan["groups"].items():
            rep_idx = group["representative_idx"]
            movement = movements[rep_idx]
            print(f"      - [HITL-SMART] Routing representative {rep_idx} for signature: {k[:60]}...", flush=True)
            route_res = self.compute_verified_movement_route(movement, force_refresh=force_refresh)
            unique_results[k] = {
                "res": route_res,
                "fwd": ">".join(self._movement_sequence_for_signature(movement)),
                "rep_trip_id": movement.get("trip_id")
            }

        # 2. Re-hydrate with Handicap Protocol
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
            # --- FULL GEOMETRY RESTORATION (No Handicap) ---
            if is_rep:
                # Master trip keeps full data
                route_copy = copy.deepcopy(group_data["res"])
                route_copy["master_geometry"] = True
            else:
                # Redundant trip gets FULL geometry cloned from the master
                rep_res = group_data["res"]
                route_copy = copy.deepcopy(rep_res)

                # Restore the polyline instead of nullifying it
                route_copy["polyline"] = rep_res.get("polyline")

                route_copy["master_geometry"] = False
                route_copy["redundant_of"] = group_data["rep_trip_id"]
                route_copy["signature"] = key

                # If the sequence is mathematically reversed from the master, reverse the nodes AND polyline
                if fwd != group_data["fwd"]:
                    route_copy["nodes"].reverse()
                    route_copy["reused_reverse"] = True
                    for node in route_copy["nodes"]:
                        if "flags" not in node:
                            node["flags"] = []
                        if "reverse_reuse" not in node["flags"]:
                            node["flags"].append("reverse_reuse")

                    # Mathematically flip the polyline string for the return trip
                    if route_copy.get("polyline"):
                        points = self._decode_polyline(route_copy["polyline"])
                        points.reverse()
                        route_copy["polyline"] = self._encode_polyline(points)

            final_movements.append({
                "trip_id": trip_id,
                "direction": movement.get("direction"),
                "origin": movement.get("origin"),
                "destination": movement.get("destination"),
                "stops": movement.get("stops") or [],
                "route": route_copy
            })
        return final_movements

    def compute_verified_route(self, stop_input, force_refresh=False, existing_nodes=None):
        stops = self._normalize_stops(stop_input)
        if len(stops) < 2:
            return {"polyline": "", "nodes": [], "status": "invalid", "issues": ["Need at least 2 valid stops."]}

        signature = self._build_route_signature(stops)
        if not force_refresh and signature in self.route_result_cache:
            cached = json.loads(json.dumps(self.route_result_cache[signature]))
            cached["cache_hit"] = True
            cached["computeMeta"] = {
                "api_calls": 0,
                "api_success": 0,
                "api_fail": 0,
                "api_elapsed_ms": 0,
                "force_refresh": False,
            }
            return cached

        metrics = {
            "api_calls": 0,
            "api_success": 0,
            "api_fail": 0,
            "api_elapsed_ms": 0,
            "force_refresh": bool(force_refresh),
        }

        nodes, issues = self._build_nodes_and_validate(stops, existing_nodes=existing_nodes)
        if len(nodes) < 2:
            return {"polyline": "", "nodes": nodes, "status": "invalid", "issues": issues}

        loop_guard = self._is_loop_suspect(nodes)
        if loop_guard:
            # Keep sequence visible but avoid drawing a deceptive loop polyline.
            for n in nodes:
                flags = list(n.get("flags") or [])
                if "loop_guard_triggered" not in flags:
                    flags.append("loop_guard_triggered")
                n["flags"] = flags
            result = {
                "polyline": "",
                "nodes": nodes,
                "status": "loop_guard_blocked",
                "issues": ["Loop guard blocked route drawing for this stop sequence."],
                "computeMeta": metrics,
            }
            self.route_result_cache[signature] = json.loads(json.dumps(result))
            return result

        polyline = self._build_polyline_chunked(nodes, metrics=metrics)
        status = "ok" if polyline else "partial"
        result = {
            "polyline": polyline,
            "nodes": nodes,
            "status": status,
            "issues": issues,
            "rendered_at": datetime.now().isoformat(timespec="seconds"),
            "computeMeta": metrics,
        }
        self.route_result_cache[signature] = json.loads(json.dumps(result))
        return result


if __name__ == "__main__":
    router = PuruliaTransitRouter(API_KEY)
    demo_movement = {
        "stops": [
            {"name": "A", "lat": 23.33091, "lng": 86.361153},
            {"name": "B", "lat": 23.3505727, "lng": 86.5286573},
            {"name": "C", "lat": 23.409836, "lng": 86.5798166},
        ]
    }
    out = router.compute_verified_route(demo_movement)
    print(json.dumps({"status": out.get("status"), "nodes": len(out.get("nodes") or [])}, indent=2))
