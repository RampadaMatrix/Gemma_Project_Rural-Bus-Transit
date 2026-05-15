"""
Village Identifier + Sequence-Locked Geocoder
==============================================
Single entrypoint that:
  1) Loads Villages_data.json (input can be without coordinates)
  2) Geocodes missing coordinates with sequence-locked block bias
  3) Computes village identity attributes
  4) Writes Villages_data_identified.json
"""

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

try:
    import requests
except Exception:  # pragma: no cover - requests may be unavailable in some runtimes
    requests = None


BASE_DIR = Path(__file__).resolve().parent
VILLAGES_FILE = BASE_DIR / "Villages_data.json"
STOPS_FILE = BASE_DIR.parent / "Raptor_data" / "final_raptor_stops.json"
RAPTOR_BUNDLE_FILE = BASE_DIR.parent / "Raptor_data" / "raptor_bundle.json"
BD_FILE = BASE_DIR.parent / "BD_Phase1_HITL_Secured.json"
OUTPUT_FILE = BASE_DIR / "Villages_data_identified.json"
CACHE_FILE = BASE_DIR / "viterbi_api_cache.json"
REUSE_COORDS_FILE = BASE_DIR / "Villages_data_identified.json"

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY") or ""
if not GOOGLE_PLACES_API_KEY:
    raise RuntimeError("Missing Google API key. Set GOOGLE_MAPS_API_KEY (preferred) or GOOGLE_PLACES_API_KEY.")
PURULIA_CENTER = (23.330910, 86.361153)
BIAS_RADIUS_KM = 12.0
GEOCODER_SLEEP_SEC = 0.04

SIGNIFICANT_POP = 1500  # villages with pop >= this are preferred as identifiers
CORRIDOR_TIE_BREAK_KM = 1.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Returns distance in meters between two lat/lon points."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def haversine_km(coord1, coord2):
    return haversine_m(coord1[0], coord1[1], coord2[0], coord2[1]) / 1000.0


def has_valid_coord(v):
    lat = v.get("lat")
    lon = v.get("lon")
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float))


def ensure_full_address(v):
    if not v.get("full_address"):
        v["full_address"] = f"{v.get('name', '')}, {v.get('block', '')}, Purulia, West Bengal, India"


def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        for q, coords in cache.items():
            cache[q] = [tuple(c) for c in coords]
        return cache
    return {}


def save_cache(cache):
    serializable = {k: [list(c) for c in v] for k, v in cache.items()}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def build_geocode_query(village):
    return f"{village.get('name', '')}, {village.get('block', '')}, Puruliya, West Bengal"


def is_city_center_hallucination(candidate, bias_coord):
    if not bias_coord:
        return False
    return haversine_km(candidate, PURULIA_CENTER) < 0.1 and haversine_km(bias_coord, PURULIA_CENTER) > 5.0


def fetch_candidates_from_places(query, bias_coord):
    if not GOOGLE_PLACES_API_KEY or requests is None:
        return []

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_PLACES_API_KEY}
    if bias_coord:
        params["locationbias"] = f"circle:10000@{bias_coord[0]},{bias_coord[1]}"

    try:
        resp = requests.get(url, params=params, timeout=20).json()
    except Exception:
        return []

    fresh = []
    if resp.get("status") == "OK" and resp.get("results"):
        for r in resp["results"][:5]:
            loc = r.get("geometry", {}).get("location", {})
            if "lat" not in loc or "lng" not in loc:
                continue
            cand = (loc["lat"], loc["lng"])
            if is_city_center_hallucination(cand, bias_coord):
                continue
            fresh.append(cand)
    time.sleep(GEOCODER_SLEEP_SEC)
    return fresh


def get_best_coordinate(village, bias_coord, cache):
    query = build_geocode_query(village)
    candidates = cache.get(query, [])

    valid = [c for c in candidates if (not bias_coord) or haversine_km(bias_coord, c) <= BIAS_RADIUS_KM]
    if not valid:
        fresh = fetch_candidates_from_places(query, bias_coord)
        cache[query] = fresh
        candidates = fresh
    else:
        candidates = valid

    final_valid = [c for c in candidates if (not bias_coord) or haversine_km(bias_coord, c) <= BIAS_RADIUS_KM]
    if not final_valid:
        return None

    if bias_coord:
        final_valid.sort(key=lambda c: haversine_km(bias_coord, c))
    return final_valid[0]


def hydrate_coordinates_from_existing_identified(villages):
    if not REUSE_COORDS_FILE.exists():
        return 0

    with open(REUSE_COORDS_FILE, "r", encoding="utf-8") as f:
        existing = json.load(f)

    by_key = defaultdict(list)
    for e in existing:
        lat, lon = e.get("lat"), e.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            k = f"{e.get('name','')}|{e.get('block','')}"
            by_key[k].append((lat, lon))

    hydrated = 0
    for v in villages:
        if has_valid_coord(v):
            continue
        k = f"{v.get('name','')}|{v.get('block','')}"
        coords = by_key.get(k)
        if coords:
            lat, lon = coords[0]
            v["lat"] = lat
            v["lon"] = lon
            hydrated += 1

    return hydrated


def ensure_village_coordinates(villages):
    for v in villages:
        ensure_full_address(v)

    missing = [idx for idx, v in enumerate(villages) if not has_valid_coord(v)]
    if not missing:
        return villages, {"missing_input": 0, "hydrated_from_existing": 0, "geocoded": 0, "cache_file": str(CACHE_FILE)}

    hydrated = hydrate_coordinates_from_existing_identified(villages)
    missing_after_hydrate = [idx for idx, v in enumerate(villages) if not has_valid_coord(v)]
    if not missing_after_hydrate:
        return villages, {
            "missing_input": len(missing),
            "hydrated_from_existing": hydrated,
            "geocoded": 0,
            "cache_file": str(CACHE_FILE),
        }

    allow_geocode_fallback = os.getenv("ALLOW_GEOCODE_FALLBACK", "0") == "1"
    if not allow_geocode_fallback:
        raise RuntimeError(
            f"{len(missing_after_hydrate)} villages still missing coordinates after reuse from existing identified file. "
            "Set ALLOW_GEOCODE_FALLBACK=1 only if you explicitly want fresh API geocoding."
        )

    if requests is None:
        raise RuntimeError("Missing dependency `requests`; cannot geocode villages with missing coordinates.")
    if not GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set and coordinates are missing in input.")

    cache = load_cache()
    geocoded = 0
    unresolved = 0

    blocks = defaultdict(list)
    for i, v in enumerate(villages):
        blocks[v.get("block", "")].append(i)

    for block_name, indexes in blocks.items():
        current_bias = None
        for idx in indexes:
            if has_valid_coord(villages[idx]):
                current_bias = (villages[idx]["lat"], villages[idx]["lon"])
                break

        if current_bias and haversine_km(current_bias, PURULIA_CENTER) < 0.1:
            first_idx = indexes[0]
            fallback = get_best_coordinate(villages[first_idx], None, cache)
            if fallback:
                current_bias = fallback

        for idx in indexes:
            v = villages[idx]
            if has_valid_coord(v):
                if current_bias is None:
                    current_bias = (v["lat"], v["lon"])
                continue

            found = get_best_coordinate(v, current_bias, cache)
            if found:
                v["lat"] = round(found[0], 7)
                v["lon"] = round(found[1], 7)
                current_bias = found
                geocoded += 1
            else:
                unresolved += 1

        if geocoded and geocoded % 100 == 0:
            save_cache(cache)
            print(f"  [geocode] progress: {geocoded} villages geocoded...")

        if block_name:
            print(f"  [geocode] processed block: {block_name}")

    save_cache(cache)

    if unresolved > 0:
        raise RuntimeError(
            f"Could not resolve coordinates for {unresolved} villages. "
            "Fix input names/blocks or run with a valid Google Places API key."
        )

    return villages, {
        "missing_input": len(missing),
        "hydrated_from_existing": hydrated,
        "geocoded": geocoded,
        "cache_file": str(CACHE_FILE),
    }


def load_json(path):
    # Backward/forward compatibility:
    # prefer split stops file when present, otherwise fallback to merged RAPTOR bundle.
    if Path(path) == STOPS_FILE and not STOPS_FILE.exists() and RAPTOR_BUNDLE_FILE.exists():
        with open(RAPTOR_BUNDLE_FILE, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        return (bundle.get("data") or {}).get("stops") or []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def decode_polyline(encoded):
    if not encoded:
        return []
    index = 0
    lat = 0
    lng = 0
    points = []
    while index < len(encoded):
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        points.append({"lat": lat * 1e-5, "lng": lng * 1e-5})
    return points


def bearing_deg(lat1, lon1, lat2, lon2):
    """Bearing from point 1 to point 2, in degrees [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360


def is_right_side(bearing):
    """
    RIGHT = North or East quadrants (bearing 0-180, i.e. NE clockwise to S)
    LEFT  = South or West quadrants (bearing 180-360, i.e. S clockwise to N)
    """
    return 0 <= bearing < 180


def find_directional_neighbors(target_idx, villages):
    """
    Find one significant neighbour on the LEFT (west/south) and one on the RIGHT (north/east).

    Strategy:
      1. Score all candidates: closer + higher-pop = better
      2. Partition into LEFT / RIGHT by bearing
      3. In each partition, prefer pop >= SIGNIFICANT_POP
      4. Pick the best from each side
    """
    target = villages[target_idx]
    t_lat, t_lon = target["lat"], target["lon"]
    t_name = target["name"]
    t_block = target.get("block", "")

    candidates = []
    for i, v in enumerate(villages):
        if i == target_idx:
            continue
        # Skip exact co-located same-name duplicates
        if v["name"] == t_name and v.get("block", "") == t_block:
            if abs(v["lat"] - t_lat) < 1e-5 and abs(v["lon"] - t_lon) < 1e-5:
                continue
        dist = haversine_m(t_lat, t_lon, v["lat"], v["lon"])
        brng = bearing_deg(t_lat, t_lon, v["lat"], v["lon"])
        pop = v.get("population_2026_est", v.get("population_2011", 0))
        candidates.append(
            {
                "idx": i,
                "name": v["name"],
                "block": v.get("block", ""),
                "dist_m": dist,
                "dist_km": round(dist / 1000, 2),
                "bearing": brng,
                "pop": pop,
                "significant": pop >= SIGNIFICANT_POP,
                "right": is_right_side(brng),
            }
        )

    # Sort: significant first, then by distance
    candidates.sort(key=lambda c: (not c["significant"], c["dist_m"]))

    right_pick = None
    left_pick = None
    seen_names = set()

    for c in candidates:
        if c["name"] in seen_names:
            continue
        if c["right"] and right_pick is None:
            right_pick = c
            seen_names.add(c["name"])
        elif not c["right"] and left_pick is None:
            left_pick = c
            seen_names.add(c["name"])
        if right_pick and left_pick:
            break

    # If one side is empty (edge villages), fill with best remaining
    if right_pick is None or left_pick is None:
        for c in candidates:
            if c["name"] in seen_names:
                continue
            if right_pick is None:
                right_pick = c
                seen_names.add(c["name"])
            elif left_pick is None:
                left_pick = c
                seen_names.add(c["name"])
            if right_pick and left_pick:
                break

    results = []
    if left_pick:
        results.append(
            {
                "name": left_pick["name"],
                "block": left_pick["block"],
                "dist_km": left_pick["dist_km"],
                "direction": "W/S",
                "pop": left_pick["pop"],
            }
        )
    if right_pick:
        results.append(
            {
                "name": right_pick["name"],
                "block": right_pick["block"],
                "dist_km": right_pick["dist_km"],
                "direction": "E/N",
                "pop": right_pick["pop"],
            }
        )
    return results


def build_corridor_index(bd_data):
    """
    Build a spatial index of corridors from BD_Phase1_HITL_Secured.json.

    Uses decoded polyline geometry plus movement stops for robust nearest-corridor lookup.
    """
    corr_map = {}  # corridor_id -> { sig, coords, movement_count, bus_names }
    for bus in bd_data.get("buses", []):
        bus_name = bus.get("bus_name") or bus.get("reg_no") or bus.get("id") or "unknown_bus"
        for mov in bus.get("movements", []):
            cid = mov.get("corridor_id")
            sig = mov.get("corridor_signature")
            if not cid or not sig:
                continue

            if cid not in corr_map:
                corr_map[cid] = {
                    "corridor_id": cid,
                    "corridor_signature": sig,
                    "coords": [],
                    "movement_count": 0,
                    "bus_names": set(),
                }

            corr = corr_map[cid]
            corr["movement_count"] += 1
            corr["bus_names"].add(bus_name)

            for s in (mov.get("stops", []) or []):
                if "lat" in s and "lng" in s:
                    corr["coords"].append({"lat": s["lat"], "lng": s["lng"], "name": s.get("name", "")})

            encoded = (mov.get("route") or {}).get("polyline")
            poly_pts = decode_polyline(encoded)
            if poly_pts:
                stride = max(1, len(poly_pts) // 200)
                for i in range(0, len(poly_pts), stride):
                    pt = poly_pts[i]
                    corr["coords"].append({"lat": pt["lat"], "lng": pt["lng"], "name": ""})

    corridors = []
    for corr in corr_map.values():
        if not corr["coords"]:
            continue
        dedup = {}
        for pt in corr["coords"]:
            k = f"{round(pt['lat'], 5)}|{round(pt['lng'], 5)}"
            dedup[k] = pt
        corr["coords"] = list(dedup.values())
        corr["bus_count"] = len(corr["bus_names"])
        corr.pop("bus_names", None)
        corridors.append(corr)
    return corridors


def find_nearest_corridor_signature(village, corridors):
    """
    Find the nearest corridor using full indexed geometry.

    If multiple corridors are near-equidistant, prefer higher-frequency corridors.
    """
    t_lat, t_lon = village["lat"], village["lon"]
    rankings = []
    for corr in corridors:
        corr_best = float("inf")
        for pt in corr["coords"]:
            dist = haversine_m(t_lat, t_lon, pt["lat"], pt["lng"])
            if dist < corr_best:
                corr_best = dist
        if corr_best < float("inf"):
            rankings.append(
                {
                    "corridor": corr,
                    "best_dist_m": corr_best,
                    "movement_count": corr.get("movement_count", 0),
                    "bus_count": corr.get("bus_count", 0),
                }
            )

    if rankings:
        rankings.sort(key=lambda r: r["best_dist_m"])
        best_dist = rankings[0]["best_dist_m"]
        competitive = [
            r for r in rankings if r["best_dist_m"] <= best_dist + (CORRIDOR_TIE_BREAK_KM * 1000.0)
        ]
        competitive.sort(key=lambda r: (-r["movement_count"], -r["bus_count"], r["best_dist_m"]))
        best = competitive[0]
        best_corr = best["corridor"]
        return {
            "corridor_signature": best_corr["corridor_signature"],
            "corridor_id": best_corr["corridor_id"],
            "dist_km": round(best["best_dist_m"] / 1000, 2),
            "movement_count": best["movement_count"],
            "bus_count": best["bus_count"],
        }
    return None


def find_nearest_stop(village, stops):
    """Find the nearest RAPTOR transit stop."""
    t_lat, t_lon = village["lat"], village["lon"]
    best_dist = float("inf")
    best_stop = None

    for s in stops:
        dist = haversine_m(t_lat, t_lon, s["lat"], s["lng"])
        if dist < best_dist:
            best_dist = dist
            best_stop = s

    if best_stop:
        return {
            "stop_name": best_stop["name"],
            "stop_id": best_stop["stop_id"],
            "dist_km": round(best_dist / 1000, 2),
        }
    return None


def build_unique_label(village, neighbors, corridor, nearest_stop):
    """
    Format:
      "Chipida (Block: Purulia-I | << Panrasol, Baraghutu >> | corridor: Kalabani-Surulia-Purulia | stop: Palma/Bhadsa More 2.75km | pop(est): 2129)"
    """
    name = village["name"]
    block = village.get("block", "Unknown")
    pop = village.get("population_2026_est", village.get("population_2011", 0))

    # Direction arrows for neighbors
    left_neigh = [n for n in neighbors if n.get("direction") == "W/S"]
    right_neigh = [n for n in neighbors if n.get("direction") == "E/N"]
    left_str = left_neigh[0]["name"] if left_neigh else "--"
    right_str = right_neigh[0]["name"] if right_neigh else "--"

    corr_str = corridor["corridor_signature"] if corridor else "--"
    stop_str = f"{nearest_stop['stop_name']} {nearest_stop['dist_km']}km" if nearest_stop else "--"

    return (
        f"{name} (Block: {block} | << {left_str}, {right_str} >> | corridor: {corr_str} "
        f"| stop: {stop_str} | pop(est): {pop})"
    )


def run():
    print("=" * 60)
    print("Village Identifier -- Integrated Geocoder + Directional + Corridor Signatures")
    print("=" * 60)

    print("\n[1/5] Loading data...")
    villages = load_json(VILLAGES_FILE)
    print(f"  Villages: {len(villages)}")

    villages, geocode_stats = ensure_village_coordinates(villages)
    print(
        "  Coordinates ready:"
        f" missing_in_input={geocode_stats['missing_input']},"
        f" hydrated_from_existing={geocode_stats.get('hydrated_from_existing', 0)},"
        f" geocoded_now={geocode_stats['geocoded']}"
    )

    stops = load_json(STOPS_FILE)
    print(f"  RAPTOR stops: {len(stops)}")

    bd_data = load_json(BD_FILE)
    corridors = build_corridor_index(bd_data)
    print(f"  Corridors indexed: {len(corridors)}")

    # Deduplicate villages (same name + block + coords)
    seen = set()
    unique_villages = []
    for v in villages:
        key = f"{v['name']}|{v.get('block', '')}|{round(v['lat'], 5)}|{round(v['lon'], 5)}"
        if key not in seen:
            seen.add(key)
            unique_villages.append(v)
    removed = len(villages) - len(unique_villages)
    if removed > 0:
        print(f"  Removed {removed} exact duplicates -> {len(unique_villages)} unique villages.")
    villages = unique_villages

    sig_count = sum(
        1 for v in villages if v.get("population_2026_est", v.get("population_2011", 0)) >= SIGNIFICANT_POP
    )
    print(f"  Significant villages (pop >= {SIGNIFICANT_POP}): {sig_count}")

    print("\n[2/5] Computing identifiers...")
    enriched = []
    for i, v in enumerate(villages):
        if i % 500 == 0:
            print(f"  Processing {i}/{len(villages)}...")

        neighbors = find_directional_neighbors(i, villages)
        corridor = find_nearest_corridor_signature(v, corridors)
        nearest_stop = find_nearest_stop(v, stops)
        label = build_unique_label(v, neighbors, corridor, nearest_stop)

        entry = {k: val for k, val in v.items() if k not in ["population_2026_est", "Population(est):"]}
        entry["nearby_villages"] = neighbors
        entry["nearest_corridor"] = corridor
        entry["nearest_stop"] = nearest_stop
        entry["unique_label"] = label
        enriched.append(entry)

    print("\n[3/5] Resolving label collisions...")
    labels = [e["unique_label"] for e in enriched]
    unique_labels = set(labels)
    if len(labels) != len(unique_labels):
        from collections import Counter

        counts = Counter(labels)
        dupes = {k for k, v in counts.items() if v > 1}
        dupe_counters = {d: 0 for d in dupes}
        for e in enriched:
            if e["unique_label"] in dupes:
                dupe_counters[e["unique_label"]] += 1
                if dupe_counters[e["unique_label"]] > 1:
                    e["unique_label"] += f" #{dupe_counters[e['unique_label']]}"
        print(f"  Resolved {len(dupes)} label collisions with index suffixes.")
    else:
        print(f"  All {len(labels)} labels are unique -- no collisions!")

    print("\n[4/5] Saving output...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(enriched)} villages -> {OUTPUT_FILE}")

    print("\n[5/5] Sample output...")
    gobindapurs = [e for e in enriched if e["name"] == "Gobindapur"]
    print(f"  Gobindapur variants: {len(gobindapurs)}")
    for g in gobindapurs[:3]:
        print(f"    {g['unique_label']}")

    chipidas = [e for e in enriched if e["name"] == "Chipida"]
    if chipidas:
        print("  Chipida sample:")
        print(json.dumps(chipidas[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run()





