import json
import os
import math


def _safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except OSError:
        # Windows can give hidden Flask child processes an invalid stdout handle.
        # Logging must not be able to fail a route request.
        pass


class RaptorRouter:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.stops = {}           # stop_id -> stop_obj
        self.routes = {}          # route_id -> { "stops": [sid1, ...], "trips": [tid1, ...] }
        self.stop_times = {}      # trip_id -> list of stop_time_objs
        self.transfers = {}       # stop_id -> list of (to_stop_id, time)
        self.stop_to_routes = {}  # stop_id -> list of (route_id, stop_index)
        self.trip_to_bus = {}     # trip_id -> bus_name
        self.villages = []        # list of village objects
        
        self.load_data()
        self.build_indices()

    def load_data(self):
        _safe_print("Loading RAPTOR data...")
        bundle_path = os.path.join(self.data_dir, "raptor_bundle.json")
        if os.path.exists(bundle_path):
            with open(bundle_path, 'r', encoding='utf-8') as f:
                bundle = json.load(f)
            data = bundle.get("data", {})
            stops_data = data.get("stops", [])
            stop_times_data = data.get("stop_times", [])
            trips_data = data.get("trips", [])
            transfers_data = data.get("transfers", [])
        else:
            # Backward-compatible fallback to split artifacts.
            with open(os.path.join(self.data_dir, "final_raptor_stops.json"), 'r', encoding='utf-8') as f:
                stops_data = json.load(f)
            with open(os.path.join(self.data_dir, "raptor_stop_times.json"), 'r', encoding='utf-8') as f:
                stop_times_data = json.load(f)
            with open(os.path.join(self.data_dir, "raptor_trips.json"), 'r', encoding='utf-8') as f:
                trips_data = json.load(f)
            with open(os.path.join(self.data_dir, "raptor_transfers.json"), 'r', encoding='utf-8') as f:
                transfers_data = json.load(f)

        # 1. Load Stops
        for s in stops_data:
            self.stops[s['stop_id']] = s

        # 2. Load Stop Times & Enforce Monotonicity
        for st in stop_times_data:
            self.stop_times.setdefault(st['trip_id'], []).append(st)

        for tid in list(self.stop_times.keys()):
            self.stop_times[tid].sort(key=lambda x: x['stop_sequence'])

            last_arr = -1
            days_offset = 0
            for st in self.stop_times[tid]:
                st['arr_mins'] = st['arrival_time_mins'] + days_offset
                st['dep_mins'] = st['departure_time_mins'] + days_offset

                if st['arr_mins'] < last_arr:
                    if (last_arr - st['arr_mins']) > 720: # Over 12h jump backwards: likely midnight crossover
                        st['arr_mins'] += 1440
                        st['dep_mins'] += 1440
                        days_offset += 1440
                    else:
                        # Minor noise: clamp
                        st['arr_mins'] = last_arr
                        if st['dep_mins'] < st['arr_mins']:
                            st['dep_mins'] = st['arr_mins']

                last_arr = st['dep_mins']

        # 3. Load Trips & Group into RAPTOR Routes (Safe Grouping)
            
        # Temporary mapping to handle route grouping by exact stop sequence
        seq_to_route_id = {}
        next_route_int = 0

        for t in trips_data:
            tid = t['trip_id']
            self.trip_to_bus[tid] = t.get('bus_name', 'Unknown')
            if tid not in self.stop_times:
                continue

            stop_seq = tuple(st['stop_id'] for st in self.stop_times[tid])

            if stop_seq not in seq_to_route_id:
                rid = f"r_{next_route_int}"
                next_route_int += 1
                seq_to_route_id[stop_seq] = rid
                self.routes[rid] = {"stops": list(stop_seq), "trips": []}

            rid = seq_to_route_id[stop_seq]
            self.routes[rid]["trips"].append(tid)

        # 4. Load Transfers
        for tr in transfers_data:
            self.transfers.setdefault(tr['from_stop_id'], []).append((tr['to_stop_id'], tr['transfer_time_mins']))

    def build_indices(self):
        _safe_print(f"Building RAPTOR indices for {len(self.routes)} unique routes...")
        for rid, r_data in self.routes.items():
            for idx, sid in enumerate(r_data['stops']):
                self.stop_to_routes.setdefault(sid, []).append((rid, idx))
            # Sort trips by departure time at the first stop (standard GTFS/RAPTOR optimization)
            r_data['trips'].sort(key=lambda tid: self.stop_times[tid][0]['dep_mins'])

    def haversine(self, lat1, lon1, lat2, lon2):
        R = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def project_point(self, lat, lon, max_radius_m=5000):
        """
        Route-Aware Projection:
        Finds the closest stop for every unique bus line within 5km.
        This prevents 'blind spots' where many stops of one route hide other nearby routes.
        """
        route_to_best_stop = {} # route_id -> (stop_id, dist)
        
        # 1. Collect all stops within 5km
        nearby_stops = []
        for sid, s in self.stops.items():
            d = self.haversine(lat, lon, s['lat'], s['lng'])
            if d <= max_radius_m:
                nearby_stops.append((sid, d))
                
        # 2. Map them to unique routes
        for sid, d in nearby_stops:
            if sid in self.stop_to_routes:
                for rid, _ in self.stop_to_routes[sid]:
                    if rid not in route_to_best_stop or d < route_to_best_stop[rid][1]:
                        route_to_best_stop[rid] = (sid, d)
        
        # 3. Extract unique candidates
        candidates_map = {} # stop_id -> dist
        for rid, (sid, d) in route_to_best_stop.items():
            if sid not in candidates_map or d < candidates_map[sid]:
                candidates_map[sid] = d
        
        candidates = list(candidates_map.items())
        
        # 4. Fallback if no stops in 5km (isolation mode)
        if not candidates:
            all_dists = [(sid, self.haversine(lat, lon, s['lat'], s['lng'])) for sid, s in self.stops.items()]
            all_dists.sort(key=lambda x: x[1])
            candidates = all_dists[:2]
        else:
            candidates.sort(key=lambda x: x[1])
            
        return candidates

    def get_walk_time(self, dist_m):
        """Dynamic access speed: 5km/h (<=1km) or 30km/h (>1km)"""
        # 5 km/h  = 83.33 m/min
        # 30 km/h = 500.0 m/min
        if dist_m <= 1000:
            return round(dist_m / 83.33)
        else:
            return round(dist_m / 500.0)

    def _norm_name(self, value):
        return ' '.join(
            ''.join(ch.lower() if ch.isalnum() else ' ' for ch in str(value or '')).split()
        )

    def _trip_segment_path(self, trip_id, from_stop_name, to_stop_name):
        """Return the stop-sequence geometry for a bus leg instead of a straight chord."""
        rows = self.stop_times.get(trip_id) or []
        if not rows:
            return []

        from_key = self._norm_name(from_stop_name)
        to_key = self._norm_name(to_stop_name)
        start_idx = None
        end_idx = None

        for idx, row in enumerate(rows):
            stop = self.stops.get(row.get('stop_id')) or {}
            stop_key = self._norm_name(stop.get('name'))
            if start_idx is None and stop_key == from_key:
                start_idx = idx
                continue
            if start_idx is not None and stop_key == to_key:
                end_idx = idx
                break

        if start_idx is None or end_idx is None or end_idx < start_idx:
            return []

        path = []
        for row in rows[start_idx:end_idx + 1]:
            stop = self.stops.get(row.get('stop_id')) or {}
            lat = stop.get('lat')
            lng = stop.get('lng')
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                path.append({
                    "lat": lat,
                    "lng": lng,
                    "name": stop.get('name'),
                    "stop_id": row.get('stop_id')
                })
        return path

    def enrich_options_geometry(self, options):
        for opt in options or []:
            for leg in opt.get("itinerary") or []:
                if leg.get("type") != "BUS" or leg.get("path"):
                    continue
                path = self._trip_segment_path(
                    leg.get("trip_id"),
                    leg.get("from_stop"),
                    leg.get("to_stop")
                )
                if len(path) >= 2:
                    leg["path"] = path
                    leg["path_points"] = len(path)
        return options

    def solve(self, origin_lat, origin_lng, dest_lat, dest_lng, departure_time_mins, max_rounds=2):
        # 1. Direct Road Access & 5km Guardrail
        direct_dist = self.haversine(origin_lat, origin_lng, dest_lat, dest_lng)
        direct_time = self.get_walk_time(direct_dist)
        
        street_walk_opt = {
            "type": "DIRECT ROAD ACCESS",
            "transfers": 0,
            "arrival_mins": departure_time_mins + direct_time,
            "arrival_time": self.format_min(departure_time_mins + direct_time),
            "duration_mins": direct_time,
            "itinerary": [{
                "type": "WALK",
                "from": "Origin",
                "to": "Destination",
                "dist_km": round(direct_dist / 1000, 2),
                "duration_mins": direct_time,
                "arr": self.format_min(departure_time_mins + direct_time)
            }],
            "summary": f"Direct Road Access, {direct_time} mins"
        }
        
        if direct_dist <= 5000:
            return {
                "status": "SUCCESS",
                "departure": self.format_min(departure_time_mins),
                "options": [street_walk_opt],
                "note": "Transit not applicable for distances < 5km."
            }

        TRANSFER_BUFFER_MINS = 5
        
        labels = [{} for _ in range(max_rounds + 1)]
        parents = [{} for _ in range(max_rounds + 1)]
        marked_stops = set()
        
        origins = self.project_point(origin_lat, origin_lng)
        for sid, dist in origins:
            walk_time = self.get_walk_time(dist)
            arr = departure_time_mins + walk_time
            labels[0][sid] = arr
            parents[0][sid] = (None, "WALK", departure_time_mins, arr)
            marked_stops.add(sid)
            
        earliest = {sid: labels[0][sid] for sid in labels[0]}

        for k in range(1, max_rounds + 1):
            routes_to_scan = {}
            for sid in marked_stops:
                if sid in self.stop_to_routes:
                    for rid, s_idx in self.stop_to_routes[sid]:
                        if rid not in routes_to_scan or s_idx < routes_to_scan[rid]:
                            routes_to_scan[rid] = s_idx
            
            marked_stops = set()
            for rid, start_idx in routes_to_scan.items():
                active_trip = None
                board_stop = None
                board_idx = None
                route_stops = self.routes[rid]['stops']
                
                for i in range(start_idx, len(route_stops)):
                    sid = route_stops[i]
                    
                    if active_trip:
                        st = self.stop_times[active_trip][i]
                        arr = st['arr_mins']
                        if arr < earliest.get(sid, float('inf')):
                            labels[k][sid] = arr
                            earliest[sid] = arr
                            dep_mins = self.stop_times[active_trip][board_idx]['dep_mins'] if board_idx is not None else self.stop_times[active_trip][i]['dep_mins']
                            parents[k][sid] = (board_stop, active_trip, dep_mins, arr)
                            marked_stops.add(sid)
                    
                    prev_arr = labels[k-1].get(sid)
                    if prev_arr is not None:
                        best_tid = None
                        best_dep = None
                        for tid in self.routes[rid]['trips']:
                            dep = self.stop_times[tid][i]['dep_mins']
                            if dep >= (prev_arr + TRANSFER_BUFFER_MINS):
                                if best_dep is None or dep < best_dep:
                                    best_dep = dep
                                    best_tid = tid
                        if best_tid is not None:
                            if active_trip is None or best_dep < self.stop_times[active_trip][i]['dep_mins']:
                                active_trip = best_tid
                                board_stop = sid
                                board_idx = i

            new_labels = {}
            for sid in marked_stops:
                if sid in self.transfers:
                    for to_sid, w_time in self.transfers[sid]:
                        arr = labels[k][sid] + w_time
                        if arr < earliest.get(to_sid, float('inf')):
                            new_labels[to_sid] = arr
                            earliest[to_sid] = arr
                            parents[k][to_sid] = (sid, "WALK", labels[k][sid], arr)
            for sid, val in new_labels.items():
                labels[k][sid] = val
                marked_stops.add(sid)
            if not marked_stops: break

        dests = self.project_point(dest_lat, dest_lng)
        options = []
        seen_arrival_times = set()

        for r in range(max_rounds + 1):
            best_r_time = float('inf')
            best_r_stop = None
            
            for sid, dist in dests:
                if sid in labels[r]:
                    t_arr = labels[r][sid] + self.get_walk_time(dist)
                    if t_arr < best_r_time:
                        best_r_time = t_arr
                        best_r_stop = sid
            
            if best_r_stop:
                if best_r_time not in seen_arrival_times:
                    itin = self.backtrack(best_r_stop, r, labels, parents, origins, dests, departure_time_mins)
                    if itin:
                        label = "FASTEST"
                        if r == 1: label = "DIRECT"
                        elif r == 0: label = "WALK ONLY"
                        
                        options.append({
                            "type": label,
                            "transfers": r - 1 if r > 0 else 0,
                            "arrival_mins": best_r_time,
                            "arrival_time": self.format_min(best_r_time),
                            "duration_mins": round(best_r_time - departure_time_mins),
                            "itinerary": itin,
                            "summary": f"{r-1 if r > 0 else 0} Transfers, {round(best_r_time - departure_time_mins)} mins"
                        })
                        seen_arrival_times.add(best_r_time)

        # Add direct road access as an option ONLY if it's within a reasonable direct distance (e.g. 15km)
        # Otherwise, for long distances like 200km, a 'DIRECT ROAD ACCESS' is nonsense.
        if direct_dist <= 15000:
            options.append(street_walk_opt)
            
        options.sort(key=lambda x: x['arrival_mins'])
        if not options: return {"status": "NO_ROUTE"}

        return {
            "status": "SUCCESS",
            "departure": self.format_min(departure_time_mins),
            "options": options
        }

    def solve_all_options(self, origin_lat, origin_lng, dest_lat, dest_lng,
                          time_window_start=240, time_window_end=1320, max_transfers=1):
        """
        McRAPTOR: Enumerates ALL reasonable journey options across the full day.
        Unlike standard RAPTOR (earliest-arrival only), this discovers every valid
        bus pair combination — two buses passing origin at the same stop but
        reaching destination via different paths/times are both reported.
        """
        direct_dist = self.haversine(origin_lat, origin_lng, dest_lat, dest_lng)
        direct_dist_km = direct_dist / 1000.0

        # If distance < 5km, transit is not applicable
        if direct_dist <= 5000:
            direct_time = self.get_walk_time(direct_dist)
            return {
                "status": "SUCCESS",
                "departure": "Full Day",
                "options": [{
                    "type": "DIRECT ROAD ACCESS", "transfers": 0,
                    "departure_mins": time_window_start,
                    "arrival_mins": time_window_start + direct_time,
                    "arrival_time": self.format_min(time_window_start + direct_time),
                    "departure_time": self.format_min(time_window_start),
                    "duration_mins": direct_time,
                    "itinerary": [{"type": "WALK", "from": "Origin", "to": "Destination",
                                   "dist_km": round(direct_dist_km, 2),
                                   "duration_mins": direct_time,
                                   "arr": self.format_min(time_window_start + direct_time)}],
                    "summary": f"Direct Road Access, {direct_time} mins"
                }],
                "note": "Transit not applicable for distances < 5km."
            }

        # Reasonableness cap: scales with distance, minimum 120 mins, max 720 mins
        max_reasonable_mins = max(120, min(720, direct_dist_km * 12 + 60))
        TRANSFER_BUFFER = 5

        origins = self.project_point(origin_lat, origin_lng)
        dests = self.project_point(dest_lat, dest_lng)

        dest_sids = set(sid for sid, _ in dests)
        dest_walk_t = {sid: self.get_walk_time(d) for sid, d in dests}
        dest_walk_d = {sid: d for sid, d in dests}
        orig_walk_t = {sid: self.get_walk_time(d) for sid, d in origins}
        orig_walk_d = {sid: d for sid, d in origins}

        all_journeys = []

        # ===========================================================
        # PHASE 1: Direct (0-transfer) journeys
        # For every origin stop × route × trip × downstream dest stop
        # ===========================================================
        for o_sid in [s for s, _ in origins]:
            if o_sid not in self.stop_to_routes:
                continue
            wt_o = orig_walk_t[o_sid]

            for rid, o_idx in self.stop_to_routes[o_sid]:
                route_stops = self.routes[rid]['stops']
                # Find destination stops downstream on this route
                ds_dests = [(di, route_stops[di]) for di in range(o_idx + 1, len(route_stops))
                            if route_stops[di] in dest_sids]
                if not ds_dests:
                    continue

                for tid in self.routes[rid]['trips']:
                    dep1 = self.stop_times[tid][o_idx]['dep_mins']
                    if dep1 < time_window_start + wt_o or dep1 > time_window_end:
                        continue
                    home_dep = dep1 - wt_o

                    for d_ri, d_sid in ds_dests:
                        arr_d = self.stop_times[tid][d_ri]['arr_mins']
                        final = arr_d + dest_walk_t[d_sid]
                        dur = final - home_dep
                        if dur <= 0 or dur > max_reasonable_mins:
                            continue
                        bname = self.trip_to_bus.get(tid, 'Unknown')
                        all_journeys.append({
                            "type": "DIRECT", "transfers": 0,
                            "departure_mins": home_dep, "arrival_mins": final,
                            "duration_mins": round(dur),
                            "arrival_time": self.format_min(final),
                            "departure_time": self.format_min(home_dep),
                            "summary": f"Direct via {bname}, {round(dur)} mins",
                            "itinerary": [
                                {"type": "WALK", "from": "Origin",
                                 "to_stop": self.stops[o_sid]['name'],
                                 "dist_km": round(orig_walk_d[o_sid] / 1000, 2),
                                 "duration_mins": wt_o,
                                 "arr": self.format_min(dep1)},
                                {"type": "BUS", "bus_name": bname, "trip_id": tid,
                                 "from_stop": self.stops[o_sid]['name'],
                                 "to_stop": self.stops[d_sid]['name'],
                                 "dep": self.format_min(dep1),
                                 "arr": self.format_min(arr_d),
                                 "duration_mins": round(arr_d - dep1)},
                                {"type": "WALK", "from_stop": self.stops[d_sid]['name'],
                                 "to": "Destination",
                                 "dist_km": round(dest_walk_d[d_sid] / 1000, 2),
                                 "duration_mins": dest_walk_t[d_sid],
                                 "arr": self.format_min(final)}
                            ]
                        })

        # ===========================================================
        # PHASE 2: 1-Transfer journeys
        # Precompute destination-side connections, then enumerate
        # ===========================================================
        if max_transfers >= 1:
            # Pre-index: for each stop, list trips that depart from it and
            # reach a destination stop downstream on the SAME route.
            # Structure: stop_id -> [(tid, rid, dep_at_stop, d_sid, arr_at_dest)]
            dest_conn = {}
            for d_sid in dest_sids:
                if d_sid not in self.stop_to_routes:
                    continue
                for rid, d_idx in self.stop_to_routes[d_sid]:
                    route_stops = self.routes[rid]['stops']
                    for up_i in range(0, d_idx):
                        up_sid = route_stops[up_i]
                        for tid in self.routes[rid]['trips']:
                            dep_up = self.stop_times[tid][up_i]['dep_mins']
                            arr_d = self.stop_times[tid][d_idx]['arr_mins']
                            if dep_up < time_window_start or arr_d > time_window_end + 180:
                                continue
                            dest_conn.setdefault(up_sid, []).append(
                                (tid, rid, dep_up, d_sid, arr_d))

            # Also index footpath-reachable transfer stops
            fp_dest_conn = {}
            for from_sid, xfers in self.transfers.items():
                for to_sid, w_time in xfers:
                    if to_sid not in dest_conn:
                        continue
                    for (tid2, rid2, dep2, d_sid, arr2) in dest_conn[to_sid]:
                        fp_dest_conn.setdefault(from_sid, []).append(
                            (tid2, rid2, dep2, d_sid, arr2, to_sid, w_time))

            # Enumerate: for each origin trip, ride to each intermediate stop,
            # then check for connecting trips to destination.
            for o_sid in [s for s, _ in origins]:
                if o_sid not in self.stop_to_routes:
                    continue
                wt_o = orig_walk_t[o_sid]

                for rid1, o_idx in self.stop_to_routes[o_sid]:
                    route1_stops = self.routes[rid1]['stops']

                    for tid1 in self.routes[rid1]['trips']:
                        dep1 = self.stop_times[tid1][o_idx]['dep_mins']
                        if dep1 < time_window_start + wt_o or dep1 > time_window_end:
                            continue
                        home_dep = dep1 - wt_o
                        bname1 = self.trip_to_bus.get(tid1, 'Unknown')

                        for mid_i in range(o_idx + 1, len(route1_stops)):
                            mid_sid = route1_stops[mid_i]
                            arr_mid = self.stop_times[tid1][mid_i]['arr_mins']

                            if arr_mid - home_dep > max_reasonable_mins:
                                break  # further stops only later

                            # Check same-stop connections
                            self._try_transfer_connections(
                                all_journeys, dest_conn.get(mid_sid),
                                o_sid, mid_sid, None, 0,
                                tid1, rid1, bname1, dep1, arr_mid,
                                home_dep, wt_o, orig_walk_d[o_sid],
                                dest_walk_t, dest_walk_d,
                                max_reasonable_mins, TRANSFER_BUFFER)

                            # Check footpath connections
                            self._try_transfer_connections(
                                all_journeys, fp_dest_conn.get(mid_sid),
                                o_sid, mid_sid, None, 0,
                                tid1, rid1, bname1, dep1, arr_mid,
                                home_dep, wt_o, orig_walk_d[o_sid],
                                dest_walk_t, dest_walk_d,
                                max_reasonable_mins, TRANSFER_BUFFER,
                                is_footpath=True)

        # ===========================================================
        # PHASE 3: Deduplicate & Filter
        # ===========================================================
        # Deduplicate by (bus_names_used, origin_stop, dest_stop, departure_hour)
        seen = set()
        unique = []
        for j in all_journeys:
            buses = tuple(
                leg.get('bus_name', '') for leg in j['itinerary'] if leg['type'] == 'BUS')
            stops = tuple(
                leg.get('from_stop', '') + '->' + (leg.get('to_stop') or leg.get('to', ''))
                for leg in j['itinerary'] if leg['type'] == 'BUS')
            dep_bucket = j['departure_mins'] // 30  # 30-min departure buckets
            sig = (buses, stops, dep_bucket)
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(j)

        # Sort by departure time, then duration
        unique.sort(key=lambda x: (x['departure_mins'], x['duration_mins'], x['transfers']))

        # Apply profile-based pruning (Dominance Filter)
        pruned = self._prune_profile_options(unique)

        if not pruned:
            return {"status": "NO_ROUTE", "options": []}

        _safe_print(f"[McRAPTOR] Found {len(all_journeys)} raw -> {len(unique)} unique -> {len(pruned)} pruned journeys "
                    f"(direct_dist={direct_dist_km:.1f}km, max_dur={max_reasonable_mins:.0f}m)")

        self.enrich_options_geometry(pruned)

        return {
            "status": "SUCCESS",
            "departure": f"Full Day ({self.format_min(time_window_start)} – {self.format_min(time_window_end)})",
            "options": pruned
        }

    def _prune_profile_options(self, options, max_results=40):
        """
        Filters out redundant or strictly worse journeys.
        A journey J1 dominates J2 if it departs later AND arrives earlier.
        Also groups by departure buckets to ensure variety.
        """
        if not options: return []
        
        # 1. Strict Dominance Filter
        # Sort by departure (ASC) then arrival (ASC)
        options.sort(key=lambda x: (x['departure_mins'], x['arrival_mins']))
        
        dominant = []
        for j in options:
            is_dominated = False
            for d in dominant:
                # If d is better in every way:
                if d['departure_mins'] >= j['departure_mins'] and d['arrival_mins'] <= j['arrival_mins']:
                    # Special case: if d is exactly same but fewer transfers, or same everything
                    # we keep only one.
                    is_dominated = True
                    break
            if not is_dominated:
                # Also check if j dominates any in dominant
                dominant = [d for d in dominant if not (j['departure_mins'] >= d['departure_mins'] and j['arrival_mins'] <= d['arrival_mins'])]
                dominant.append(j)

        # 2. Diversity Filter: Group by 15-min departure buckets and keep top 2 per bucket
        dominant.sort(key=lambda x: (x['departure_mins'], x['duration_mins']))
        buckets = {}
        for j in dominant:
            b_id = j['departure_mins'] // 15
            buckets.setdefault(b_id, []).append(j)
        
        varied = []
        for b_id in sorted(buckets.keys()):
            # Keep top 2 per bucket (one fastest, one fewest transfers)
            b_opts = buckets[b_id]
            b_opts.sort(key=lambda x: (x['duration_mins'], x['transfers']))
            varied.append(b_opts[0])
            if len(b_opts) > 1:
                # Find best by transfers that isn't the fastest
                b_opts.sort(key=lambda x: (x['transfers'], x['duration_mins']))
                if b_opts[0] != varied[-1]:
                    varied.append(b_opts[0])
        
        # 3. Final Cap
        varied.sort(key=lambda x: x['departure_mins'])
        return varied[:max_results]

    def _try_transfer_connections(self, results, connections, o_sid, mid_sid,
                                   xfer_sid_override, xfer_walk_override,
                                   tid1, rid1, bname1, dep1, arr_mid,
                                   home_dep, wt_o, o_dist,
                                   dest_walk_t, dest_walk_d,
                                   max_dur, buffer, is_footpath=False):
        """Helper: check a list of connecting trips and append valid journeys."""
        if not connections:
            return
        for conn in connections:
            if is_footpath:
                tid2, rid2, dep2, d_sid, arr2, xfer_sid, xfer_walk = conn
            else:
                tid2, rid2, dep2, d_sid, arr2 = conn
                xfer_sid = mid_sid
                xfer_walk = 0

            if tid2 == tid1:
                continue  # same trip
            if dep2 < arr_mid + xfer_walk + buffer:
                continue  # can't catch it

            final = arr2 + dest_walk_t.get(d_sid, 0)
            dur = final - home_dep
            if dur <= 0 or dur > max_dur:
                continue

            bname2 = self.trip_to_bus.get(tid2, 'Unknown')
            mid_name = self.stops[mid_sid]['name']
            xfer_name = self.stops[xfer_sid]['name'] if xfer_sid != mid_sid else mid_name

            itin = [
                {"type": "WALK", "from": "Origin",
                 "to_stop": self.stops[o_sid]['name'],
                 "dist_km": round(o_dist / 1000, 2),
                 "duration_mins": wt_o,
                 "arr": self.format_min(dep1)},
                {"type": "BUS", "bus_name": bname1, "trip_id": tid1,
                 "from_stop": self.stops[o_sid]['name'],
                 "to_stop": mid_name,
                 "dep": self.format_min(dep1),
                 "arr": self.format_min(arr_mid),
                 "duration_mins": round(arr_mid - dep1)},
            ]
            if xfer_walk > 0 or xfer_sid != mid_sid:
                xfer_dist = self.haversine(
                    self.stops[mid_sid]['lat'], self.stops[mid_sid]['lng'],
                    self.stops[xfer_sid]['lat'], self.stops[xfer_sid]['lng'])
                itin.append(
                    {"type": "WALK", "from": mid_name,
                     "to_stop": xfer_name,
                     "dist_km": round(xfer_dist / 1000, 2),
                     "duration_mins": xfer_walk,
                     "arr": self.format_min(arr_mid + xfer_walk)})
            itin.extend([
                {"type": "BUS", "bus_name": bname2, "trip_id": tid2,
                 "from_stop": xfer_name,
                 "to_stop": self.stops[d_sid]['name'],
                 "dep": self.format_min(dep2),
                 "arr": self.format_min(arr2),
                 "duration_mins": round(arr2 - dep2)},
                {"type": "WALK", "from_stop": self.stops[d_sid]['name'],
                 "to": "Destination",
                 "dist_km": round(dest_walk_d.get(d_sid, 0) / 1000, 2),
                 "duration_mins": dest_walk_t.get(d_sid, 0),
                 "arr": self.format_min(final)}
            ])

            results.append({
                "type": "FASTEST", "transfers": 1,
                "departure_mins": home_dep, "arrival_mins": final,
                "duration_mins": round(dur),
                "arrival_time": self.format_min(final),
                "departure_time": self.format_min(home_dep),
                "summary": f"Transfer at {mid_name}, {round(dur)} mins",
                "itinerary": itin
            })

    def backtrack(self, target_stop, target_round, labels, parents, origins, dests, departure_mins):
        itin = []
        walk_end_dist = next((d[1] for d in dests if d[0] == target_stop), 0)
        walk_end_mins = self.get_walk_time(walk_end_dist)
        arr_final = labels[target_round][target_stop] + walk_end_mins
        
        itin.append({
            "type": "WALK",
            "from_stop": self.stops[target_stop]['name'],
            "to": "Destination",
            "dist_km": round(walk_end_dist / 1000, 2),
            "duration_mins": walk_end_mins,
            "arr": self.format_min(arr_final)
        })

        curr = target_stop
        for r in range(target_round, -1, -1):
            if curr in parents[r]:
                p_stop, trip_id, dep_t, arr_t = parents[r][curr]
                
                # Check if this is a transfer walk within the round
                if trip_id == "WALK" and p_stop is not None:
                    # 1. Add the transfer walk
                    p_name = self.stops[p_stop]['name']
                    dist_m = self.haversine(
                        self.stops[p_stop]['lat'], self.stops[p_stop]['lng'],
                        self.stops[curr]['lat'], self.stops[curr]['lng']
                    )
                    itin.append({
                        "type": "WALK",
                        "from": p_name,
                        "to_stop": self.stops[curr]['name'],
                        "dist_km": round(dist_m / 1000, 2),
                        "duration_mins": round(arr_t - dep_t),
                        "dep": self.format_min(dep_t),
                        "arr": self.format_min(arr_t)
                    })
                    curr = p_stop
                    
                    # 2. Extract the Bus ride that fed this transfer walk in the SAME round
                    if curr in parents[r]:
                        p_stop2, trip_id2, dep_t2, arr_t2 = parents[r][curr]
                        if trip_id2 != "WALK":
                            itin.append({
                                "type": "BUS",
                                "bus_name": self.trip_to_bus.get(trip_id2, "Unknown"),
                                "trip_id": trip_id2,
                                "from_stop": self.stops[p_stop2]['name'],
                                "to_stop": self.stops[curr]['name'],
                                "dep": self.format_min(dep_t2),
                                "arr": self.format_min(arr_t2),
                                "duration_mins": round(arr_t2 - dep_t2)
                            })
                            curr = p_stop2
                            
                else:
                    # Normal logic: either an initial walk (r=0) or a clean bus ride
                    if trip_id == "WALK":
                        p_name = self.stops[p_stop]['name'] if p_stop else "Origin"
                        dist_m = self.haversine(
                            self.stops[p_stop]['lat'] if p_stop else 0,
                            self.stops[p_stop]['lng'] if p_stop else 0,
                            self.stops[curr]['lat'], self.stops[curr]['lng']
                        ) if p_stop else next((o[1] for o in origins if o[0] == curr), 0)
                        
                        itin.append({
                            "type": "WALK",
                            "from": p_name,
                            "to_stop": self.stops[curr]['name'],
                            "dist_km": round(dist_m / 1000, 2),
                            "duration_mins": round(arr_t - dep_t),
                            "dep": self.format_min(dep_t),
                            "arr": self.format_min(arr_t)
                        })
                    else:
                        itin.append({
                            "type": "BUS",
                            "bus_name": self.trip_to_bus.get(trip_id, "Unknown"),
                            "trip_id": trip_id,
                            "from_stop": self.stops[p_stop]['name'],
                            "to_stop": self.stops[curr]['name'],
                            "dep": self.format_min(dep_t),
                            "arr": self.format_min(arr_t),
                            "duration_mins": round(arr_t - dep_t)
                        })
                    curr = p_stop
                    
                if not curr: break
        itin.reverse()
        return itin

    def format_min(self, mins, base_dep_mins=0):
        # Calculate how many days have passed relative to the very first day.
        # But realistically, mins > 1440 inherently means next day in our dataset.
        days = int(mins) // 1440
        h = (int(mins) // 60) % 24
        m = int(mins) % 60
        p = "AM" if h < 12 else "PM"
        h_12 = h % 12 or 12
        day_str = f" (+{days}d)" if days > 0 else ""
        return f"{h_12:02d}:{m:02d} {p}{day_str}"

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    router = RaptorRouter(script_dir)
    res = router.solve(23.23758, 86.4212616, 23.1223464, 86.4541511, 480)
    _safe_print(json.dumps(res, indent=2))

