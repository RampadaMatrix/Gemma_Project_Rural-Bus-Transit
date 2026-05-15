import json
import os
import re
import math
import hashlib
from datetime import datetime, timezone
from collections import Counter

# File Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HITL_DIR = os.path.dirname(SCRIPT_DIR)

SECURED_DATA_PATH = os.path.join(HITL_DIR, "BD_Phase1_HITL_Secured.json")
VILLAGES_DATA_PATH = os.path.join(HITL_DIR, "Villages_data", "Villages_data_identified.json")
ROAD_DATA_PATH = os.path.join(HITL_DIR, "Villages_data", "purulia_roads.json")
OUTPUT_DIR = SCRIPT_DIR
FINAL_STOPS_OUTPUT = os.path.join(OUTPUT_DIR, "final_raptor_stops.json")
BENCHMARK_SUITE_PATH = os.path.join(OUTPUT_DIR, "raptor_benchmark_suite.json")

# Globals
VILLAGES_INDEX = []
JUNCTIONS_INDEX = []
JUNCTIONS_GRID = {}
master_stops = []
virtual_stops = []


def make_unique_id(base_id, used_ids):
    base = normalize_stop_id(base_id) or "id"
    if base not in used_ids:
        used_ids.add(base)
        return base
    idx = 2
    while True:
        candidate = f"{base}_{idx}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        idx += 1

def normalize_stop_id(name):
    if not name: return ""
    clean = re.sub(r'[^a-zA-Z0-9_\s]', '', name).strip().lower()
    return re.sub(r'\s+', '_', clean)

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def decode_polyline(encoded):
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

def phase_1_extract_curated_stops():
    print("\n--- PHASE 1: CURATED BACKBONE EXTRACTION ---")
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    with open(SECURED_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    unique_stops = {}
    for bus in data.get("buses", []):
        for mov in bus.get("movements", []):
            for node in mov.get("route", {}).get("nodes", []):
                name, lat, lng = node.get('name'), node.get('lat'), node.get('lng')
                if name and (lat is not None) and (lng is not None):
                    stop_id = normalize_stop_id(name)
                    if stop_id not in unique_stops:
                        unique_stops[stop_id] = {
                            "stop_id": stop_id, "name": name.strip(),
                            "lat": lat, "lng": lng, "type": "CURATED"
                        }
                        
    global master_stops
    master_stops = list(unique_stops.values())
    print(f"Success: Extracted {len(master_stops)} Curated Backbone stops into memory.")


def phase_2_village_spatial_indexing():
    print("\n--- PHASE 2: VILLAGE SPATIAL INDEXING ---")
    global VILLAGES_INDEX
    with open(VILLAGES_DATA_PATH, 'r', encoding='utf-8') as f:
        VILLAGES_INDEX = json.load(f)
    print(f"Success: Loaded {len(VILLAGES_INDEX)} villages into spatial index.")

def phase_2b_junction_spatial_indexing():
    print("\n--- PHASE 2B: ROAD JUNCTION SPATIAL INDEXING ---")
    global JUNCTIONS_INDEX, JUNCTIONS_GRID
    if not os.path.exists(ROAD_DATA_PATH):
        print("Warning: Road data not found. Skipping junction indexing.")
        return
        
    with open(ROAD_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    from collections import defaultdict
    point_counts = defaultdict(int)
    
    # Combine all road tiers to find true intersections (e.g. where a village branch meets a highway spine)
    all_segments = data.get('spine', []) + data.get('branch', []) + data.get('subbranch', []) + data.get('lastmile', [])
    for segment in all_segments:
        for point in segment:
            # Round to 5 decimals (~1.1 meter precision) to group near points
            lat = round(point['lat'], 5)
            lng = round(point['lng'], 5)
            point_counts[(lat, lng)] += 1
            
    junctions = [p for p, count in point_counts.items() if count > 1]
    
    for lat, lng in junctions:
        j = {'lat': lat, 'lng': lng}
        JUNCTIONS_INDEX.append(j)
        gx, gy = int(lat * 100), int(lng * 100)
        JUNCTIONS_GRID.setdefault((gx, gy), []).append(j)
        
    print(f"Success: Extracted {len(JUNCTIONS_INDEX)} physical road junctions into spatial index.")

def phase_3_corridor_extraction():
    print("\n--- PHASE 3: CORRIDOR ROUTE EXTRACTION ---")
    ROUTES_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_routes.json")
    with open(SECURED_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    unique_routes = {}
    for bus in data.get("buses", []):
        for mov in bus.get("movements", []):
            corridor_id = mov.get('corridor_id')
            if not corridor_id: continue
            corr_sig = mov.get('corridor_signature') or f"{mov.get('origin', 'Unknown')} - {mov.get('destination', 'Unknown')}"
            if corridor_id not in unique_routes:
                unique_routes[corridor_id] = {"route_id": corridor_id, "route_name": corr_sig}
                
    routes_array = list(unique_routes.values())
    with open(ROUTES_OUTPUT, 'w', encoding='utf-8') as out_f:
        json.dump(routes_array, out_f, indent=4)
    print(f"Success: Extracted {len(routes_array)} unique RAPTOR Routes (Corridors).")

def phase_4_and_5_densification():
    print("\n--- PHASE 4 & 5: INTERVAL DENSIFICATION & MERGING ---")
    
    with open(SECURED_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    corridors_geom = {}
    for bus in data.get("buses", []):
        for mov in bus.get("movements", []):
            cid = mov.get('corridor_id')
            encoded_poly = mov.get('route', {}).get('polyline')
            if cid and encoded_poly and cid not in corridors_geom:
                dense_nodes = decode_polyline(encoded_poly)
                if dense_nodes:
                    corridors_geom[cid] = dense_nodes
                
    # 1. Village Spatial Grid for fast radius sweep
    grid = {}
    for village in VILLAGES_INDEX:
        v_lat, v_lon = village.get('lat'), village.get('lon')
        if v_lat is None or v_lon is None: continue
        gx, gy = int(v_lat * 100), int(v_lon * 100)
        grid.setdefault((gx, gy), []).append(village)
        
    # 1.5 Master Stops Spatial Grid for instant proximity checks
    master_grid = {}
    for ms in master_stops:
        gx, gy = int(ms['lat'] * 100), int(ms['lng'] * 100)
        master_grid.setdefault((gx, gy), []).append(ms)

    raw_virtual_candidates = []

    # 2. Extract Virtual Stops strictly every 3000m
    for cid, dense_nodes in corridors_geom.items():
        dist_since_last_stop = 0.0
        curr_chainage = 0.0
        
        for i in range(len(dense_nodes)):
            n_lat, n_lon = dense_nodes[i].get('lat'), dense_nodes[i].get('lng')
            if n_lat is None or n_lon is None:
                continue
            
            if i > 0:
                prev_lat, prev_lon = dense_nodes[i-1].get('lat'), dense_nodes[i-1].get('lng')
                segment_dist = haversine_distance(prev_lat, prev_lon, n_lat, n_lon)
                dist_since_last_stop += segment_dist
                curr_chainage += segment_dist
                
            # A. Master Stop Proximity Reset!
            # Use spatial grid to check master stops instantly
            is_near_master = False
            gx_m, gy_m = int(n_lat * 100), int(n_lon * 100)
            
            for dx in [-1, 0, 1]:
                if is_near_master: break
                for dy in [-1, 0, 1]:
                    if is_near_master: break
                    for ms in master_grid.get((gx_m + dx, gy_m + dy), []):
                        if haversine_distance(ms['lat'], ms['lng'], n_lat, n_lon) < 400:
                            dist_since_last_stop = 0.0
                            is_near_master = True
                            break
                    
            if is_near_master:
                continue
                
            # B. 3 KM Bullet Fire!
            if dist_since_last_stop >= 3000.0:
                gx, gy = int(n_lat * 100), int(n_lon * 100)
                
                # Spine Awareness: Check for physical junction first (radius 1000m)
                closest_junction = None
                min_j_dist = 999999
                
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        for j in JUNCTIONS_GRID.get((gx + dx, gy + dy), []):
                            j_lat, j_lon = j['lat'], j['lng']
                            d = haversine_distance(n_lat, n_lon, j_lat, j_lon)
                            if d <= 1000 and d < min_j_dist:
                                min_j_dist = d
                                closest_junction = j
                                
                if closest_junction:
                    # Anchor stop directly onto the physical junction geometry
                    anchor_lat, anchor_lon = closest_junction['lat'], closest_junction['lng']
                    is_junction = True
                    # Look for villages around junction rather than interval point
                    search_lat, search_lon = anchor_lat, anchor_lon
                else:
                    anchor_lat, anchor_lon = n_lat, n_lon
                    is_junction = False
                    search_lat, search_lon = n_lat, n_lon
                    
                gx_s, gy_s = int(search_lat * 100), int(search_lon * 100)

                # Search for village clusters in 5km radius for naming
                candidates_in_radius = []
                for dx in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]:
                    for dy in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]:
                        for v in grid.get((gx_s + dx, gy_s + dy), []):
                            v_lat, v_lon = v.get('lat'), v.get('lon')
                            off_m = haversine_distance(v_lat, v_lon, search_lat, search_lon)
                            if off_m <= 5000:
                                candidates_in_radius.append((v, off_m))
                                
                if candidates_in_radius:
                    # Pick top 2 villages by (Tier, Population, -Distance)
                    def cluster_score(item):
                        v, dist = item
                        pop = int(v.get('population_2011', 0) or 0)
                        # Tier 3: < 500m (Highly Relevant - The "More")
                        # Tier 2: < 1500m (Near)
                        # Tier 1: < 5000m (Area)
                        if dist < 500: tier = 3
                        elif dist < 1500: tier = 2
                        else: tier = 1
                        return (tier, pop, -dist)
                    
                    sorted_c = sorted(candidates_in_radius, key=cluster_score, reverse=True)
                    v1, d1 = sorted_c[0]
                    vname1 = v1.get('name')
                    
                    # Naming Logic: Only use "More" if the village is within 1km (Tight proximity)
                    if len(sorted_c) > 1 or is_junction:
                        if len(sorted_c) > 1:
                            v2, d2 = sorted_c[1]
                            vname2 = v2.get('name')
                            # Suffix Logic
                            suffix = "More" if (d1 < 1000 or d2 < 1000) else "Area"
                            stop_name = f"{vname1} / {vname2} {suffix}"
                            stop_id = f"{normalize_stop_id(vname1)}_{normalize_stop_id(vname2)}_{cid}_{int(round(anchor_lat*1e5))}_{int(round(anchor_lon*1e5))}_virt"
                        else:
                            suffix = "More" if d1 < 1000 else "Point"
                            stop_name = f"{vname1} {suffix}"
                            stop_id = f"{normalize_stop_id(vname1)}_{suffix.lower()}_{cid}_{int(round(anchor_lat*1e5))}_{int(round(anchor_lon*1e5))}_virt"
                    else:
                        stop_name = f"Near {vname1}" if d1 < 2000 else f"Near {vname1} (Area)"
                        stop_id = f"{normalize_stop_id(vname1)}_{cid}_{int(round(anchor_lat*1e5))}_{int(round(anchor_lon*1e5))}_virt"
                    
                    raw_virtual_candidates.append({
                        "id": stop_id,
                        "name": stop_name,
                        "lat": anchor_lat, "lng": anchor_lon,
                        "distance_to_village": -1 if is_junction else d1, # Highest priority if junction
                        "corridor": cid,
                        "is_junction": is_junction
                    })
                else:                     
                    # Fallback for Extended Route Coverage - Include corridor hint for better UX
                    km_m = int(curr_chainage / 1000)
                    corr_hint = cid.split('_')[-1] if '_' in cid else cid[:6]
                    if is_junction:
                        raw_virtual_candidates.append({
                            "id": f"checkpoint_junc_{cid}_{km_m}_{int(round(anchor_lat*1e5))}_{int(round(anchor_lon*1e5))}_virt",
                            "name": f"Junction {km_m}km ({corr_hint})",
                            "lat": anchor_lat, "lng": anchor_lon,
                            "distance_to_village": -1,
                            "corridor": cid,
                            "is_junction": True
                        })
                    else:
                        raw_virtual_candidates.append({
                            "id": f"checkpoint_{cid}_{km_m}_{int(round(anchor_lat*1e5))}_{int(round(anchor_lon*1e5))}_virt",
                            "name": f"Route Point {km_m}km ({corr_hint})",
                            "lat": anchor_lat, "lng": anchor_lon,
                            "distance_to_village": 99999,
                            "corridor": cid,
                            "is_junction": False
                        })
                    
                # Reset counter to wait another 3km
                dist_since_last_stop = 0.0

    # 3. GLOBAL DEDUPLICATION (Fixes multiple corridors dropping identical stops)
    global_deduped_stops = []
    used_virtual_ids = set()
    # Sort by closest distance to village instead of population so the tightest geographic snaps anchor the stop
    raw_virtual_candidates.sort(key=lambda x: x['distance_to_village']) 
    
    for cand in raw_virtual_candidates:
        # Corridor-aware dedupe: merge only very near cross-corridor duplicates, keep coverage on distinct corridors.
        collision = False
        for approved in global_deduped_stops:
            d = haversine_distance(cand['lat'], cand['lng'], approved['lat'], approved['lng'])
            if d < 150:
                collision = True
                break
            if cand['corridor'] == approved.get('corridor') and d < 600:
                collision = True
                break
        
        if not collision:
            unique_vid = make_unique_id(cand['id'], used_virtual_ids)
            global_deduped_stops.append({
                "stop_id": unique_vid,
                "name": cand['name'],
                "lat": cand['lat'],
                "lng": cand['lng'],
                "type": "VIRTUAL",
                "corridor": cand['corridor'],
                "is_junction": cand.get('is_junction', False)
            })

    global virtual_stops
    virtual_stops = global_deduped_stops
    print(f"Success: Snapped and globally deduplicated {len(global_deduped_stops)} unified Virtual Stops into memory!")

def phase_5b_unify_graph():
    print("\n--- PHASE 5B: UNIFY MASTER GRAPH ---")
    unified_graph = master_stops + virtual_stops
    used_ids = set()
    for st in unified_graph:
        st["stop_id"] = make_unique_id(st.get("stop_id"), used_ids)
    
    with open(FINAL_STOPS_OUTPUT, 'w', encoding='utf-8') as out_f:
        json.dump(unified_graph, out_f, indent=4)
        
    print(f"Success: Unified graph generated! Total Nodes: {len(unified_graph)} (Curated: {len(master_stops)} | Virtual: {len(virtual_stops)})")

def phase_6_trip_extraction():
    print("\n--- PHASE 6: TRIP EXTRACTION ---")
    TRIPS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_trips.json")
    
    with open(SECURED_DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    with open(FINAL_STOPS_OUTPUT, 'r', encoding='utf-8') as f:
        unified_stops = json.load(f)
        
    # Spatial Grid for ALL Unified Stops
    stops_grid = {}
    for st in unified_stops:
        gx, gy = int(st['lat'] * 100), int(st['lng'] * 100)
        stops_grid.setdefault((gx, gy), []).append(st)

    extracted_trips = []
    seen_trip_ids = set()

    for bus_idx, bus in enumerate(data.get("buses", [])):
        bus_name = bus.get('bus_name')
        bus_reg_key = (
            bus.get("bus_reg_key")
            or bus.get("bus_reg_no")
            or bus.get("registration_no")
            or bus.get("registration_number")
            or bus.get("bus_registration")
            or bus.get("vehicle_id")
            or bus.get("id")
        )
        bus_identity = normalize_stop_id(str(bus_reg_key)) if bus_reg_key else normalize_stop_id(bus_name)
        for idx, mov in enumerate(bus.get("movements", [])):
            cid = mov.get('corridor_id')
            if not cid: continue
            
            encoded_poly = mov.get('route', {}).get('polyline')
            if not encoded_poly: continue
            dense_nodes = decode_polyline(encoded_poly)
            
            raw_trip_id = (
                f"trip_{bus_identity}_{bus_idx}_{idx}_"
                f"{normalize_stop_id(str(cid))}_{normalize_stop_id(str(mov.get('direction', 'UNK')))}"
            )
            trip_id = make_unique_id(raw_trip_id, seen_trip_ids)
            
            schedule_map = {}
            for s in mov.get('stops', []):
                norm_id = normalize_stop_id(s.get('name'))
                schedule_map[norm_id] = {
                    'arrival_time': s.get('arrival_time'),
                    'departure_time': s.get('departure_time')
                }
            
            stop_sequence = []
            added_stops = set()
            dist_along_poly = 0.0
            
            for i in range(len(dense_nodes)):
                n_lat, n_lon = dense_nodes[i].get('lat'), dense_nodes[i].get('lng')
                
                if i > 0:
                    prev_lat, prev_lon = dense_nodes[i-1].get('lat'), dense_nodes[i-1].get('lng')
                    dist_along_poly += haversine_distance(prev_lat, prev_lon, n_lat, n_lon)
                    
                gx, gy = int(n_lat * 100), int(n_lon * 100)
                
                closest_stop = None
                min_d = 999999
                
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        for st in stops_grid.get((gx + dx, gy + dy), []):
                            d = haversine_distance(n_lat, n_lon, st['lat'], st['lng'])
                            if d <= 400 and d < min_d:
                                min_d = d
                                closest_stop = st
                                
                if closest_stop:
                    if 'stop_id' in closest_stop:
                        s_id = closest_stop['stop_id']
                        if s_id not in added_stops:
                            item = {
                                "stop_id": s_id,
                                "distance_m": round(dist_along_poly, 1)
                            }
                            schedule_key = s_id
                            if schedule_key not in schedule_map:
                                schedule_key = normalize_stop_id(closest_stop.get("name", ""))
                            if schedule_key in schedule_map:
                                if schedule_map[schedule_key]['arrival_time']:
                                    item['arrival_time'] = schedule_map[schedule_key]['arrival_time']
                                if schedule_map[schedule_key]['departure_time']:
                                    item['departure_time'] = schedule_map[schedule_key]['departure_time']
                            
                            stop_sequence.append(item)
                            added_stops.add(s_id)
                        
            if len(stop_sequence) > 0:
                extracted_trips.append({
                    "trip_id": trip_id,
                    "route_id": cid,
                    "bus_name": bus_name,
                    "bus_reg_key": bus_reg_key,
                    "direction": mov.get('direction', ''),
                    "origin": mov.get('origin', ''),
                    "destination": mov.get('destination', ''),
                    "stop_sequence": stop_sequence
                })
                
    with open(TRIPS_OUTPUT, 'w', encoding='utf-8') as out_f:
        json.dump(extracted_trips, out_f, indent=4)
        
    print(f"Success: Extracted {len(extracted_trips)} Trips with Chronological Stop Sequences.")

def parse_time(time_str):
    if not time_str: return None
    import re
    m = re.match(r"(\d+):(\d+)\s*(AM|PM)", str(time_str).strip(), re.IGNORECASE)
    if m:
        h, m_mins, p = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if p == 'PM' and h < 12: h += 12
        if p == 'AM' and h == 12: h = 0
        return h * 60 + m_mins
    return None

def phase_7_timetable_materialization():
    print("\n--- PHASE 7: TIMETABLE MATERIALIZATION ---")
    TRIPS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_trips.json")
    STOP_TIMES_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_stop_times.json")
    
    with open(TRIPS_OUTPUT, 'r', encoding='utf-8') as f:
        trips = json.load(f)
        
    raptor_stop_times = []
    
    for trip in trips:
        seq = trip.get("stop_sequence", [])
        if not seq: continue
        
        # Parse all known times
        for s in seq:
            s['arr_m'] = parse_time(s.get('arrival_time'))
            s['dep_m'] = parse_time(s.get('departure_time'))
            if s['arr_m'] is None and s['dep_m'] is not None: s['arr_m'] = s['dep_m']
            if s['dep_m'] is None and s['arr_m'] is not None: s['dep_m'] = s['arr_m']
            
        # Find all anchors
        anchors = [i for i, s in enumerate(seq) if s.get('arr_m') is not None]
        
        if not anchors:
            # Fallback if literally no schedule exists: arbitrarily start at 06:00 AM (360) and assume 30km/h (500m/min)
            for i, s in enumerate(seq):
                m = 360 + (s['distance_m'] / 500.0)
                s['arr_m'] = m
                s['dep_m'] = m + (1 if 0 < i < len(seq)-1 else 0)
        else:
            # Interpolate between anchors
            for k in range(len(anchors) - 1):
                idx_A = anchors[k]
                idx_B = anchors[k+1]
                tA = seq[idx_A]['dep_m']
                tB = seq[idx_B]['arr_m']
                dA = seq[idx_A]['distance_m']
                dB = seq[idx_B]['distance_m']
                
                # if timestamps are physically illogical (e.g. going back in time), enforce forward time.
                if tB <= tA:
                    fallback_travel = max(1.0, abs(dB - dA) / 500.0)  # Assume ~30km/h but always forward
                    tB = tA + fallback_travel
                    seq[idx_B]['arr_m'] = tB
                    seq[idx_B]['dep_m'] = tB
                    
                for i in range(idx_A + 1, idx_B):
                    dI = seq[i]['distance_m']
                    frac = (dI - dA) / (dB - dA) if dB > dA else 0
                    tI = tA + frac * (tB - tA)
                    seq[i]['arr_m'] = tI
                    seq[i]['dep_m'] = tI + 1  # 1 min dwell
                    
            # Extrapolate before first anchor
            first = anchors[0]
            for i in range(0, first):
                dist_diff = seq[first]['distance_m'] - seq[i]['distance_m']
                tI = seq[first]['arr_m'] - (dist_diff / 500.0)
                seq[i]['arr_m'] = tI
                seq[i]['dep_m'] = tI + 1
                
            # Extrapolate after last anchor
            last = anchors[-1]
            for i in range(last + 1, len(seq)):
                dist_diff = seq[i]['distance_m'] - seq[last]['distance_m']
                tI = seq[last]['dep_m'] + (dist_diff / 500.0)
                seq[i]['arr_m'] = tI
                seq[i]['dep_m'] = tI + 1
                
        # Generate Stop Times Output
        last_dep = -1
        for i, s in enumerate(seq):
            arr_m = round(s['arr_m'])
            dep_m = round(s['dep_m'])
            if dep_m < arr_m:
                dep_m = arr_m
            while arr_m < last_dep:
                if (last_dep - arr_m) > 720: # Over 12h jump backwards: likely midnight crossover
                    arr_m += 1440
                    dep_m += 1440
                else:
                    # Minor noise or small backwards jump: clamp to last_dep
                    arr_m = last_dep
                    if dep_m < arr_m:
                        dep_m = arr_m
            last_dep = dep_m
            raptor_stop_times.append({
                "trip_id": trip['trip_id'],
                "stop_id": s['stop_id'],
                "stop_sequence": i + 1, # 1-based index
                "arrival_time_mins": arr_m,
                "departure_time_mins": dep_m,
                "distance_m": s['distance_m']
            })
            
    with open(STOP_TIMES_OUTPUT, 'w', encoding='utf-8') as out_f:
        json.dump(raptor_stop_times, out_f, indent=4)
        
    print(f"Success: Materialized {len(raptor_stop_times)} Timetable Logs via Geometric Interpolation.")

def phase_8_hub_generation():
    print("\n--- PHASE 8: HUB GENERATION & TRANSFERS ---")
    TRANSFERS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_transfers.json")
    TRIPS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_trips.json")

    with open(FINAL_STOPS_OUTPUT, 'r', encoding='utf-8') as f:
        stops = json.load(f)

    # Route-service degree per stop (used to avoid unrealistic long-distance transfer edges).
    stop_routes = {}
    if os.path.exists(TRIPS_OUTPUT):
        with open(TRIPS_OUTPUT, 'r', encoding='utf-8') as f:
            trips = json.load(f)
        for trip in trips:
            rid = trip.get('route_id')
            for row in trip.get('stop_sequence', []) or []:
                sid = row.get('stop_id')
                if not sid:
                    continue
                stop_routes.setdefault(sid, set()).add(rid)

    # 1. Spatial Grid for O(N) Proximity Search
    grid = {}
    for st in stops:
        gx, gy = int(st['lat'] * 100), int(st['lng'] * 100)
        grid.setdefault((gx, gy), []).append(st)

    transfers = []
    MAX_WALK_M = 1000 # 1km rural walking radius
    STRICT_WALK_M = 400 # Beyond this, require a hub-like endpoint
    WALK_SPEED_MPM = 83.3 # 5 km/h in meters per minute
    BUFFER_MINS = 2 # Transfer buffer

    seen_pairs = set()
    
    for s1 in stops:
        sid1 = s1['stop_id']
        lat1, lon1 = s1['lat'], s1['lng']
        gx, gy = int(lat1 * 100), int(lon1 * 100)
        
        # Self Transfer
        transfers.append({
            "from_stop_id": sid1,
            "to_stop_id": sid1,
            "transfer_time_mins": 0
        })
        
        # Find neighbors within 9 grid cells
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                for s2 in grid.get((gx + dx, gy + dy), []):
                    sid2 = s2['stop_id']
                    if sid1 == sid2: continue
                    
                    # Deduplicate bidirectional search
                    pair = tuple(sorted([sid1, sid2]))
                    if pair in seen_pairs: continue
                    
                    dist = haversine_distance(lat1, lon1, s2['lat'], s2['lng'])
                    if dist <= MAX_WALK_M:
                        routes_1 = stop_routes.get(sid1, set())
                        routes_2 = stop_routes.get(sid2, set())
                        degree_1 = len(routes_1)
                        degree_2 = len(routes_2)
                        # Keep short links always; for longer links require at least one endpoint to be a hub.
                        if dist > STRICT_WALK_M and max(degree_1, degree_2) < 2:
                            continue

                        time_mins = round((dist / WALK_SPEED_MPM) + BUFFER_MINS)

                        # Add both directions
                        transfers.append({
                            "from_stop_id": sid1,
                            "to_stop_id": sid2,
                            "transfer_time_mins": time_mins
                        })
                        transfers.append({
                            "from_stop_id": sid2,
                            "to_stop_id": sid1,
                            "transfer_time_mins": time_mins
                        })
                        seen_pairs.add(pair)
                        
    with open(TRANSFERS_OUTPUT, 'w', encoding='utf-8') as out_f:
        json.dump(transfers, out_f, indent=4)
        
    print(f"Success: Generated {len(transfers)} Walking Transfers across {len(stops)} stops.")


def phase_9_build_qa_gate():
    print("\n--- PHASE 9: BUILD QA GATE ---")
    TRIPS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_trips.json")
    STOP_TIMES_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_stop_times.json")
    TRANSFERS_OUTPUT = os.path.join(OUTPUT_DIR, "raptor_transfers.json")

    with open(FINAL_STOPS_OUTPUT, 'r', encoding='utf-8') as f:
        stops = json.load(f)
    with open(TRIPS_OUTPUT, 'r', encoding='utf-8') as f:
        trips = json.load(f)
    with open(STOP_TIMES_OUTPUT, 'r', encoding='utf-8') as f:
        stop_times = json.load(f)
    with open(TRANSFERS_OUTPUT, 'r', encoding='utf-8') as f:
        transfers = json.load(f)

    errors = []
    stop_ids = [s.get("stop_id") for s in stops]
    trip_ids = [t.get("trip_id") for t in trips]
    stop_id_set = set(stop_ids)
    trip_id_set = set(trip_ids)

    dup_stop_ids = [sid for sid, c in Counter(stop_ids).items() if sid and c > 1]
    dup_trip_ids = [tid for tid, c in Counter(trip_ids).items() if tid and c > 1]
    if dup_stop_ids:
        errors.append(f"Duplicate stop_ids detected: {len(dup_stop_ids)}")
    if dup_trip_ids:
        errors.append(f"Duplicate trip_ids detected: {len(dup_trip_ids)}")

    trip_stop_orphans = 0
    trip_dist_nonmono = 0
    for t in trips:
        last_d = -1.0
        for row in t.get("stop_sequence", []) or []:
            sid = row.get("stop_id")
            if sid not in stop_id_set:
                trip_stop_orphans += 1
            d = row.get("distance_m")
            if d is not None and d < last_d:
                trip_dist_nonmono += 1
            if d is not None:
                last_d = d
    if trip_stop_orphans:
        errors.append(f"Trip stop references missing in stops: {trip_stop_orphans}")
    if trip_dist_nonmono:
        errors.append(f"Trips with non-monotonic distance segments: {trip_dist_nonmono}")

    stop_times_by_trip = {}
    for row in stop_times:
        tid = row.get("trip_id")
        stop_times_by_trip.setdefault(tid, []).append(row)
    stop_time_orphans = 0
    stop_time_nonmono = 0
    for tid, rows in stop_times_by_trip.items():
        if tid not in trip_id_set:
            stop_time_orphans += 1
        rows.sort(key=lambda x: x.get("stop_sequence", 0))
        last_dep = -1
        last_d = -1.0
        for r in rows:
            sid = r.get("stop_id")
            if sid not in stop_id_set:
                stop_time_orphans += 1
            arr = r.get("arrival_time_mins")
            dep = r.get("departure_time_mins")
            d = r.get("distance_m")
            if arr is None or dep is None or dep < arr or arr < last_dep:
                stop_time_nonmono += 1
                break
            if d is not None and d < last_d:
                stop_time_nonmono += 1
                break
            last_dep = dep
            if d is not None:
                last_d = d
    if stop_time_orphans:
        errors.append(f"Orphan stop_times references: {stop_time_orphans}")
    if stop_time_nonmono:
        errors.append(f"Trips with non-monotonic timetable rows: {stop_time_nonmono}")

    transfer_orphans = 0
    bad_transfer_rows = 0
    for tr in transfers:
        fs = tr.get("from_stop_id")
        ts = tr.get("to_stop_id")
        tm = tr.get("transfer_time_mins")
        if fs not in stop_id_set or ts not in stop_id_set:
            transfer_orphans += 1
        if tm is None or tm < 0:
            bad_transfer_rows += 1
        if fs == ts and tm != 0:
            bad_transfer_rows += 1
    if transfer_orphans:
        errors.append(f"Transfer references missing in stops: {transfer_orphans}")
    if bad_transfer_rows:
        errors.append(f"Invalid transfer rows (negative or bad self-loop): {bad_transfer_rows}")

    if errors:
        print("Build QA FAILED:")
        for e in errors:
            print(f"  - {e}")
        raise RuntimeError("Build halted by QA gate.")

    print("Success: Build QA PASS (IDs, references, monotonicity, and transfer integrity).")


def phase_10_benchmark_regression():
    print("\n--- PHASE 10: BENCHMARK REGRESSION (OPTIONAL) ---")
    if not os.path.exists(BENCHMARK_SUITE_PATH):
        print(f"Warning: benchmark suite missing at {BENCHMARK_SUITE_PATH}. Skipping benchmark regression.")
        return

    with open(BENCHMARK_SUITE_PATH, "r", encoding="utf-8") as f:
        suite = json.load(f)
    cases = suite.get("cases") or []
    expected = (suite.get("snapshot") or {}).get("results") or {}
    if not cases:
        print("Warning: benchmark suite has no cases. Skipping benchmark regression.")
        return
    if not expected:
        print("Warning: benchmark suite has no snapshot. Run benchmark init first.")
        return

    from raptor_solver import RaptorRouter

    def normalize_result(result):
        status = result.get("status", "UNKNOWN")
        norm = {"status": status}
        if status != "SUCCESS":
            return norm
        options = result.get("options", []) or []
        norm["option_count"] = len(options)
        norm["has_direct_road_access"] = any(o.get("type") == "DIRECT ROAD ACCESS" for o in options)
        if options:
            best = min(options, key=lambda x: x.get("arrival_mins", 10**9))
            itinerary = best.get("itinerary", []) or []
            norm.update(
                {
                    "best_arrival_mins": best.get("arrival_mins"),
                    "best_duration_mins": best.get("duration_mins"),
                    "best_transfers": best.get("transfers"),
                    "best_type": best.get("type"),
                    "best_itinerary_types": [leg.get("type") for leg in itinerary[:5]],
                }
            )
        return norm

    def compare_snapshot(observed, expected_payload, arrival_tol=20, duration_tol=20, option_tol=1):
        failures = []
        expected_ids = set(expected_payload.keys())
        observed_ids = set(observed.keys())
        missing = sorted(expected_ids - observed_ids)
        extra = sorted(observed_ids - expected_ids)
        if missing:
            failures.append(f"Missing observed case ids: {missing}")
        if extra:
            failures.append(f"Unexpected observed case ids: {extra}")

        for case_id in sorted(expected_ids & observed_ids):
            exp = expected_payload[case_id]
            obs = observed[case_id]
            if exp.get("status") != obs.get("status"):
                failures.append(
                    f"{case_id}: status drift expected={exp.get('status')} observed={obs.get('status')}"
                )
                continue
            if obs.get("status") != "SUCCESS":
                continue
            if abs(obs["best_arrival_mins"] - exp["best_arrival_mins"]) > arrival_tol:
                failures.append(
                    f"{case_id}: arrival drift expected={exp['best_arrival_mins']} observed={obs['best_arrival_mins']} tol={arrival_tol}"
                )
            if abs(obs["best_duration_mins"] - exp["best_duration_mins"]) > duration_tol:
                failures.append(
                    f"{case_id}: duration drift expected={exp['best_duration_mins']} observed={obs['best_duration_mins']} tol={duration_tol}"
                )
            if abs(obs["option_count"] - exp["option_count"]) > option_tol:
                failures.append(
                    f"{case_id}: option_count drift expected={exp['option_count']} observed={obs['option_count']} tol={option_tol}"
                )
        return failures

    print("Running benchmark regression harness...")
    router = RaptorRouter(OUTPUT_DIR)
    observed = {}
    for case in cases:
        result = router.solve(
            case["origin_lat"],
            case["origin_lng"],
            case["dest_lat"],
            case["dest_lng"],
            case["departure_time_mins"],
            case.get("max_rounds", 2),
        )
        observed[case["id"]] = normalize_result(result)

    failures = compare_snapshot(observed, expected)
    if failures:
        print("Benchmark regression FAILED:")
        for f in failures[:100]:
            print(f"  - {f}")
        raise RuntimeError("Build halted by benchmark regression drift.")
    print("Success: Benchmark regression PASS.")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def phase_11_merge_bundle():
    print("\n--- PHASE 11: MERGE RAPTOR BUNDLE ---")
    files = {
        "stops": os.path.join(OUTPUT_DIR, "final_raptor_stops.json"),
        "routes": os.path.join(OUTPUT_DIR, "raptor_routes.json"),
        "trips": os.path.join(OUTPUT_DIR, "raptor_trips.json"),
        "stop_times": os.path.join(OUTPUT_DIR, "raptor_stop_times.json"),
        "transfers": os.path.join(OUTPUT_DIR, "raptor_transfers.json"),
    }
    for key, p in files.items():
        if not os.path.exists(p):
            raise RuntimeError(f"Bundle merge failed: missing required artifact `{key}` at {p}")

    merged = {}
    manifest_entries = {}
    for key, p in files.items():
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        merged[key] = payload
        manifest_entries[key] = {
            "path": p,
            "sha256": _sha256_file(p),
            "rows": len(payload) if isinstance(payload, list) else None,
        }

    bundle = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "build_notes": "Derived RAPTOR runtime bundle generated from phase outputs.",
        "stats": {
            "stops": len(merged["stops"]),
            "routes": len(merged["routes"]),
            "trips": len(merged["trips"]),
            "stop_times": len(merged["stop_times"]),
            "transfers": len(merged["transfers"]),
        },
        "manifest": manifest_entries,
        "data": merged,
    }

    bundle_path = os.path.join(OUTPUT_DIR, "raptor_bundle.json")
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    print(f"Success: Wrote merged bundle: {bundle_path}")


def phase_12_cleanup_split_artifacts():
    print("\n--- PHASE 12: CLEANUP SPLIT ARTIFACTS ---")
    keep_split = os.getenv("KEEP_SPLIT_ARTIFACTS", "0") == "1"
    if keep_split:
        print("Info: KEEP_SPLIT_ARTIFACTS=1, split artifacts retained.")
        return
    split_files = [
        os.path.join(OUTPUT_DIR, "final_raptor_stops.json"),
        os.path.join(OUTPUT_DIR, "raptor_routes.json"),
        os.path.join(OUTPUT_DIR, "raptor_trips.json"),
        os.path.join(OUTPUT_DIR, "raptor_stop_times.json"),
        os.path.join(OUTPUT_DIR, "raptor_transfers.json"),
    ]
    removed = 0
    for p in split_files:
        if os.path.exists(p):
            os.remove(p)
            removed += 1
    print(f"Success: Removed {removed} split artifacts. Runtime now uses raptor_bundle.json.")

if __name__ == "__main__":
    phase_1_extract_curated_stops()
    phase_2_village_spatial_indexing()
    phase_2b_junction_spatial_indexing()
    phase_3_corridor_extraction()
    phase_4_and_5_densification()
    phase_5b_unify_graph()
    phase_6_trip_extraction()
    phase_7_timetable_materialization()
    phase_8_hub_generation()
    phase_9_build_qa_gate()
    if os.getenv("RUN_BENCHMARKS", "0") == "1":
        phase_10_benchmark_regression()
    if os.getenv("MERGE_RAPTOR_BUNDLE", "1") == "1":
        phase_11_merge_bundle()
    if os.getenv("MERGE_RAPTOR_BUNDLE", "1") == "1":
        phase_12_cleanup_split_artifacts()





