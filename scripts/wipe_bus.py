import json
import os
import sys
import argparse

def wipe_bus(reg_no, base_dir=r'd:\Gemma Project'):
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
        os.path.join(base_dir, 'HITL_Pipeline_new', 'GITL_Audit_Report.json')
    ]

    print(f"--- WIPING BUS {reg_no} ---")

    for fp in files_to_check:
        if not os.path.exists(fp):
            continue
            
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            modified = False
            
            # Handle list-based files (most HITL/Polyline files)
            if isinstance(data, dict) and 'buses' in data and isinstance(data['buses'], list):
                orig_len = len(data['buses'])
                data['buses'] = [b for b in data['buses'] if b.get('reg_no') != reg_no]
                if len(data['buses']) < orig_len:
                    modified = True
                    print(f"  [LIST] Removed from {os.path.basename(fp)}")

            # Handle dict-based files (pipeline_state.json)
            elif isinstance(data, dict) and 'buses' in data and isinstance(data['buses'], dict):
                if reg_no in data['buses']:
                    del data['buses'][reg_no]
                    modified = True
                    print(f"  [DICT] Removed from {os.path.basename(fp)}")

            # Handle Session ledger
            elif isinstance(data, dict) and 'sessions' in data and isinstance(data['sessions'], list):
                orig_len = len(data['sessions'])
                data['sessions'] = [s for s in data['sessions'] if s.get('active_bus_reg') != reg_no]
                if len(data['sessions']) < orig_len:
                    modified = True
                    print(f"  [SESS] Removed session from {os.path.basename(fp)}")

            # Handle generic top-level dict (GITL_Audit_Report.json)
            elif isinstance(data, dict) and reg_no in data:
                del data[reg_no]
                modified = True
                print(f"  [ROOT] Removed from {os.path.basename(fp)}")

            if modified:
                with open(fp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                    
        except Exception as e:
            print(f"  [ERROR] Failed to process {os.path.basename(fp)}: {e}")

    # Remove any specific log files
    log_dir = os.path.join(base_dir, 'Pipeline_Logs')
    specific_log = os.path.join(log_dir, f"error_{reg_no}.log")
    if os.path.exists(specific_log):
        os.remove(specific_log)
        print(f"  [LOG] Deleted {os.path.basename(specific_log)}")

    print("--- WIPE COMPLETE ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe a bus from the Purulia Transit pipeline.")
    parser.add_argument("reg_no", help="The registration number to wipe.")
    args = parser.parse_args()
    wipe_bus(args.reg_no)
