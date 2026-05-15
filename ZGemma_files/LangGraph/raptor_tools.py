"""
RAPTOR Journey Planner Tools for Gemma Agent
=============================================
Bridges the Gemma 4 31B IT agent to the McRAPTOR transit solver.
Provides name-to-coordinate resolution and time-filtered journey planning.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool

# ── Paths ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_RAPTOR_DATA_DIR = _PROJECT_ROOT / "HITL_Pipeline_new" / "Raptor_data"
_VILLAGES_PATH = _PROJECT_ROOT / "HITL_Pipeline_new" / "Villages_data" / "Villages_data_identified.json"

# ── Singleton RAPTOR Engine ──
# Lazy-loaded on first tool call to avoid startup penalty if never used.
_raptor_router = None
_last_raptor_load_time = 0
_villages_index = None
_stop_name_index = None  # normalized_name -> list of stop objects


def _get_raptor_router():
    """Lazy-loads the RaptorRouter singleton with hot-reloading support."""
    global _raptor_router, _last_raptor_load_time, _stop_name_index
    
    # Check modification time of the primary data file
    stop_times_file = _RAPTOR_DATA_DIR / "raptor_stop_times.json"
    mtime = stop_times_file.stat().st_mtime if stop_times_file.exists() else 0

    if _raptor_router is None or mtime > _last_raptor_load_time:
        import sys
        raptor_dir = str(_RAPTOR_DATA_DIR)
        if raptor_dir not in sys.path:
            sys.path.insert(0, str(_RAPTOR_DATA_DIR.parent))
        from Raptor_data.raptor_solver import RaptorRouter  # type: ignore
        
        _raptor_router = RaptorRouter(str(_RAPTOR_DATA_DIR))
        _last_raptor_load_time = mtime
        _stop_name_index = None  # Force rebuild stop index
        print(f"[RAPTOR TOOLS] Engine {'Hot-Reloaded' if _last_raptor_load_time > 0 else 'Loaded'}: {len(_raptor_router.stops)} stops, {len(_raptor_router.routes)} routes", flush=True)
    
    return _raptor_router


def _get_villages():
    """Lazy-loads the villages spatial index."""
    global _villages_index
    if _villages_index is None:
        if _VILLAGES_PATH.exists():
            with open(_VILLAGES_PATH, "r", encoding="utf-8") as f:
                _villages_index = json.load(f)
            print(f"[RAPTOR TOOLS] Villages loaded: {len(_villages_index)}", flush=True)
        else:
            _villages_index = []
    return _villages_index


def _get_stop_name_index():
    """Builds a normalized name -> stop list index for fast lookup."""
    global _stop_name_index
    if _stop_name_index is None:
        router = _get_raptor_router()
        _stop_name_index = {}
        for sid, stop in router.stops.items():
            name = stop.get("name", "")
            norm = _normalize(name)
            if norm:
                _stop_name_index.setdefault(norm, []).append(stop)
    return _stop_name_index


def _normalize(name: str) -> str:
    """Lowercase, strip special chars, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9\s]", "", str(name or "").lower()).split())


def _parse_time_to_mins(time_str: str) -> int:
    """Parses '3:00 PM' or '15:00' to minutes since midnight."""
    if not time_str:
        return -1
    time_str = str(time_str).strip()
    # Try 12-hour format: "3:00 PM", "03:00 PM"
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_str, re.IGNORECASE)
    if m:
        h, mins, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if period == "PM" and h < 12:
            h += 12
        if period == "AM" and h == 12:
            h = 0
        return h * 60 + mins
    # Try 24-hour format: "15:00"
    m = re.match(r"(\d{1,2}):(\d{2})", time_str)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    # Try bare hour: "3pm", "3 pm"
    m = re.match(r"(\d{1,2})\s*(AM|PM)", time_str, re.IGNORECASE)
    if m:
        h, period = int(m.group(1)), m.group(2).upper()
        if period == "PM" and h < 12:
            h += 12
        if period == "AM" and h == 12:
            h = 0
        return h * 60
    return -1


def _resolve_location(name: str) -> dict:
    """
    Resolves a human-readable location name to lat/lng coordinates.
    
    Resolution order:
    1. Exact match in RAPTOR stop names
    2. Substring match in RAPTOR stop names (prefer CURATED over VIRTUAL)
    3. Exact match in villages data
    4. Substring match in villages data
    5. Return None (let model ask user)
    """
    norm = _normalize(name)
    if not norm:
        return None

    index = _get_stop_name_index()

    # 1. Exact stop name match
    if norm in index:
        stops = index[norm]
        # Prefer CURATED stops over VIRTUAL
        curated = [s for s in stops if s.get("type") == "CURATED"]
        best = curated[0] if curated else stops[0]
        return {
            "name": best["name"],
            "lat": best["lat"],
            "lng": best["lng"],
            "source": "transit_stop",
            "stop_id": best.get("stop_id"),
        }

    # 2. Substring match in stop names
    candidates = []
    for stop_norm, stops in index.items():
        if norm in stop_norm or stop_norm in norm:
            for s in stops:
                candidates.append(s)
    if candidates:
        # Prefer CURATED, then shortest name (most specific)
        curated = [c for c in candidates if c.get("type") == "CURATED"]
        pool = curated if curated else candidates
        pool.sort(key=lambda s: len(s.get("name", "")))
        best = pool[0]
        return {
            "name": best["name"],
            "lat": best["lat"],
            "lng": best["lng"],
            "source": "transit_stop_fuzzy",
            "stop_id": best.get("stop_id"),
        }

    # 3. Exact village match
    villages = _get_villages()
    for v in villages:
        if _normalize(v.get("name")) == norm:
            lat = v.get("lat")
            lng = v.get("lon") or v.get("lng")
            if lat is not None and lng is not None:
                return {
                    "name": v["name"],
                    "lat": lat,
                    "lng": lng,
                    "source": "village",
                    "block": v.get("block"),
                }

    # 4. Substring village match
    village_candidates = []
    for v in villages:
        v_norm = _normalize(v.get("name"))
        if v_norm and (norm in v_norm or v_norm in norm):
            lat = v.get("lat")
            lng = v.get("lon") or v.get("lng")
            if lat is not None and lng is not None:
                village_candidates.append(v)
    if village_candidates:
        # Prefer by population (bigger village = more likely the intended one)
        village_candidates.sort(key=lambda v: int(v.get("population_2011") or 0), reverse=True)
        best = village_candidates[0]
        return {
            "name": best["name"],
            "lat": best["lat"],
            "lng": best.get("lon") or best.get("lng"),
            "source": "village_fuzzy",
            "block": best.get("block"),
        }

    # 5. True Fuzzy Match (Typo Correction)
    import difflib
    
    # Collect all normalized names
    all_names = list(index.keys()) + [_normalize(v.get("name")) for v in villages if _normalize(v.get("name"))]
    all_names = list(set([n for n in all_names if n]))
    
    # Find close matches (65% similarity cutoff)
    close_matches = difflib.get_close_matches(norm, all_names, n=1, cutoff=0.65)
    
    if close_matches:
        best_match = close_matches[0]
        
        # Check if it's a stop
        if best_match in index:
            stops = index[best_match]
            curated = [s for s in stops if s.get("type") == "CURATED"]
            best = curated[0] if curated else stops[0]
            return {
                "name": best["name"],
                "lat": best["lat"],
                "lng": best["lng"],
                "source": "spell_corrected_stop",
                "stop_id": best.get("stop_id"),
                "corrected_from": name
            }
            
        # Check if it's a village
        for v in villages:
            if _normalize(v.get("name")) == best_match:
                lat = v.get("lat")
                lng = v.get("lon") or v.get("lng")
                if lat is not None and lng is not None:
                    return {
                        "name": v["name"],
                        "lat": lat,
                        "lng": lng,
                        "source": "spell_corrected_village",
                        "block": v.get("block"),
                        "corrected_from": name
                    }

    return None


@tool
def find_transit_route(
    origin: str,
    destination: str,
    arrive_by: str = "",
    depart_after: str = "",
):
    """
    Finds bus routes between two locations using the RAPTOR transit solver.
    Resolves location names to coordinates automatically.
    
    Parameters:
    - origin: Starting location name (e.g. "Chipida", "Purulia", "Bankura")
    - destination: Ending location name (e.g. "Bardhaman", "Manbazar")
    - arrive_by: Optional. Filter to routes arriving before this time. Format: "3:00 PM" or "15:00"
    - depart_after: Optional. Filter to routes departing after this time. Format: "10:00 AM" or "10:00"
    
    Use this tool when the user asks about bus routes, travel options, journey planning,
    or how to get from one place to another.
    """
    try:
        # Resolve locations
        origin_loc = _resolve_location(origin)
        if not origin_loc:
            return json.dumps({
                "status": "ORIGIN_NOT_FOUND",
                "message": f"Could not find location '{origin}'. Try a different spelling or use list_transit_stops to search.",
                "origin_query": origin,
            })

        dest_loc = _resolve_location(destination)
        if not dest_loc:
            return json.dumps({
                "status": "DESTINATION_NOT_FOUND",
                "message": f"Could not find location '{destination}'. Try a different spelling or use list_transit_stops to search.",
                "destination_query": destination,
            })

        router = _get_raptor_router()

        # Parse time filters
        arrive_by_mins = _parse_time_to_mins(arrive_by) if arrive_by else -1
        depart_after_mins = _parse_time_to_mins(depart_after) if depart_after else -1

        # If depart_after not specified but user context implies "now", 
        # the model should pass current time explicitly.
        window_start = max(240, depart_after_mins) if depart_after_mins > 0 else 240
        window_end = 1320  # 10 PM

        # Run McRAPTOR solver
        result = router.solve_all_options(
            origin_loc["lat"], origin_loc["lng"],
            dest_loc["lat"], dest_loc["lng"],
            time_window_start=window_start,
            time_window_end=window_end,
        )

        if result.get("status") != "SUCCESS" or not result.get("options"):
            return json.dumps({
                "status": "NO_ROUTE",
                "message": f"No transit route found from {origin_loc['name']} to {dest_loc['name']}.",
                "origin": {"name": origin_loc["name"], "source": origin_loc["source"]},
                "destination": {"name": dest_loc["name"], "source": dest_loc["source"]},
            })

        options = result["options"]

        # Apply arrive_by filter (graceful — never hard-fail)
        all_options = list(options)  # preserve full list for fallback
        time_note = ""
        if arrive_by_mins > 0:
            filtered = [o for o in options if o.get("arrival_mins", 9999) <= arrive_by_mins]
            if filtered:
                options = filtered
            else:
                # Graceful fallback: show closest departures instead of failing
                time_note = f"No buses arrive before {arrive_by}, showing closest available options."
                options = all_options  # keep all, sort by proximity below

        # Dynamically sort to justify the time constraints in the UI
        if depart_after_mins > 0:
            # Sort by departure time closest to the requested depart_after time
            options = sorted(options, key=lambda x: _parse_time_to_mins(x.get("departure_time", "00:00")))
        elif arrive_by_mins > 0:
            # Sort to show the buses that arrive closest to the arrive_by deadline
            options = sorted(options, key=lambda x: x.get("arrival_mins", 9999))
        else:
            # Default sorting: Fastest travel duration
            options = sorted(options, key=lambda x: x.get("duration_mins", 9999))

        # Build compact response (top 6 sorted by user's time constraint)
        compact_options = []
        for opt in options[:6]:
            # Extract bus names from itinerary
            buses = []
            structured_legs = []
            
            for leg in opt.get("itinerary", []):
                if leg.get("type") == "BUS":
                    bus_name = leg.get("bus_name", "Unknown")
                    buses.append(bus_name)
                    structured_legs.append({
                        "type": "BUS",
                        "bus_name": bus_name,
                        "from_stop": leg.get("from_stop", "?"),
                        "to_stop": leg.get("to_stop", "?"),
                        "dep": leg.get("dep", "?"),
                        "arr": leg.get("arr", "?"),
                        "duration_mins": leg.get("duration_mins", 0)
                    })
                elif leg.get("type") == "WALK":
                    dist = leg.get("dist_km", 0)
                    dur = leg.get("duration_mins", 0)
                    from_loc = leg.get("from_stop") or leg.get("from", "?")
                    to_loc = leg.get("to_stop") or leg.get("to", "?")
                    structured_legs.append({
                        "type": "WALK",
                        "from": from_loc,
                        "to": to_loc,
                        "dist_km": dist,
                        "duration_mins": dur
                    })

            compact_options.append({
                "type": opt.get("type", "FASTEST"),
                "departure": opt.get("departure_time", "?"),
                "arrival": opt.get("arrival_time", "?"),
                "duration_mins": opt.get("duration_mins"),
                "transfers": opt.get("transfers", 0),
                "buses": buses,
                "legs": structured_legs,
                "summary": opt.get("summary", ""),
            })

        response_data = {
            "status": "SUCCESS",
            "origin": {"name": origin_loc["name"], "source": origin_loc["source"]},
            "destination": {"name": dest_loc["name"], "source": dest_loc["source"]},
            "total_options": len(options),
            "showing": len(compact_options),
            "options": compact_options,
        }
        if time_note:
            response_data["time_note"] = time_note

        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})


@tool
def list_transit_stops(query: str):
    """
    Searches for transit stops and villages matching a query string.
    Returns the top 10 matches with coordinates.
    
    Use this when:
    - The user mentions a location you're not sure about
    - find_transit_route returned ORIGIN_NOT_FOUND or DESTINATION_NOT_FOUND
    - You need to disambiguate between similar-sounding locations
    """
    try:
        norm_query = _normalize(query)
        if not norm_query:
            return json.dumps({"status": "ERROR", "message": "Empty query."})

        results = []
        seen_names = set()

        # Search RAPTOR stops
        router = _get_raptor_router()
        for sid, stop in router.stops.items():
            stop_name = stop.get("name", "")
            stop_norm = _normalize(stop_name)
            if not stop_norm:
                continue

            score = 0
            if stop_norm == norm_query:
                score = 100
            elif norm_query in stop_norm:
                score = 80
            elif stop_norm in norm_query:
                score = 70
            else:
                # Word-level match
                query_words = set(norm_query.split())
                stop_words = set(stop_norm.split())
                common = query_words & stop_words
                if common:
                    score = 50 + len(common) * 10

            if score > 0:
                # Boost CURATED stops
                if stop.get("type") == "CURATED":
                    score += 15
                name_key = _normalize(stop_name)
                if name_key not in seen_names:
                    seen_names.add(name_key)
                    results.append({
                        "name": stop_name,
                        "type": stop.get("type", "STOP"),
                        "lat": stop["lat"],
                        "lng": stop["lng"],
                        "stop_id": sid,
                        "score": score,
                    })

        # Search villages
        villages = _get_villages()
        for v in villages:
            v_name = v.get("name", "")
            v_norm = _normalize(v_name)
            if not v_norm:
                continue

            score = 0
            if v_norm == norm_query:
                score = 90
            elif norm_query in v_norm:
                score = 65
            elif v_norm in norm_query:
                score = 55
            else:
                query_words = set(norm_query.split())
                v_words = set(v_norm.split())
                common = query_words & v_words
                if common:
                    score = 40 + len(common) * 10

            if score > 0:
                lat = v.get("lat")
                lng = v.get("lon") or v.get("lng")
                if lat is not None and lng is not None:
                    name_key = v_norm
                    if name_key not in seen_names:
                        seen_names.add(name_key)
                        results.append({
                            "name": v_name,
                            "type": "VILLAGE",
                            "lat": lat,
                            "lng": lng,
                            "block": v.get("block"),
                            "population": v.get("population_2011"),
                            "score": score,
                        })

        # Sort by score descending, take top 10
        results.sort(key=lambda r: r["score"], reverse=True)
        top = results[:10]

        # Strip internal scores
        for r in top:
            r.pop("score", None)

        return json.dumps({
            "status": "SUCCESS",
            "query": query,
            "matches": len(top),
            "results": top,
        }, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})
