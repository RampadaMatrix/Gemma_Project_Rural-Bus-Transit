#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==================================================="
echo "Rural Bus Transit Intelligence - Local Demo"
echo "==================================================="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but was not found in PATH."
  exit 1
fi

if [ ! -f ".env" ]; then
  echo ".env file not found. Copy .env.example to .env and add your API keys first."
  exit 1
fi

echo "Checking Python dependencies..."
python3 -m pip install -r requirements.txt

echo
echo "Starting Master Orchestrator (Backend + Agent)..."
echo "Please wait for the servers to spin up. On Windows, a separate HITL Server terminal should open."
echo "UI:  http://localhost:5000"
echo "API: http://localhost:8000"
echo
python3 purulia_pipeline_orchestrator.py
