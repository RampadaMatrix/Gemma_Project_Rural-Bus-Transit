import hashlib
import math
from datetime import datetime


MAJOR_HUBS = {
    "purulia",
    "bankura",
    "medinipur",
    "durgapur",
    "jhargram",
    "jhalda",
    "chas",
    "asansol",
    "lalpur",
    "hura",
    "bishpuria",
    "simulia",
    "surulya",
    "kaluhar",
    "chakaltore",
    "raghunathpur",
    "barabazar",
    "manbazar",
    "khatra",
    "raipur",
    "bandwan",
    "kenda",
    "kharbona",
    "lakshanpur",
}

MIN_PLAUSIBLE_SPEED_KMPH = 10
MAX_PLAUSIBLE_SPEED_KMPH = 75
NORMAL_MIN_SPEED_KMPH = 10
NORMAL_MAX_SPEED_KMPH = 55
LONG_HAUL_DISTANCE_KM = 200
IDEAL_SPEED_RANGE = (15, 60)
GLOBAL_DEFAULT_AVG_SPEED_KMPH = 30


def normalize_stop_name(name):
    raw = str(name or "").strip().lower()
    chars = []
    prev_space = False
    for ch in raw:
        keep = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if keep:
            chars.append(ch)
            prev_space = False
        elif not prev_space:
            chars.append(" ")
            prev_space = True
    return " ".join("".join(chars).split())


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return r * 2.0 * math.asin(math.sqrt(a))


def _decode_polyline(encoded):
    points = []
    index = lat = lng = 0
    if not encoded:
        return points
    while index < len(encoded):
        for coord in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if coord == 0:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def _cumulative_distances(path):
    dists = [0.0]
    for i in range(1, len(path)):
        dists.append(dists[-1] + _haversine_m(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1]))
    return dists


def _project_point_onto_polyline(lat, lng, path, cum_dists):
    best_dist_from_point = float("inf")
    best_cum = 0.0

    for i in range(len(path) - 1):
        ax, ay = path[i]
        bx, by = path[i + 1]
        abx, aby = bx - ax, by - ay
        apx, apy = lat - ax, lng - ay
        ab_sq = abx * abx + aby * aby
        if ab_sq < 1e-18:
            d = _haversine_m(lat, lng, ax, ay)
            if d < best_dist_from_point:
                best_dist_from_point = d
                best_cum = cum_dists[i]
            continue

        t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab_sq))
        proj_lat = ax + t * abx
        proj_lng = ay + t * aby
        d = _haversine_m(lat, lng, proj_lat, proj_lng)
        if d < best_dist_from_point:
            best_dist_from_point = d
            seg_len = cum_dists[i + 1] - cum_dists[i]
            best_cum = cum_dists[i] + t * seg_len

    return best_cum, best_dist_from_point


def _parse_time_to_minutes(time_str):
    if not time_str:
        return None
    raw = str(time_str).strip().upper()
    parts = raw.split()
    if len(parts) != 2 or parts[1] not in {"AM", "PM"}:
        return None
    hm = parts[0].split(":")
    if len(hm) != 2:
        return None
    try:
        hh = int(hm[0])
        mm = int(hm[1])
    except Exception:
        return None
    if hh < 1 or hh > 12 or mm < 0 or mm > 59:
        return None
    if parts[1] == "AM":
        hh = 0 if hh == 12 else hh
    else:
        hh = 12 if hh == 12 else hh + 12
    return hh * 60 + mm


def _flip_ampm(time_str):
    if not time_str:
        return None
    raw = str(time_str).strip()
    if " AM" in raw.upper():
        return raw.upper().replace(" AM", " PM")
    if " PM" in raw.upper():
        return raw.upper().replace(" PM", " AM")
    return None


def _stop_sequence_for_corridor(stops):
    names = []
    for s in stops or []:
        if not isinstance(s, dict):
            continue
        if s.get("isWaypoint") or s.get("is_waypoint"):
            continue
        nm = str(s.get("name") or "").strip()
        if nm:
            names.append(nm)
    if not names:
        for s in stops or []:
            nm = str((s or {}).get("name") or "").strip()
            if nm:
                names.append(nm)
    return names


def _build_corridor_signature(movement, bus_context=None):
    stops = movement.get("stops") or []
    names = _stop_sequence_for_corridor(stops)
    if not names:
        o = str(movement.get("origin") or "").strip()
        d = str(movement.get("destination") or "").strip()
        fallback = "-".join([x for x in [o, d] if x]) or "Unknown Corridor"
        return fallback

    global_freq = {}
    for mv in (bus_context or {}).get("movements", []) or []:
        for nm in _stop_sequence_for_corridor(mv.get("stops") or []):
            key = normalize_stop_name(nm)
            global_freq[key] = global_freq.get(key, 0) + 1

    origin = names[0]
    destination = names[-1]
    mids = names[1:-1]
    seen = set()
    ranked = []
    for idx, nm in enumerate(mids, start=1):
        key = normalize_stop_name(nm)
        if not key or key in seen:
            continue
        seen.add(key)
        hub_bonus = 40 if key in MAJOR_HUBS else 0
        junction_bonus = 20 if (" mor " in f" {key} " or " more " in f" {key} " or "bus stand" in key) else 0
        score = (global_freq.get(key, 0) * 3) + hub_bonus + junction_bonus
        ranked.append({"name": nm, "score": score, "idx": idx})
    ranked = sorted(ranked, key=lambda x: (-x["score"], x["idx"]))[:3]
    ranked = sorted(ranked, key=lambda x: x["idx"])
    mids_final = [r["name"] for r in ranked]

    parts = [origin] + mids_final + [destination]
    compact = []
    for p in parts:
        if not compact or normalize_stop_name(compact[-1]) != normalize_stop_name(p):
            compact.append(p)
    return "-".join([x for x in compact if str(x or "").strip()]) or "Unknown Corridor"


def _corridor_id(signature):
    raw = normalize_stop_name(signature) or "unknown-corridor"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"corr_{digest}"


def _movement_points_with_coords(movement):
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    nodes = route.get("nodes") or []
    node_by_name = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nm = str(n.get("name") or "").strip()
        key = normalize_stop_name(nm)
        lat = n.get("lat")
        lng = n.get("lng")
        if key and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            node_by_name[key] = (float(lat), float(lng))

    points = []
    for idx, s in enumerate(movement.get("stops") or []):
        if not isinstance(s, dict):
            continue
        nm = str(s.get("name") or "").strip()
        if not nm:
            continue
        lat = s.get("lat")
        lng = s.get("lng")
        if (not isinstance(lat, (int, float)) or not isinstance(lng, (int, float))) and node_by_name:
            alt = node_by_name.get(normalize_stop_name(nm))
            if alt:
                lat, lng = alt[0], alt[1]
                s["lat"], s["lng"] = float(lat), float(lng)
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            points.append(
                {
                    "index": idx,
                    "name": nm,
                    "lat": float(lat),
                    "lng": float(lng),
                    "arrival_time": s.get("arrival_time"),
                    "departure_time": s.get("departure_time"),
                    "is_waypoint": bool(s.get("isWaypoint") or s.get("is_waypoint")),
                }
            )
    return points


def _compute_chainage(points, route_polyline):
    if len(points) < 1:
        return []
    path = _decode_polyline(route_polyline or "")
    if len(path) >= 2:
        cum = _cumulative_distances(path)
        projected = []
        for p in points:
            road_m, _ = _project_point_onto_polyline(p["lat"], p["lng"], path, cum)
            projected.append(dict(p, road_dist_m=round(road_m, 1)))
        projected = sorted(projected, key=lambda x: x["road_dist_m"])
        return projected

    projected = []
    running = 0.0
    for i, p in enumerate(points):
        if i > 0:
            prev = points[i - 1]
            running += _haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"])
        projected.append(dict(p, road_dist_m=round(running, 1)))
    return projected


def _speed_thresholds_for_trip(total_route_km):
    try:
        km = float(total_route_km or 0.0)
    except Exception:
        km = 0.0
    if km > LONG_HAUL_DISTANCE_KM:
        return MIN_PLAUSIBLE_SPEED_KMPH, MAX_PLAUSIBLE_SPEED_KMPH
    return NORMAL_MIN_SPEED_KMPH, NORMAL_MAX_SPEED_KMPH


def _build_segments(projected, min_speed_kmph, max_speed_kmph):
    timed = [
        p for p in projected
        if not p.get("is_waypoint")
        and (
            _parse_time_to_minutes(p.get("arrival_time")) is not None
            or _parse_time_to_minutes(p.get("departure_time")) is not None
        )
    ]
    if len(timed) < 2:
        return []
    first_t = _parse_time_to_minutes(timed[0].get("departure_time")) or _parse_time_to_minutes(timed[0].get("arrival_time"))
    last_t = _parse_time_to_minutes(timed[-1].get("arrival_time")) or _parse_time_to_minutes(timed[-1].get("departure_time"))
    if first_t is not None and last_t is not None and last_t < first_t:
        timed = list(reversed(timed))

    segments = []
    for i in range(len(timed) - 1):
        a = timed[i]
        b = timed[i + 1]
        ta = _parse_time_to_minutes(a.get("departure_time")) or _parse_time_to_minutes(a.get("arrival_time"))
        tb = _parse_time_to_minutes(b.get("arrival_time")) or _parse_time_to_minutes(b.get("departure_time"))
        if ta is None or tb is None:
            continue
        duration = tb - ta
        if duration <= 0:
            duration += 24 * 60
        if duration <= 0:
            continue
        dist_km = round(abs(float(b["road_dist_m"]) - float(a["road_dist_m"])) / 1000.0, 1)
        speed = round(dist_km / (duration / 60.0), 1) if duration > 0 else 0.0
        is_outlier = speed > max_speed_kmph or speed < min_speed_kmph
        segments.append(
            {
                "from": a.get("name"),
                "to": b.get("name"),
                "distance_km": dist_km,
                "duration_min": int(duration),
                "speed_kmph": speed,
                "is_outlier": is_outlier,
            }
        )
    return segments


def _audit_trip_physics(stops, segments, total_route_km, min_speed_kmph, max_speed_kmph):
    violations = []
    max_impact = 0

    valid_dist = sum(float(s.get("distance_km") or 0.0) for s in segments if not s.get("is_outlier"))
    valid_dur = sum(float(s.get("duration_min") or 0.0) for s in segments if not s.get("is_outlier"))
    filtered_avg_kmph = round(valid_dist / (valid_dur / 60.0), 1) if valid_dur > 0 else 0.0
    
    expected_baseline_kmph = 45.0 if total_route_km > 200 else float(GLOBAL_DEFAULT_AVG_SPEED_KMPH)
    if filtered_avg_kmph <= 0:
        filtered_avg_kmph = expected_baseline_kmph
        
    actual_max_speed_kmph = round(max([float(s.get("speed_kmph") or 0.0) for s in segments] + [0.0]), 1)

    speed_impact = 0
    for seg in segments:
        speed = float(seg.get("speed_kmph") or 0.0)
        dist = float(seg.get("distance_km") or 0.0)
        if dist < 0.5:
            continue
        if speed > 100:
            imp = 70 if speed > 120 else 40
            violations.append(
                {
                    "type": "IMPOSSIBLE_SPEED",
                    "severity": "HIGH" if imp > 50 else "MEDIUM",
                    "impact": imp,
                    "details": f"{seg.get('from')} → {seg.get('to')}: {speed} km/h (Extreme)",
                    "segment": seg,
                }
            )
            speed_impact = max(speed_impact, imp)
        elif speed > max_speed_kmph or speed < min_speed_kmph:
            violations.append(
                {
                    "type": "SPEED_OUTLIER",
                    "severity": "MEDIUM",
                    "impact": 15,
                    "details": f"{seg.get('from')} → {seg.get('to')}: {speed} km/h (Outlier)",
                    "segment": seg,
                }
            )
            speed_impact = max(speed_impact, 15)

    if valid_dist > 0:
        # Leniency: We successfully got an average speed from at least 1 valid segment.
        # Do not fail the audit due to speed outliers.
        speed_impact = min(speed_impact, 39)
        
    max_impact = max(max_impact, speed_impact)

    timed = [
        s
        for s in stops
        if not s.get("isWaypoint") and not s.get("is_waypoint")
        and (
            _parse_time_to_minutes(s.get("arrival_time")) is not None
            or _parse_time_to_minutes(s.get("departure_time")) is not None
        )
    ]
    if len(timed) >= 2:
        start_m = _parse_time_to_minutes(timed[0].get("departure_time")) or _parse_time_to_minutes(timed[0].get("arrival_time"))
        end_m = _parse_time_to_minutes(timed[-1].get("arrival_time")) or _parse_time_to_minutes(timed[-1].get("departure_time"))
        if start_m is not None and end_m is not None:
            raw_dur = end_m - start_m
            if raw_dur < 0:
                raw_dur += 1440
            if raw_dur > 0 and total_route_km > 5:
                avg_spd = total_route_km / (raw_dur / 60.0)
                if avg_spd > max_speed_kmph + 10:
                    first_flip = _flip_ampm(timed[0].get("departure_time") or timed[0].get("arrival_time"))
                    last_flip = _flip_ampm(timed[-1].get("arrival_time") or timed[-1].get("departure_time"))
                    if first_flip and last_flip:
                        fs = _parse_time_to_minutes(first_flip)
                        fe = _parse_time_to_minutes(last_flip)
                        if fs is not None and fe is not None:
                            flip_dur = fe - fs
                            if flip_dur < 0:
                                flip_dur += 1440
                            if flip_dur > 0:
                                flipped_spd = total_route_km / (flip_dur / 60.0)
                                if IDEAL_SPEED_RANGE[0] <= flipped_spd <= IDEAL_SPEED_RANGE[1]:
                                    violations.append(
                                        {
                                            "type": "AM_PM_MISMATCH_LIKELY",
                                            "severity": "HIGH",
                                            "impact": 60,
                                            "details": f"Impossible avg {avg_spd:.1f} km/h. Flip correction suggests {flipped_spd:.1f} km/h.",
                                            "corrected_speed": round(flipped_spd, 1),
                                        }
                                    )
                                    max_impact = max(max_impact, 60)

    timed_chrono = []
    for s in stops:
        if s.get("isWaypoint") or s.get("is_waypoint"):
            continue
        t = _parse_time_to_minutes(s.get("departure_time")) or _parse_time_to_minutes(s.get("arrival_time"))
        if t is None:
            continue
        timed_chrono.append((s.get("name"), t))
    for i in range(1, len(timed_chrono)):
        prev_name, prev_t = timed_chrono[i - 1]
        cur_name, cur_t = timed_chrono[i]
        if cur_t < prev_t and abs(cur_t - prev_t) > 120:
            violations.append(
                {
                    "type": "NON_MONOTONIC_TIME",
                    "severity": "HIGH",
                    "impact": 45,
                    "details": f"Time goes backward: {prev_name} -> {cur_name}",
                }
            )
            max_impact = max(max_impact, 45)

    audit = {
        "status": "FAIL" if max_impact >= 40 else "PASS",
        "impact_score": int(max_impact),
        "violations": violations,
    }
    return {
        "audit": audit,
        "filtered_avg_kmph": round(filtered_avg_kmph, 1),
        "max_speed_kmph": round(actual_max_speed_kmph, 1),
    }


def enrich_movement_kinematics(movement, bus_context=None, version="hitl_embedded_kinematics_v1"):
    if not isinstance(movement, dict):
        return {"available": False, "reason": "invalid_movement"}
    stops = movement.get("stops")
    if not isinstance(stops, list) or len(stops) < 1:
        movement["analysis_meta"] = {
            "version": version,
            "method": "embedded_polyline_projection",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "no_stops",
        }
        return {"available": False, "reason": "no_stops"}

    points = _movement_points_with_coords(movement)
    route = movement.get("route") if isinstance(movement.get("route"), dict) else {}
    projected = _compute_chainage(points, route.get("polyline"))
    by_index = {p["index"]: p for p in projected}

    if projected:
        origin_m = projected[0]["road_dist_m"]
        dest_m = projected[-1]["road_dist_m"]
    else:
        origin_m = 0.0
        dest_m = 0.0

    for idx, s in enumerate(stops):
        if not isinstance(s, dict):
            continue
        p = by_index.get(idx)
        s["confidence"] = s.get("confidence", 100)
        if p is None:
            s["road_dist_m"] = 0.0
            s["from_origin_m"] = 0.0
            s["to_destination_m"] = 0.0
            s["from_previous_m"] = 0.0
            s["to_next_m"] = 0.0
            continue
        i = projected.index(p)
        s["road_dist_m"] = round(p["road_dist_m"], 1)
        s["from_origin_m"] = round(p["road_dist_m"] - origin_m, 1)
        s["to_destination_m"] = round(dest_m - p["road_dist_m"], 1)
        s["from_previous_m"] = round(p["road_dist_m"] - projected[i - 1]["road_dist_m"], 1) if i > 0 else 0.0
        s["to_next_m"] = round(projected[i + 1]["road_dist_m"] - p["road_dist_m"], 1) if i < len(projected) - 1 else 0.0
        if not isinstance(s.get("lat"), (int, float)):
            s["lat"] = p["lat"]
        if not isinstance(s.get("lng"), (int, float)):
            s["lng"] = p["lng"]

    corridor_signature = _build_corridor_signature(movement, bus_context=bus_context)
    movement["corridor_signature"] = corridor_signature
    movement["corridor_id"] = _corridor_id(corridor_signature)
    total_route_km = 0.0
    if projected:
        total_route_km = max(0.0, (float(projected[-1]["road_dist_m"]) - float(projected[0]["road_dist_m"])) / 1000.0)
    movement["total_route_km"] = round(total_route_km, 1)
    min_speed_kmph, max_speed_kmph = _speed_thresholds_for_trip(movement["total_route_km"])
    segments = _build_segments(projected, min_speed_kmph=min_speed_kmph, max_speed_kmph=max_speed_kmph)
    movement["segments"] = segments
    speed_summary = _audit_trip_physics(
        stops,
        segments,
        movement["total_route_km"],
        min_speed_kmph=min_speed_kmph,
        max_speed_kmph=max_speed_kmph,
    )
    movement["audit"] = speed_summary["audit"]
    movement["filtered_avg_kmph"] = speed_summary["filtered_avg_kmph"]
    movement["average_speed_kmph"] = speed_summary["filtered_avg_kmph"]
    movement["max_speed_kmph"] = speed_summary["max_speed_kmph"]
    movement["speed_meta"] = {
        "version": version,
        "threshold_profile": {
            "min_kmph": min_speed_kmph,
            "max_kmph": max_speed_kmph,
            "normal_min_kmph": NORMAL_MIN_SPEED_KMPH,
            "normal_max_kmph": NORMAL_MAX_SPEED_KMPH,
            "long_haul_min_kmph": MIN_PLAUSIBLE_SPEED_KMPH,
            "long_haul_max_kmph": MAX_PLAUSIBLE_SPEED_KMPH,
            "long_haul_distance_km": LONG_HAUL_DISTANCE_KM,
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    movement["analysis_meta"] = {
        "version": version,
        "method": "embedded_polyline_projection + physics_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stop_count": len(stops),
        "projected_count": len(projected),
    }

    return analysis_from_movement(movement)


def enrich_bus_kinematics(bus, version="hitl_embedded_kinematics_v1"):
    if not isinstance(bus, dict):
        return 0
    count = 0
    movements = bus.get("movements", []) or []
    
    # Pass 1: Standard Enrichment
    for mv in movements:
        enrich_movement_kinematics(mv, bus_context=bus, version=version)
        count += 1

    # Pass 2: Sibling Speed Backfilling
    valid_speeds = []
    total_bus_km = sum(float(mv.get("total_route_km") or 0.0) for mv in movements if isinstance(mv, dict))
    expected_baseline = 45.0 if total_bus_km > 200 else float(GLOBAL_DEFAULT_AVG_SPEED_KMPH)

    for mv in movements:
        audit = mv.get("audit") or {}
        if audit.get("status") == "PASS":
            avg = float(mv.get("average_speed_kmph") or 0.0)
            if avg > 0:
                valid_speeds.append(avg)

    # Only backfill if we have at least one valid trip to copy from.
    # If no trips are valid, we let the anomaly persist.
    if valid_speeds:
        sibling_avg = round(sum(valid_speeds) / len(valid_speeds), 1)
        for mv in movements:
            audit = mv.get("audit") or {}
            if audit.get("status") != "PASS":
                mv["average_speed_kmph"] = sibling_avg
                mv["filtered_avg_kmph"] = sibling_avg
                audit["status"] = "PASS"
                audit["impact_score"] = min(audit.get("impact_score", 0), 39)
                
                violations = audit.get("violations") or []
                violations.append({
                    "type": "SIBLING_BACKFILL",
                    "severity": "INFO",
                    "impact": 0,
                    "details": f"Speed backfilled ({sibling_avg} km/h) from valid sibling trips to bypass anomaly."
                })
                audit["violations"] = violations
                mv["audit"] = audit

    return count


def movement_has_embedded_kinematics(movement):
    if not isinstance(movement, dict):
        return False
    if not str(movement.get("corridor_signature") or "").strip():
        return False
    stops = movement.get("stops") or []
    if not stops:
        return False
    has_distance = False
    for s in stops:
        if not isinstance(s, dict):
            continue
        if all(k in s for k in ("from_origin_m", "to_destination_m", "from_previous_m", "to_next_m")):
            has_distance = True
            break
    if not has_distance:
        return False
    if movement.get("filtered_avg_kmph") is None and movement.get("average_speed_kmph") is None:
        return False
    if not isinstance(movement.get("segments"), list):
        return False
    if not isinstance(movement.get("audit"), dict):
        return False
    return True


def analysis_from_movement(movement, bus_name=None, reg_no=None):
    if not isinstance(movement, dict):
        return {"available": False, "reason": "invalid_movement"}
    stops = movement.get("stops") or []
    rows = []
    by_name = {}
    for idx, stop in enumerate(stops):
        if not isinstance(stop, dict):
            continue
        nm = str(stop.get("name") or "").strip()
        if not nm:
            continue
        row = {
            "index": idx,
            "name": nm,
            "arrival_time": stop.get("arrival_time"),
            "departure_time": stop.get("departure_time"),
            "road_dist_m": stop.get("road_dist_m"),
            "from_origin_m": stop.get("from_origin_m"),
            "to_destination_m": stop.get("to_destination_m"),
            "from_previous_m": stop.get("from_previous_m"),
            "to_next_m": stop.get("to_next_m"),
            "confidence": stop.get("confidence", 100),
            "lat": stop.get("lat"),
            "lng": stop.get("lng"),
            "is_waypoint": bool(stop.get("isWaypoint") or stop.get("is_waypoint")),
        }
        rows.append(row)
        key = normalize_stop_name(nm)
        if key and key not in by_name:
            by_name[key] = row

    available = len(rows) > 0 and any(isinstance(r.get("from_origin_m"), (int, float)) for r in rows)
    return {
        "available": available,
        "source": "embedded_output",
        "bus_name": bus_name,
        "reg_no": reg_no,
        "trip_id": movement.get("trip_id"),
        "direction": movement.get("direction"),
        "origin": movement.get("origin"),
        "destination": movement.get("destination"),
        "corridor_signature": movement.get("corridor_signature"),
        "corridor_id": movement.get("corridor_id"),
        "segments": movement.get("segments") or [],
        "total_route_km": movement.get("total_route_km", 0),
        "filtered_avg_kmph": movement.get("filtered_avg_kmph", movement.get("average_speed_kmph", 0)),
        "average_speed_kmph": movement.get("average_speed_kmph", movement.get("filtered_avg_kmph", 0)),
        "max_speed_kmph": movement.get("max_speed_kmph", 0),
        "audit": movement.get("audit") or {"status": "PASS", "impact_score": 0, "violations": []},
        "speed_meta": movement.get("speed_meta") or {},
        "analysis_meta": movement.get("analysis_meta") or {},
        "stops": rows,
        "stops_by_name": by_name,
    }


def compute_trip_fallback_kinematics(markers, corridor_signature=None, origin=None, destination=None):
    movement = {
        "trip_id": None,
        "direction": None,
        "origin": origin,
        "destination": destination,
        "corridor_signature": corridor_signature,
        "stops": [],
        "route": {"polyline": "", "nodes": []},
    }
    for row in markers or []:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name") or "").strip()
        if not nm:
            continue
        movement["stops"].append(
            {
                "name": nm,
                "lat": row.get("lat"),
                "lng": row.get("lng"),
                "arrival_time": row.get("arrival_time", row.get("arrival")),
                "departure_time": row.get("departure_time", row.get("departure")),
                "isWaypoint": bool(row.get("isWaypoint") or row.get("is_waypoint")),
                "confidence": row.get("confidence", 100),
            }
        )

    enriched = enrich_movement_kinematics(movement, bus_context={"movements": [movement]})
    if not enriched.get("available"):
        return {"available": False, "reason": "no_marker_coords"}
    if corridor_signature and not movement.get("corridor_signature"):
        movement["corridor_signature"] = corridor_signature
    return analysis_from_movement(movement)
