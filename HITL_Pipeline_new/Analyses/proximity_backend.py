import math
import re
from datetime import datetime


SEARCH_RADIUS_M = 5000.0
FALLBACK_CORRIDOR_COUNT = 3
MAX_RADIUS_TRIPS = 60

_PROX_RUNTIME_CACHE = {}
_PROX_RUNTIME_CACHE_LIMIT = 4
_PROX_STOP_ROWS_CACHE = {}
_PROX_MOVEMENT_INDEX_CACHE = {}
_PROX_POINT_BASE_CACHE = {}
_PROX_POINT_CACHE_LIMIT = 256
_PROX_ORIENTATION_CACHE = {}


def _get_corridors_cached(secured_data, cache_key=None):
    if cache_key:
        cached = _PROX_RUNTIME_CACHE.get(cache_key)
        if isinstance(cached, list) and cached:
            return cached
    corridors = _build_corridor_index(secured_data)
    if cache_key:
        _PROX_RUNTIME_CACHE[cache_key] = corridors
        if len(_PROX_RUNTIME_CACHE) > _PROX_RUNTIME_CACHE_LIMIT:
            oldest = next(iter(_PROX_RUNTIME_CACHE.keys()))
            _PROX_RUNTIME_CACHE.pop(oldest, None)
    return corridors


def _get_movement_index_cached(secured_data, cache_key=None):
    if cache_key:
        cached = _PROX_MOVEMENT_INDEX_CACHE.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("entries"), list):
            return cached

    entries = []
    by_key = {}
    for bus in secured_data.get("buses", []) or []:
        if not isinstance(bus, dict):
            continue
        for idx, movement in enumerate(bus.get("movements", []) or []):
            if not isinstance(movement, dict):
                continue
            trip_key = _trip_unique_key(bus, movement, idx)
            sig = _normalize_signature(movement.get("corridor_signature"))
            cid = _normalize_signature(movement.get("corridor_id"))
            entry = {
                "trip_key": trip_key,
                "bus_name": str(bus.get("bus_name") or "").strip(),
                "reg_no": str(bus.get("reg_no") or "").strip(),
                "movement": movement,
                "sig": sig,
                "cid": cid,
            }
            entries.append(entry)
            if sig:
                by_key.setdefault(sig, []).append(entry)
            if cid:
                by_key.setdefault(cid, []).append(entry)

    payload = {"entries": entries, "by_key": by_key}
    if cache_key:
        _PROX_MOVEMENT_INDEX_CACHE[cache_key] = payload
        if len(_PROX_MOVEMENT_INDEX_CACHE) > _PROX_RUNTIME_CACHE_LIMIT:
            oldest = next(iter(_PROX_MOVEMENT_INDEX_CACHE.keys()))
            _PROX_MOVEMENT_INDEX_CACHE.pop(oldest, None)
    return payload


def _normalize_signature(txt):
    if not txt:
        return ""
    return "".join(ch for ch in str(txt).lower() if ch.isalnum())


def _reverse_corridor_signature(raw_title):
    parts = [part.strip() for part in str(raw_title or "").split("-") if part and str(part).strip()]
    if len(parts) < 2:
        return ""
    return _normalize_signature("-".join(parts[::-1]))


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dlat = p2 - p1
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _decode_polyline(encoded):
    if not encoded:
        return []
    index = lat = lng = 0
    coordinates = []
    while index < len(encoded):
        shift = result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        delta = ~(result >> 1) if result & 1 else (result >> 1)
        lat += delta

        shift = result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        delta = ~(result >> 1) if result & 1 else (result >> 1)
        lng += delta
        coordinates.append({"lat": lat * 1e-5, "lng": lng * 1e-5})
    return coordinates


def _fallback_points_from_rows(rows):
    pts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lat = row.get("lat")
        lng = row.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            pts.append({"lat": float(lat), "lng": float(lng)})
    return pts


def _polyline_endpoints_mismatch(points, anchor_rows, max_endpoint_m=2500.0):
    if not isinstance(points, list) or len(points) < 2:
        return True
    anchors = _fallback_points_from_rows(anchor_rows)
    if len(anchors) < 2:
        return False
    p_start, p_end = points[0], points[-1]
    a_start, a_end = anchors[0], anchors[-1]
    direct = _haversine_m(p_start["lat"], p_start["lng"], a_start["lat"], a_start["lng"]) + _haversine_m(
        p_end["lat"], p_end["lng"], a_end["lat"], a_end["lng"]
    )
    flipped = _haversine_m(p_start["lat"], p_start["lng"], a_end["lat"], a_end["lng"]) + _haversine_m(
        p_end["lat"], p_end["lng"], a_start["lat"], a_start["lng"]
    )
    return min(direct, flipped) > float(max_endpoint_m)


def _min_point_to_polyline_m(lat, lng, points):
    if not isinstance(points, list) or len(points) < 2:
        return float("inf")
    target = {"lat": float(lat), "lng": float(lng)}
    best = None
    for i in range(len(points) - 1):
        seg_proj = _project_point_to_segment(target, points[i], points[i + 1])
        off = float(seg_proj.get("off_road_m") or float("inf"))
        if best is None or off < best:
            best = off
    return float(best if best is not None else float("inf"))


def _polyline_anchor_mismatch(points, anchor_rows, max_anchor_m=1800.0):
    anchors = _fallback_points_from_rows(anchor_rows)
    if len(anchors) < 2 or len(points) < 2:
        return False
    worst = 0.0
    for anchor in anchors:
        d = _min_point_to_polyline_m(anchor["lat"], anchor["lng"], points)
        if d > worst:
            worst = d
        if worst > float(max_anchor_m):
            return True
    return False

def _build_segments_for_points(points):
    segments = []
    total_length_m = 0.0
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        length_m = _haversine_m(p1["lat"], p1["lng"], p2["lat"], p2["lng"])
        segments.append(
            {
                "p1": p1,
                "p2": p2,
                "start_chainage_m": total_length_m,
                "length_m": length_m,
            }
        )
        total_length_m += length_m
    return segments, total_length_m


def _projection_points(points, max_points=1800):
    if not isinstance(points, list):
        return []
    n = len(points)
    if n <= 2:
        return points
    if n <= 2500:
        return points
    step = max(1, int(math.ceil(n / float(max_points))))
    sampled = [points[i] for i in range(0, n, step)]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    if len(sampled) < 2:
        return points
    return sampled


def _coarse_projection_points(points, max_points=96):
    if not isinstance(points, list):
        return []
    n = len(points)
    if n <= 2:
        return points
    if n <= max_points:
        return points
    step = max(1, int(math.ceil(n / float(max_points))))
    sampled = [points[i] for i in range(0, n, step)]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled if len(sampled) >= 2 else points

def _bbox_for_points(points):
    if not points:
        return None
    lats = [float(p["lat"]) for p in points]
    lngs = [float(p["lng"]) for p in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lng": min(lngs),
        "max_lng": max(lngs),
    }


def _bbox_min_distance_m(lat, lng, bbox):
    if not isinstance(bbox, dict):
        return float("inf")
    clamped_lat = min(max(float(lat), float(bbox["min_lat"])), float(bbox["max_lat"]))
    clamped_lng = min(max(float(lng), float(bbox["min_lng"])), float(bbox["max_lng"]))
    return _haversine_m(float(lat), float(lng), clamped_lat, clamped_lng)


def _project_point_to_segment(point, a, b):
    lat_to_m = 111320.0
    lng_to_m = 111320.0 * math.cos(math.radians(float(point["lat"])))

    dx = (float(b["lng"]) - float(a["lng"])) * lng_to_m
    dy = (float(b["lat"]) - float(a["lat"])) * lat_to_m
    px = (float(point["lng"]) - float(a["lng"])) * lng_to_m
    py = (float(point["lat"]) - float(a["lat"])) * lat_to_m

    length_sq = dx * dx + dy * dy
    fraction = 0.0
    if length_sq > 0:
        fraction = (px * dx + py * dy) / length_sq
        fraction = max(0.0, min(1.0, fraction))

    proj_lng = float(a["lng"]) + fraction * (float(b["lng"]) - float(a["lng"]))
    proj_lat = float(a["lat"]) + fraction * (float(b["lat"]) - float(a["lat"]))
    return {
        "proj_lat": proj_lat,
        "proj_lng": proj_lng,
        "off_road_m": _haversine_m(float(point["lat"]), float(point["lng"]), proj_lat, proj_lng),
        "fraction": fraction,
    }


def _bearing_deg(lat1, lng1, lat2, lng2):
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dlon = math.radians(float(lng2) - float(lng1))
    x = math.sin(dlon) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)) - (math.sin(phi1) * math.cos(phi2) * math.cos(dlon))
    deg = math.degrees(math.atan2(x, y))
    return (deg + 360.0) % 360.0


def _bearing_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((float(deg) + 22.5) // 45.0) % 8)
    return dirs[idx]


def _offset_payload(query_lat, query_lng, proj_lat, proj_lng, off_road_m):
    bearing = _bearing_deg(query_lat, query_lng, proj_lat, proj_lng)
    cardinal = _bearing_cardinal(bearing)
    km = float(off_road_m) / 1000.0
    return {
        "off_road_m": round(float(off_road_m), 1),
        "offset_km": round(km, 2),
        "offset_bearing_deg": round(float(bearing), 1),
        "offset_cardinal": cardinal,
        "offset_label": f"{round(km, 2):.2f} km {cardinal}",
    }


def _project_chainage_on_corridor(corridor, lat, lng):
    if not isinstance(corridor, dict) or not corridor.get("segments"):
        return None
    point = {"lat": float(lat), "lng": float(lng)}
    best = None
    for seg in corridor["segments"]:
        proj = _project_point_to_segment(point, seg["p1"], seg["p2"])
        if best is None or proj["off_road_m"] < best["off_road_m"]:
            best = {
                "off_road_m": float(proj["off_road_m"]),
                "chainage_m": float(seg["start_chainage_m"]) + (float(proj["fraction"]) * float(seg["length_m"])),
            }
    return best["chainage_m"] if isinstance(best, dict) else None


def _movement_endpoint_coords(movement):
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
    coords = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        lat = node.get("lat")
        lng = node.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            coords.append((float(lat), float(lng)))
    if len(coords) >= 2:
        return coords[0], coords[-1]

    stops = movement.get("stops") if isinstance(movement.get("stops"), list) else []
    s_coords = []
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        lat = stop.get("lat")
        lng = stop.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            s_coords.append((float(lat), float(lng)))
    if len(s_coords) >= 2:
        return s_coords[0], s_coords[-1]
    return None, None


def _movement_needs_fraction_flip(movement, corridor, cache_key=None, trip_key=None):
    if not isinstance(movement, dict) or not isinstance(corridor, dict):
        return False
    cache_scope = str(cache_key or "__nocache__")
    tk = str(trip_key or "")
    cid = str(corridor.get("id") or "")
    ckey = f"{cache_scope}||{tk}||{cid}"
    if ckey in _PROX_ORIENTATION_CACHE:
        return bool(_PROX_ORIENTATION_CACHE.get(ckey))

    first_pt, last_pt = _movement_endpoint_coords(movement)
    if not first_pt or not last_pt:
        _PROX_ORIENTATION_CACHE[ckey] = False
        return False

    c_first = _project_chainage_on_corridor(corridor, first_pt[0], first_pt[1])
    c_last = _project_chainage_on_corridor(corridor, last_pt[0], last_pt[1])
    if not isinstance(c_first, (int, float)) or not isinstance(c_last, (int, float)):
        _PROX_ORIENTATION_CACHE[ckey] = False
        return False

    flip = float(c_first) > float(c_last)
    _PROX_ORIENTATION_CACHE[ckey] = bool(flip)
    return bool(flip)


def _parse_time_to_minutes(value):
    if not value:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None

    compact = raw.replace(" ", "")
    m = re.match(r"^(\d{1,2})(?::?(\d{2}))?(AM|PM)$", compact)
    if m:
        try:
            hh = int(m.group(1))
            mm = int(m.group(2) or "0")
        except Exception:
            return None
        if hh < 1 or hh > 12 or mm < 0 or mm > 59:
            return None
        ap = m.group(3)
        if ap == "AM":
            hh = 0 if hh == 12 else hh
        else:
            hh = 12 if hh == 12 else hh + 12
        return hh * 60 + mm

    m24 = re.match(r"^(\d{1,2})(?::?(\d{2}))$", raw)
    if m24:
        try:
            hh = int(m24.group(1))
            mm = int(m24.group(2) or "0")
        except Exception:
            return None
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 60 + mm
    return None


def _first_valid_minutes(*candidates):
    for val in candidates:
        parsed = _parse_time_to_minutes(val)
        if parsed is not None:
            return parsed
    return None


def _clock_from_minutes(mins):
    if mins is None:
        return "--"
    mins = int(round(float(mins)))
    while mins < 0:
        mins += 1440
    while mins >= 1440:
        mins -= 1440
    hh = mins // 60
    mm = mins % 60
    ap = "PM" if hh >= 12 else "AM"
    hh = hh % 12 or 12
    return f"{hh}:{str(mm).zfill(2)} {ap}"


def _live_minutes(now_minutes=None):
    if isinstance(now_minutes, (int, float)):
        return int(now_minutes) % 1440
    now = datetime.now()
    return now.hour * 60 + now.minute


def _trip_unique_key(bus, movement, idx):
    return "||".join(
        [
            str(bus.get("bus_name") or "").strip(),
            str(bus.get("reg_no") or "").strip().upper(),
            str(movement.get("trip_id") or "").strip(),
            str(movement.get("corridor_id") or "").strip(),
            str(idx),
        ]
    )


def _build_corridor_index(secured_data):
    corridors = []
    seen = {}
    for bus in secured_data.get("buses", []) or []:
        if not isinstance(bus, dict):
            continue
        for movement in bus.get("movements", []) or []:
            if not isinstance(movement, dict):
                continue
            
            corridor_id = str(movement.get("corridor_id") or "").strip()
            norm_corr_id = _normalize_signature(corridor_id)
            
            if norm_corr_id and norm_corr_id in seen:
                existing = seen[norm_corr_id]
                if existing.get("polyline") and len(existing.get("points") or []) > 20:
                    continue

            route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
            polyline = route.get("polyline") or ""
            anchors = route.get("nodes") or movement.get("stops") or []
            points = _decode_polyline(polyline)
            
            if len(points) >= 2 and (
                _polyline_endpoints_mismatch(points, anchors)
                or _polyline_anchor_mismatch(points, anchors)
            ):
                points = []
            if len(points) < 2:
                points = _fallback_points_from_rows(anchors)
            if len(points) < 2:
                continue

            corridor_signature = str(movement.get("corridor_signature") or "").strip()
            raw_title = corridor_signature or f"{movement.get('origin') or ''}-{movement.get('destination') or ''}".strip("-")
            norm_title = _normalize_signature(raw_title)
            key = f"id:{norm_corr_id}" if norm_corr_id else f"title:{norm_title}|pts:{len(points)}"
            
            if key in seen:
                existing = seen[key]
                sig = _normalize_signature(corridor_signature or raw_title)
                rev_sig = _reverse_corridor_signature(corridor_signature or raw_title)
                if sig:
                    existing["signatures"].add(sig)
                if rev_sig:
                    existing["reverse_signatures"].add(rev_sig)
                if corridor_id:
                    existing["signatures"].add(_normalize_signature(corridor_id))
                if len(points) > len(existing.get("points") or []):
                    segments, total_length_m = _build_segments_for_points(_projection_points(points))
                    coarse_segments, _ = _build_segments_for_points(_coarse_projection_points(points))
                    existing["polyline"] = polyline
                    existing["points"] = points
                    existing["segments"] = segments
                    existing["coarse_segments"] = coarse_segments
                    existing["total_length_m"] = total_length_m
                    existing["bbox"] = _bbox_for_points(points)
                continue

            segments, total_length_m = _build_segments_for_points(_projection_points(points))
            coarse_segments, _ = _build_segments_for_points(_coarse_projection_points(points))
            signatures = set()
            reverse_signatures = set()
            if norm_title:
                signatures.add(norm_title)
                rev_norm_title = _reverse_corridor_signature(raw_title)
                if rev_norm_title:
                    reverse_signatures.add(rev_norm_title)
            if raw_title and "-" in raw_title:
                rev = "-".join([part for part in raw_title.split("-")[::-1] if part])
                if rev:
                    reverse_signatures.add(_normalize_signature(rev))
            if corridor_id:
                signatures.add(_normalize_signature(corridor_id))
            if corridor_signature:
                sig = _normalize_signature(corridor_signature)
                if sig:
                    signatures.add(sig)
                rev_sig = _reverse_corridor_signature(corridor_signature)
                if rev_sig:
                    reverse_signatures.add(rev_sig)

            corridor = {
                "id": corridor_id or f"corr_{len(corridors) + 1}",
                "signature": norm_title or _normalize_signature(corridor_id),
                "raw_title": raw_title or corridor_id or "Unknown Corridor",
                "polyline": polyline,
                "points": points,
                "segments": segments,
                "coarse_segments": coarse_segments,
                "total_length_m": total_length_m,
                "bbox": _bbox_for_points(points),
                "signatures": signatures,
                "reverse_signatures": reverse_signatures,
            }
            seen[key] = corridor
            corridors.append(corridor)

    by_sig = {}
    for c in corridors:
        by_sig[c.get("signature") or ""] = c

    for c in corridors:
        if c.get("polyline") and len(c.get("points") or []) > 20:
            continue
        rev_sig = _reverse_corridor_signature(c.get("raw_title"))
        rev = by_sig.get(rev_sig)
        if not rev:
            continue
        recovered = list(rev.get("points") or [])[::-1]
        if len(recovered) < 2:
            continue
        segs, total_len = _build_segments_for_points(_projection_points(recovered))
        coarse_segs, _ = _build_segments_for_points(_coarse_projection_points(recovered))
        c["points"] = recovered
        c["segments"] = segs
        c["coarse_segments"] = coarse_segs
        c["total_length_m"] = total_len
        c["bbox"] = _bbox_for_points(recovered)
        if not c.get("polyline"):
            c["polyline"] = rev.get("polyline") or ""

    return corridors


def _best_projection_on_segments(point, segments):
    if not isinstance(segments, list) or not segments:
        return None
    best_local = None
    for seg in segments:
        proj = _project_point_to_segment(point, seg["p1"], seg["p2"])
        if best_local is None or proj["off_road_m"] < best_local["off_road_m"]:
            best_local = {
                **proj,
                "global_chainage_m": seg["start_chainage_m"] + (proj["fraction"] * seg["length_m"]),
            }
    return best_local


def _project_across_corridors(corridors, lat, lng):
    best = None
    projections = []
    point = {"lat": float(lat), "lng": float(lng)}

    ranked = sorted(
        corridors,
        key=lambda c: _bbox_min_distance_m(point["lat"], point["lng"], c.get("bbox")),
    )
    near = [c for c in ranked if _bbox_min_distance_m(point["lat"], point["lng"], c.get("bbox")) <= (SEARCH_RADIUS_M * 2.0)]
    candidates = near if near else ranked[:36]

    coarse_ranked = []
    for corridor in candidates:
        coarse_segments = corridor.get("coarse_segments") or corridor.get("segments") or []
        coarse_best = _best_projection_on_segments(point, coarse_segments)
        if coarse_best is None:
            continue
        coarse_ranked.append({"corridor": corridor, "coarse": coarse_best})

    coarse_ranked.sort(key=lambda item: float(item["coarse"]["off_road_m"]))
    precise_limit = 36 if near else 18
    precise_pool = coarse_ranked[:precise_limit] if coarse_ranked else []

    for item in precise_pool:
        corridor = item["corridor"]
        best_local = _best_projection_on_segments(point, corridor.get("segments") or [])
        if best_local is None:
            continue
        row = {"corridor": corridor, "projection": best_local}
        projections.append(row)
        if best is None or best_local["off_road_m"] < best["projection"]["off_road_m"]:
            best = row

    if len(projections) < 3:
        seen_ids = {item["corridor"]["id"] for item in projections}
        for corridor in ranked:
            if corridor.get("id") in seen_ids:
                continue
            best_local = _best_projection_on_segments(point, corridor.get("segments") or [])
            if best_local is None:
                continue
            row = {"corridor": corridor, "projection": best_local}
            projections.append(row)
            if best is None or best_local["off_road_m"] < best["projection"]["off_road_m"]:
                best = row
            if len(projections) >= 12:
                break

    return best, projections


def _movement_stop_chainages(movement, corridor):
    rows = []
    stops = movement.get("stops") or []
    use_projection = corridor is not None and corridor.get("segments")
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    route_nodes = route.get("nodes") if isinstance(route.get("nodes"), list) else []
    route_chainage_by_name = {}
    route_total_m = 0.0
    if route_nodes:
        prev = None
        for node in route_nodes:
            if not isinstance(node, dict):
                continue
            lat = node.get("lat")
            lng = node.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                if prev is not None:
                    route_total_m += _haversine_m(prev["lat"], prev["lng"], float(lat), float(lng))
                prev = {"lat": float(lat), "lng": float(lng)}
            name_key = _normalize_signature(node.get("name"))
            if name_key and name_key not in route_chainage_by_name:
                route_chainage_by_name[name_key] = route_total_m / 1000.0
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        name = str(stop.get("name") or "").strip()
        if not name:
            continue
        chainage_km = None
        if isinstance(stop.get("chainage_km"), (int, float)):
            chainage_km = float(stop["chainage_km"])
        elif isinstance(stop.get("from_origin_m"), (int, float)):
            chainage_km = float(stop["from_origin_m"]) / 1000.0
        elif isinstance(stop.get("road_dist_m"), (int, float)):
            chainage_km = float(stop["road_dist_m"]) / 1000.0
        elif route_chainage_by_name:
            stop_key = _normalize_signature(name)
            chainage_km = route_chainage_by_name.get(stop_key)
        elif use_projection and isinstance(stop.get("lat"), (int, float)) and isinstance(stop.get("lng"), (int, float)):
            best = None
            point = {"lat": float(stop["lat"]), "lng": float(stop["lng"])}
            for seg in corridor["segments"]:
                proj = _project_point_to_segment(point, seg["p1"], seg["p2"])
                if best is None or proj["off_road_m"] < best["off_road_m"]:
                    best = {
                        **proj,
                        "global_chainage_m": seg["start_chainage_m"] + (proj["fraction"] * seg["length_m"]),
                    }
            if best is not None:
                chainage_km = best["global_chainage_m"] / 1000.0
        rows.append(
            {
                "name": name,
                "chainage_km": chainage_km,
                "arrival_time": stop.get("arrival_time"),
                "departure_time": stop.get("departure_time"),
                "estimated_time": stop.get("estimated_time"),
            }
        )

    missing = [idx for idx, row in enumerate(rows) if row.get("chainage_km") is None]
    if missing and rows:
        total_km = float(movement.get("total_route_km") or 0.0)
        if total_km <= 0 and corridor and isinstance(corridor.get("total_length_m"), (int, float)):
            total_km = float(corridor.get("total_length_m")) / 1000.0
        if total_km > 0:
            denom = max(1, len(rows) - 1)
            for idx in missing:
                rows[idx]["chainage_km"] = (float(idx) / float(denom)) * total_km
    return rows


def _cached_movement_rows(cache_key, trip_key, movement, corridor):
    if not cache_key or not trip_key:
        return _movement_stop_chainages(movement, corridor)
    scoped = _PROX_STOP_ROWS_CACHE.get(cache_key)
    if not isinstance(scoped, dict):
        scoped = {}
        _PROX_STOP_ROWS_CACHE[cache_key] = scoped
        if len(_PROX_STOP_ROWS_CACHE) > _PROX_RUNTIME_CACHE_LIMIT:
            oldest = next(iter(_PROX_STOP_ROWS_CACHE.keys()))
            if oldest != cache_key:
                _PROX_STOP_ROWS_CACHE.pop(oldest, None)
    corr_key = str(corridor.get("id") if isinstance(corridor, dict) else "") or "corr"
    key = f"{trip_key}||{corr_key}"
    cached = scoped.get(key)
    if isinstance(cached, list) and cached:
        return cached
    rows = _movement_stop_chainages(movement, corridor)
    scoped[key] = rows
    return rows


def _interpolate_trip_pass_minutes(movement, corridor, target_chainage_km, cache_key=None, trip_key=None):
    rows = _cached_movement_rows(cache_key, trip_key, movement, corridor)
    timed = []
    for row in rows:
        ta = _first_valid_minutes(row.get("departure_time"), row.get("arrival_time"), row.get("estimated_time"))
        if ta is None or row.get("chainage_km") is None:
            continue
        timed.append(row)
    if len(timed) < 2:
        return None, "low"

    stop_a = timed[0]
    stop_b = timed[-1]
    for idx in range(len(timed) - 1):
        a = timed[idx]
        b = timed[idx + 1]
        c_a = float(a.get("chainage_km") or 0.0)
        c_b = float(b.get("chainage_km") or c_a)
        if (c_a <= target_chainage_km <= c_b) or (c_b <= target_chainage_km <= c_a):
            stop_a = a
            stop_b = b
            break

    t_a = _first_valid_minutes(stop_a.get("departure_time"), stop_a.get("arrival_time"), stop_a.get("estimated_time"))
    t_b = _first_valid_minutes(stop_b.get("arrival_time"), stop_b.get("departure_time"), stop_b.get("estimated_time"))
    if t_a is None or t_b is None:
        return None, "low"
    if t_b < t_a:
        t_b += 1440

    c_a = float(stop_a.get("chainage_km") or 0.0)
    c_b = float(stop_b.get("chainage_km") or c_a)
    span = abs(c_b - c_a)
    offset = abs(target_chainage_km - c_a)
    local_frac = (offset / span) if span > 0 else 0.0
    pass_min = t_a + local_frac * (t_b - t_a)
    if pass_min >= 1440:
        pass_min -= 1440

    confidence = "high" if span > 0 and len(timed) >= 3 else "medium"
    return pass_min, confidence


def _classify_trip_eta(eta_min, live_min):
    delta = float(eta_min) - float(live_min)
    if delta < -720:
        delta += 1440
    if delta > 720:
        delta -= 1440

    status = "LATER"
    label = "LATER"
    if delta < -30:
        status = "MISSED"
        label = "MISSED"
    elif delta <= 0:
        status = "JUST_PASSED"
        label = "JUST PASSED"
    elif delta <= 60:
        status = "SOON"
        label = "PASSES SOON"
    else:
        status = "UPCOMING"
        label = "UPCOMING"
    return {"status": status, "label": label, "delta": round(delta, 1)}


def _confidence_window_label(confidence_band):
    band = str(confidence_band or "").strip().lower()
    if band == "high":
        return "+-30 min"
    if band == "medium":
        return "+-1h"
    return "+-1h 30m"


def _point_cache_namespace(cache_key):
    ns = str(cache_key or "__nocache__")
    if ns not in _PROX_POINT_BASE_CACHE:
        _PROX_POINT_BASE_CACHE[ns] = {}
        if len(_PROX_POINT_BASE_CACHE) > _PROX_RUNTIME_CACHE_LIMIT:
            oldest = next(iter(_PROX_POINT_BASE_CACHE.keys()))
            if oldest != ns:
                _PROX_POINT_BASE_CACHE.pop(oldest, None)
    return _PROX_POINT_BASE_CACHE[ns]


def _point_cache_token(lat, lng):
    return f"{round(float(lat), 4):.4f}:{round(float(lng), 4):.4f}"


def _compute_point_base(secured_data, corridors, movement_index, lat, lng, cache_key=None):
    best_hit, corridor_projections = _project_across_corridors(corridors, lat, lng)
    if best_hit is None:
        return {"available": False, "reason": "projection_failed"}

    in_radius = [cp for cp in corridor_projections if cp["projection"]["off_road_m"] <= SEARCH_RADIUS_M]
    selected_corridors = (
        in_radius
        if in_radius
        else sorted(corridor_projections, key=lambda cp: cp["projection"]["off_road_m"])[:FALLBACK_CORRIDOR_COUNT]
    )
    selected_signature_map = {}
    selected_id_map = {}
    for cp in selected_corridors:
        corridor = cp["corridor"]
        projection = cp["projection"]
        score = float(projection["off_road_m"])
        frac = (projection["global_chainage_m"] / corridor["total_length_m"]) if corridor["total_length_m"] > 0 else 0.0
        fwd_entry = {
            "fraction": frac,
            "length_km": corridor["total_length_m"] / 1000.0,
            "corridor_id": corridor["id"],
            "corridor": corridor,
            "score": score,
        }
        rev_entry = {
            "fraction": 1.0 - frac,
            "length_km": corridor["total_length_m"] / 1000.0,
            "corridor_id": corridor["id"],
            "corridor": corridor,
            "score": score,
        }
        for sig in corridor.get("signatures") or []:
            if sig:
                existing = selected_signature_map.get(sig)
                if existing is None or score < existing.get("score", float("inf")):
                    selected_signature_map[sig] = fwd_entry
        for rev_sig in corridor.get("reverse_signatures") or []:
            if rev_sig:
                existing = selected_signature_map.get(rev_sig)
                if existing is None or score < existing.get("score", float("inf")):
                    selected_signature_map[rev_sig] = rev_entry
        id_key = _normalize_signature(corridor["id"])
        if id_key:
            existing = selected_id_map.get(id_key)
            if existing is None or score < existing.get("score", float("inf")):
                selected_id_map[id_key] = fwd_entry

    by_key = movement_index.get("by_key") if isinstance(movement_index, dict) else {}
    candidate_entries = []
    seen_trip = set()
    for lookup_key in list(selected_signature_map.keys()) + list(selected_id_map.keys()):
        for entry in by_key.get(lookup_key, []) if isinstance(by_key, dict) else []:
            tk = entry.get("trip_key")
            if not tk or tk in seen_trip:
                continue
            seen_trip.add(tk)
            candidate_entries.append(entry)

    passing_trips_base = []
    for entry in candidate_entries:
        movement = entry.get("movement") if isinstance(entry, dict) else None
        if not isinstance(movement, dict):
            continue
        trip_key = entry.get("trip_key")
        corridor_sig = entry.get("sig") or _normalize_signature(movement.get("corridor_signature"))
        corridor_id = entry.get("cid") or _normalize_signature(movement.get("corridor_id"))
        match = selected_signature_map.get(corridor_sig) or selected_signature_map.get(corridor_id) or selected_id_map.get(corridor_id)
        if not match:
            continue

        total_route_km = float(movement.get("total_route_km") or match["length_km"] or 0.0)
        effective_fraction = float(match.get("fraction") or 0.0)
        if _movement_needs_fraction_flip(movement, match["corridor"], cache_key=cache_key, trip_key=trip_key):
            effective_fraction = 1.0 - effective_fraction
        target_chainage_km = effective_fraction * total_route_km if total_route_km > 0 else 0.0
        eta_min, confidence = _interpolate_trip_pass_minutes(
            movement,
            match["corridor"],
            target_chainage_km,
            cache_key=cache_key,
            trip_key=trip_key,
        )
        if eta_min is None:
            continue

        passing_trips_base.append(
            {
                "bus_name": entry.get("bus_name") or "",
                "reg_no": entry.get("reg_no") or "",
                "trip_id": movement.get("trip_id"),
                "direction": movement.get("direction"),
                "origin": movement.get("origin"),
                "destination": movement.get("destination"),
                "corridor_signature": movement.get("corridor_signature"),
                "corridor_id": match["corridor_id"],
                "eta_minutes": round(float(eta_min), 1),
                "eta_clock": _clock_from_minutes(eta_min),
                "confidence_band": confidence,
                "confidence_window": _confidence_window_label(confidence),
            }
        )

    primary = best_hit["corridor"]
    projection = best_hit["projection"]
    primary_offset = _offset_payload(
        lat,
        lng,
        projection["proj_lat"],
        projection["proj_lng"],
        projection["off_road_m"],
    )
    corridor_rows = []
    for cp in selected_corridors:
        c_proj = cp["projection"]
        c = cp["corridor"]
        row_offset = _offset_payload(
            lat,
            lng,
            c_proj["proj_lat"],
            c_proj["proj_lng"],
            c_proj["off_road_m"],
        )
        corridor_rows.append(
            {
                "corridor_id": c["id"],
                "corridor_signature": c["raw_title"],
                "off_road_m": round(float(c_proj["off_road_m"]), 1),
                "chainage_km": round(float(c_proj["global_chainage_m"]) / 1000.0, 3),
                "proj_lat": round(float(c_proj["proj_lat"]), 6),
                "proj_lng": round(float(c_proj["proj_lng"]), 6),
                "path": c.get("points") or [],
                "offset_km": row_offset["offset_km"],
                "offset_cardinal": row_offset["offset_cardinal"],
                "offset_label": row_offset["offset_label"],
            }
        )

    return {
        "available": True,
        "source": "hitl_secure_proximity_v3",
        "query": {"lat": float(lat), "lng": float(lng)},
        "projection": {
            "corridor_id": primary["id"],
            "corridor_signature": primary["raw_title"],
            "off_road_m": round(float(projection["off_road_m"]), 1),
            "chainage_km": round(float(projection["global_chainage_m"]) / 1000.0, 3),
            "proj_lat": round(float(projection["proj_lat"]), 6),
            "proj_lng": round(float(projection["proj_lng"]), 6),
            "offset_km": primary_offset["offset_km"],
            "offset_bearing_deg": primary_offset["offset_bearing_deg"],
            "offset_cardinal": primary_offset["offset_cardinal"],
            "offset_label": primary_offset["offset_label"],
            "scope_mode": "radius" if in_radius else "fallback",
            "scope_count": len(selected_corridors),
            "routes_in_radius": len(in_radius),
            "path": primary["points"],
        },
        "corridors": corridor_rows,
        "trips_base": passing_trips_base,
    }


def prewarm_proximity_engine(secured_data, cache_key=None):
    if not isinstance(secured_data, dict):
        return {"ready": False, "reason": "invalid_secure_dataset"}
    corridors = _get_corridors_cached(secured_data, cache_key=cache_key)
    movement_index = _get_movement_index_cached(secured_data, cache_key=cache_key)
    return {
        "ready": bool(corridors),
        "corridor_count": len(corridors or []),
        "movement_count": len((movement_index or {}).get("entries") or []),
    }


def _evaluate_route_activity(point_base, now_minutes=None):
    if not isinstance(point_base, dict) or not point_base.get("available"):
        return []
    
    live_min = _live_minutes(now_minutes)
    passing_trips = []
    for base in point_base.get("trips_base", []) or []:
        eta_min = base.get("eta_minutes")
        if not isinstance(eta_min, (int, float)):
            continue
        status_info = _classify_trip_eta(eta_min, live_min)
        passing_trips.append(
            {
                "bus_name": base.get("bus_name") or "",
                "reg_no": base.get("reg_no") or "",
                "trip_id": base.get("trip_id"),
                "direction": base.get("direction"),
                "origin": base.get("origin"),
                "destination": base.get("destination"),
                "corridor_signature": base.get("corridor_signature"),
                "corridor_id": base.get("corridor_id"),
                "eta_minutes": base.get("eta_minutes"),
                "eta_clock": base.get("eta_clock"),
                "delta_minutes": status_info["delta"],
                "status": status_info["status"],
                "status_label": status_info["label"],
                "confidence_band": base.get("confidence_band"),
                "confidence_window": base.get("confidence_window") or _confidence_window_label(base.get("confidence_band")),
            }
        )

    weight = {"SOON": 1, "UPCOMING": 2, "JUST_PASSED": 3, "MISSED": 4, "LATER": 5}
    return sorted(
        passing_trips,
        key=lambda item: (weight.get(item["status"], 9), abs(float(item["delta_minutes"] or 0.0)), str(item["bus_name"] or "")),
    )


def resolve_secure_proximity(secured_data, lat, lng, now_minutes=None, cache_key=None):
    if not isinstance(secured_data, dict):
        return {"available": False, "reason": "invalid_secure_dataset"}

    corridors = _get_corridors_cached(secured_data, cache_key=cache_key)
    if not corridors:
        return {"available": False, "reason": "no_secure_corridors"}

    movement_index = _get_movement_index_cached(secured_data, cache_key=cache_key)
    token = _point_cache_token(lat, lng)
    ns = _point_cache_namespace(cache_key)
    point_base = ns.get(token)
    if not isinstance(point_base, dict):
        point_base = _compute_point_base(
            secured_data,
            corridors,
            movement_index,
            float(lat),
            float(lng),
            cache_key=cache_key,
        )
        ns[token] = point_base
        if len(ns) > _PROX_POINT_CACHE_LIMIT:
            oldest = next(iter(ns.keys()))
            if oldest != token:
                ns.pop(oldest, None)
    if not point_base.get("available"):
        return point_base

    evaluated = _evaluate_route_activity(point_base, now_minutes)
    in_radius_scope = str((point_base.get("projection") or {}).get("scope_mode") or "").lower() == "radius"
    if not in_radius_scope:
        upcoming = [item for item in evaluated if item["status"] in {"SOON", "UPCOMING"}]
        display_trips = upcoming if upcoming else evaluated[:20]
    else:
        display_trips = evaluated[:MAX_RADIUS_TRIPS]
    
    return {
        "available": True,
        "source": point_base.get("source") or "hitl_secure_proximity_v3",
        "query": {"lat": float(lat), "lng": float(lng), "live_minutes": _live_minutes(now_minutes)},
        "projection": dict(point_base.get("projection") or {}),
        "corridors": list(point_base.get("corridors") or []),
        "summary": {
            "unique_buses": len({(item["bus_name"], item["reg_no"]) for item in display_trips}),
            "trip_count": len(display_trips),
        },
        "trips": display_trips,
    }


def resolve_journey_planner_proximity(secured_data, origin_lat, origin_lng, dest_lat, dest_lng, now_minutes=None, cache_key=None):
    if not isinstance(secured_data, dict):
        return {"available": False, "reason": "invalid_secure_dataset"}

    corridors = _get_corridors_cached(secured_data, cache_key=cache_key)
    if not corridors:
        return {"available": False, "reason": "no_secure_corridors"}

    movement_index = _get_movement_index_cached(secured_data, cache_key=cache_key)
    ns = _point_cache_namespace(cache_key)

    o_token = f"ORG:{_point_cache_token(origin_lat, origin_lng)}"
    origin_base = ns.get(o_token)
    if not isinstance(origin_base, dict):
        origin_base = _compute_point_base(secured_data, corridors, movement_index, float(origin_lat), float(origin_lng), cache_key=cache_key)
        ns[o_token] = origin_base

    d_token = f"DST:{_point_cache_token(dest_lat, dest_lng)}"
    dest_base = ns.get(d_token)
    if not isinstance(dest_base, dict):
        dest_base = _compute_point_base(secured_data, corridors, movement_index, float(dest_lat), float(dest_lng), cache_key=cache_key)
        ns[d_token] = dest_base

    if not origin_base.get("available") or not dest_base.get("available"):
        return {
            "available": False, 
            "reason": "projection_failed_for_one_or_both_points",
            "origin_available": origin_base.get("available", False),
            "dest_available": dest_base.get("available", False)
        }

    o_corridor_ids = {c["corridor_id"] for c in origin_base.get("corridors", [])}
    d_corridor_ids = {c["corridor_id"] for c in dest_base.get("corridors", [])}
    common_ids = o_corridor_ids.intersection(d_corridor_ids)

    o_evaluated = []
    if origin_base.get("available"):
        o_evaluated = _evaluate_route_activity(origin_base, now_minutes)

    d_evaluated = []
    if dest_base.get("available"):
        d_evaluated = _evaluate_route_activity(dest_base, now_minutes)

    def extract_trips(evaluated):
        if not evaluated:
            return []
        upcoming = [item for item in evaluated if item["status"] in {"SOON", "UPCOMING"}]
        return upcoming if upcoming else evaluated[:20]

    return {
        "available": True,
        "source": "hitl_secure_journey_planner_v1",
        "origin": {
            "query": {"lat": float(origin_lat), "lng": float(origin_lng)},
            "projection": origin_base.get("projection"),
            "corridors": origin_base.get("corridors"),
            "trips": extract_trips(o_evaluated),
            "summary": {
               "unique_buses": len({(item["bus_name"], item["reg_no"]) for item in extract_trips(o_evaluated)}),
               "trip_count": len(extract_trips(o_evaluated))
            }
        },
        "destination": {
            "query": {"lat": float(dest_lat), "lng": float(dest_lng)},
            "projection": dest_base.get("projection"),
            "corridors": dest_base.get("corridors"),
            "trips": extract_trips(d_evaluated),
            "summary": {
               "unique_buses": len({(item["bus_name"], item["reg_no"]) for item in extract_trips(d_evaluated)}),
               "trip_count": len(extract_trips(d_evaluated))
            }
        },
        "common_corridor_ids": list(common_ids)
    }
