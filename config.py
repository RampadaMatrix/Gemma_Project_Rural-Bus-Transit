import os
from pathlib import Path

# Base Directories
BASE_DIR = Path(__file__).resolve().parent
HITL_DIR = BASE_DIR / "HITL_Pipeline_new"
POLYLINE_DIR = BASE_DIR / "Polyline_Drawing_Pipeline"
SECRET_DIR = BASE_DIR / "ASecrets"

# HITL Files
HITL_INPUT = HITL_DIR / "BD_Phase1_HITL_input.json"
HITL_OUTPUT = HITL_DIR / "BD_Phase1_HITL_polyline_output.json"
HITL_TT_OUTPUT = HITL_DIR / "BD_Phase1_HITL_TT_output.json"
HITL_SECURED = HITL_DIR / "BD_Phase1_HITL_Secured.json"
MAP_FILE = HITL_DIR / "route_verification_map.html"

# Polyline Files
MASTER_BUSDATA = POLYLINE_DIR / "BusData_Phase_1.json"
STAGE_1_QUEUE = POLYLINE_DIR / "Stage_1_data.json"

# State
STATE_FILE = BASE_DIR / "pipeline_state.json"

# Security
def get_api_token():
    return os.getenv("API_AUTH_TOKEN", "purulia_transit_secret_2026")

def load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value
