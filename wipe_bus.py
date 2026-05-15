import json
import os
import sys
import argparse
import glob

def wipe_bus(reg_no, base_dir=r'd:\Gemma_Project_Rural-Bus-Transit'):
    reg_no = reg_no.strip()
    print(f"--- WIPING BUS AND ALL RELATED INSTANCES FOR {reg_no} ---")

    files_to_check = [
        os.path.join(base_dir, 'pipeline_state.json'),
        os.path.join(base_dir, 'GACC_Sessions.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'BD_Phase1_HITL_input.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'BD_Phase1_HITL_TT_output.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'BD_Phase1_HITL_polyline_output.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'BD_Phase1_HITL_Secured.json'),
        os.path.join(base_dir, 'Polyline_Drawing_Pipeline', 'BusData_Phase_1.json'),
        os.path.join(base_dir, 'Polyline_Drawing_Pipeline', 'Stage_1_data.json'),
        os.path.join(base_dir, 'Polyline_Drawing_Pipeline', 'BusData_Phase_1_polyline_stoppages.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'GITL_Audit_Report.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'precomputed_cache.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'Raptor_data', 'raptor_bundle.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'Raptor_data', 'raptor_trips.json'),
        os.path.join(base_dir, 'HITL_Pipeline_new', 'Raptor_data', 'raptor_stop_times.json'),
    ]

    # Step 1: First scan all files to discover all possible bus_names associated with this reg_no
    bus_names = set()
    for fp in files_to_check:
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Check buses list
                for b in data.get('buses', []) if isinstance(data.get('buses'), list) else []:
                    if isinstance(b, dict) and str(b.get('reg_no', '')).strip() == reg_no:
                        bn = str(b.get('bus_name', '')).strip()
                        if bn: bus_names.add(bn)
                # Check locked_ids list
                for item in data.get('locked_ids', []) if isinstance(data.get('locked_ids'), list) else []:
                    item_str = str(item).strip()
                    if f"({reg_no})" in item_str:
                        bn = item_str.split('(')[0].strip()
                        if bn: bus_names.add(bn)
                # Check secured_buses dict in cache
                payload = data.get('payload', {}) if isinstance(data.get('payload'), dict) else data
                sb = payload.get('secure_registry', {}) or payload.get('secured_buses', {})
                if isinstance(sb, dict):
                    for k, v in sb.items():
                        if isinstance(v, dict) and str(v.get('reg_no', '')).strip() == reg_no:
                            bn = str(v.get('bus_name', '')).strip()
                            if bn: bus_names.add(bn)
        except Exception:
            pass

    print(f"  [DISCOVERY] Associated bus names found for {reg_no}: {list(bus_names)}")

    wiped_trip_ids = set()

    for fp in files_to_check:
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            modified = False

            # --- GENERAL LIST OF BUSES ---
            if isinstance(data, dict) and 'buses' in data and isinstance(data['buses'], list):
                orig_len = len(data['buses'])
                data['buses'] = [b for b in data['buses'] if isinstance(b, dict) and str(b.get('reg_no', '')).strip() != reg_no]
                if len(data['buses']) < orig_len:
                    modified = True
                    print(f"  [LIST] Removed {orig_len - len(data['buses'])} bus instance(s) from {os.path.basename(fp)}")

            # --- PIPELINE STATE DICTS ---
            if isinstance(data, dict) and 'buses' in data and isinstance(data['buses'], dict):
                if reg_no in data['buses']:
                    del data['buses'][reg_no]
                    modified = True
                    print(f"  [DICT] Removed bus state from {os.path.basename(fp)}")
            if isinstance(data, dict) and 'bus_context' in data and isinstance(data['bus_context'], dict):
                if reg_no in data['bus_context']:
                    del data['bus_context'][reg_no]
                    modified = True
                    print(f"  [CONTEXT] Removed bus context from {os.path.basename(fp)}")

            # --- SESSION LEDGER ---
            if isinstance(data, dict) and 'sessions' in data and isinstance(data['sessions'], list):
                orig_len = len(data['sessions'])
                data['sessions'] = [s for s in data['sessions'] if isinstance(s, dict) and str(s.get('active_bus_reg', '')).strip() != reg_no]
                if len(data['sessions']) < orig_len:
                    modified = True
                    print(f"  [SESS] Removed {orig_len - len(data['sessions'])} session(s) from {os.path.basename(fp)}")

            # --- ROOT LEVEL DICT (e.g. Audit report) ---
            if isinstance(data, dict) and reg_no in data and not ('buses' in data or 'payload' in data):
                del data[reg_no]
                modified = True
                print(f"  [ROOT] Removed root key from {os.path.basename(fp)}")

            # --- LOCKED IDS & METADATA (Secured & Cache) ---
            if isinstance(data, dict) and 'locked_ids' in data and isinstance(data['locked_ids'], list):
                orig_len = len(data['locked_ids'])
                data['locked_ids'] = [item for item in data['locked_ids'] if not (f"({reg_no})" in str(item) or str(item).strip() == reg_no)]
                if len(data['locked_ids']) < orig_len:
                    modified = True
                    print(f"  [LOCKED_IDS] Removed from {os.path.basename(fp)}")
                    if 'metadata' in data and isinstance(data['metadata'], dict) and 'lock_count' in data['metadata']:
                        data['metadata']['lock_count'] = len(data['locked_ids'])

            # --- PRECOMPUTED CACHE SPECIFIC ---
            if isinstance(data, dict) and ('payload' in data or 'hitl_status' in data):
                payload = data['payload'] if 'payload' in data and isinstance(data['payload'], dict) else data
                
                # hitl_status dict
                if 'hitl_status' in payload and isinstance(payload['hitl_status'], dict):
                    keys_to_del = [k for k in payload['hitl_status'] if f"({reg_no})" in k or k == reg_no]
                    for k in keys_to_del:
                        del payload['hitl_status'][k]
                        modified = True
                        print(f"  [CACHE:hitl_status] Removed key {k}")
                
                # secure_bus_ids list
                if 'secure_bus_ids' in payload and isinstance(payload['secure_bus_ids'], list):
                    orig_len = len(payload['secure_bus_ids'])
                    payload['secure_bus_ids'] = [item for item in payload['secure_bus_ids'] if not (f"({reg_no})" in str(item) or str(item).strip() == reg_no)]
                    if len(payload['secure_bus_ids']) < orig_len: modified = True

                # tt_hitl_ids / polyline_hitl_ids lists
                for key_name in ['tt_hitl_ids', 'polyline_hitl_ids']:
                    if key_name in payload and isinstance(payload[key_name], list):
                        orig_len = len(payload[key_name])
                        payload[key_name] = [item for item in payload[key_name] if not (f"({reg_no})" in str(item) or str(item).strip() == reg_no)]
                        if len(payload[key_name]) < orig_len: modified = True

                # secure_registry / secured_buses dict
                for reg_key in ['secure_registry', 'secured_buses']:
                    if reg_key in payload and isinstance(payload[reg_key], dict):
                        keys_to_del = [k for k, v in payload[reg_key].items() if f"({reg_no})" in k or k == reg_no or (isinstance(v, dict) and str(v.get('reg_no','')).strip() == reg_no)]
                        for k in keys_to_del:
                            del payload[reg_key][k]
                            modified = True
                            print(f"  [CACHE:{reg_key}] Removed {k}")

                # verified_routes / secure_verified_routes lists of dicts
                for vr_key in ['verified_routes', 'secure_verified_routes']:
                    if vr_key in payload and isinstance(payload[vr_key], list):
                        orig_len = len(payload[vr_key])
                        payload[vr_key] = [r for r in payload[vr_key] if isinstance(r, dict) and str(r.get('reg_no', '')).strip() != reg_no and not (f"({reg_no})" in str(r.get('display_name', '')))]
                        if len(payload[vr_key]) < orig_len:
                            modified = True
                            print(f"  [CACHE:{vr_key}] Removed {orig_len - len(payload[vr_key])} route(s)")

                # hitl_storage dict
                if 'hitl_storage' in payload and isinstance(payload['hitl_storage'], dict):
                    keys_to_del = [k for k in payload['hitl_storage'] if f"({reg_no})" in k or k == reg_no or k in bus_names]
                    for k in keys_to_del:
                        del payload['hitl_storage'][k]
                        modified = True
                        print(f"  [CACHE:hitl_storage] Removed {k}")

            # --- RAPTOR BUNDLE SPECIFIC ---
            if os.path.basename(fp) == 'raptor_bundle.json' and isinstance(data, dict) and 'data' in data and isinstance(data['data'], dict):
                r_data = data['data']
                if 'trips' in r_data and isinstance(r_data['trips'], list):
                    orig_trips = len(r_data['trips'])
                    new_trips = []
                    for t in r_data['trips']:
                        is_target = False
                        if str(t.get('bus_reg_key', '')).strip() == reg_no: is_target = True
                        elif str(t.get('bus_name', '')).strip() == reg_no: is_target = True
                        elif str(t.get('bus_name', '')).strip() in bus_names: is_target = True
                        elif f"_{reg_no.lower()}_" in str(t.get('trip_id', '')).lower(): is_target = True
                        for bn in bus_names:
                            if f"_{bn.lower()}_" in str(t.get('trip_id', '')).lower(): is_target = True

                        if is_target:
                            wiped_trip_ids.add(str(t.get('trip_id', '')))
                        else:
                            new_trips.append(t)

                    if len(new_trips) < orig_trips:
                        r_data['trips'] = new_trips
                        modified = True
                        print(f"  [RAPTOR] Wiped {orig_trips - len(new_trips)} trip(s) matching {reg_no}")

                if 'stop_times' in r_data and isinstance(r_data['stop_times'], list) and wiped_trip_ids:
                    orig_st = len(r_data['stop_times'])
                    r_data['stop_times'] = [st for st in r_data['stop_times'] if isinstance(st, dict) and str(st.get('trip_id', '')) not in wiped_trip_ids]
                    if len(r_data['stop_times']) < orig_st:
                        modified = True
                        print(f"  [RAPTOR] Wiped {orig_st - len(r_data['stop_times'])} stop_time(s)")

                # Update stats
                if modified and 'stats' in data and isinstance(data['stats'], dict):
                    data['stats']['trips'] = len(r_data.get('trips', []))
                    data['stats']['stop_times'] = len(r_data.get('stop_times', []))

            # --- INDIVIDUAL RAPTOR ARTIFACTS (if any) ---
            if os.path.basename(fp) == 'raptor_trips.json' and isinstance(data, list) and wiped_trip_ids:
                orig_len = len(data)
                data = [t for t in data if isinstance(t, dict) and str(t.get('trip_id', '')) not in wiped_trip_ids]
                if len(data) < orig_len:
                    modified = True
                    print(f"  [RAPTOR_SPLIT] Removed from {os.path.basename(fp)}")

            if os.path.basename(fp) == 'raptor_stop_times.json' and isinstance(data, list) and wiped_trip_ids:
                orig_len = len(data)
                data = [st for st in data if isinstance(st, dict) and str(st.get('trip_id', '')) not in wiped_trip_ids]
                if len(data) < orig_len:
                    modified = True
                    print(f"  [RAPTOR_SPLIT] Removed from {os.path.basename(fp)}")

            if modified:
                with open(fp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2 if os.path.basename(fp) != 'pipeline_state.json' else 4)
                print(f"  [SUCCESS] Written updated clean {os.path.basename(fp)}")

        except Exception as e:
            print(f"  [ERROR] Failed to process {os.path.basename(fp)}: {e}")

    # Remove any specific log files
    log_dir = os.path.join(base_dir, 'Pipeline_Logs')
    specific_log = os.path.join(log_dir, f"error_{reg_no}.log")
    if os.path.exists(specific_log):
        try:
            os.remove(specific_log)
            print(f"  [LOG] Deleted {os.path.basename(specific_log)}")
        except Exception:
            pass

    # Also clean any session files in ZGemma_files/Sessions if applicable
    sess_dir = os.path.join(base_dir, 'ZGemma_files', 'Sessions')
    if os.path.exists(sess_dir):
        for s_file in glob.glob(os.path.join(sess_dir, '*.json')):
            try:
                with open(s_file, 'r', encoding='utf-8') as sf:
                    s_data = json.load(sf)
                if isinstance(s_data, list):
                    new_s = [m for m in s_data if not (isinstance(m, dict) and (reg_no in str(m.get('text','')) or reg_no in str(m.get('thought',''))))]
                    if len(new_s) < len(s_data):
                        with open(s_file, 'w', encoding='utf-8') as sf:
                            json.dump(new_s, sf, indent=2)
            except Exception:
                pass

    print("--- WIPE COMPLETE ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe a bus and all related instances from the Purulia Transit pipeline.")
    parser.add_argument("reg_no", help="The registration number to wipe.")
    args = parser.parse_args()
    wipe_bus(args.reg_no)
