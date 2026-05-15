import json
import os
from gemma_interface import GemmaAgent

# --- THE VISION PROMPT (Strictly Metadata Compliant) ---
DIGITIZATION_PROMPT = """
You are a Purulia Transit Data Auditor. Your task is to digitize bus schedules into the 'JSON-HITL-v5' schema.

STRICT ARCHITECTURAL RULES (MANDATORY):
1. **The "No Hub-Through" Rule**: Purulia is strictly PROHIBITED as an intermediate stop. 
   - If a route passes through Purulia (e.g., A -> Purulia -> B), you MUST split it into TWO discrete movements.
   - Movement 1: A -> Purulia (Direction: UP)
   - Movement 2: Purulia -> B (Direction: DOWN)

2. **Nomenclature Logic**:
   - 'UP': Movement where Destination is 'Purulia'.
   - 'DOWN': Movement where Origin is 'Purulia'.
   - 'towards [Destination]': Use this ONLY if 'Purulia' is NOT in the entire trip (Bypass route).

3. **Terminal Anchoring**:
   - The 'origin' string must EXACTLY match the name of the first stop.
   - The 'destination' string must EXACTLY match the name of the last stop.

4. **Temporal Schema**:
   - Every stop MUST have 'arrival_time' and 'departure_time'.
   - Use 'null' if a time is not specifically visible on the image.

5. **HITL Learning**:
   - Every stop must include a 'hitl_learning' object: {"status": "VERIFIED", "historical_offset_mins": 0, "variance_range": [0,0], "total_reports": 0}

OUTPUT FORMAT (STRICT JSON ONLY):
{
  "bus_name": "...",
  "reg_no": "...",
  "primary_hub": "...",
  "movements": [
    {
      "trip_id": "1st trip",
      "direction": "UP/DOWN/towards ...",
      "origin": "...",
      "destination": "...",
      "stops": [
        {
          "name": "...",
          "arrival_time": "HH:MM AM/PM" or null,
          "departure_time": "HH:MM AM/PM" or null,
          "stop_type": "ORIGIN/INTERMEDIARY/DESTINATION",
          "hitl_learning": {...}
        }
      ]
    }
  ]
}
"""

def digitize_schedule_image(image_path: str):
    agent = GemmaAgent(model_id="gemma-4-31b-it")
    print(f"[INGESTOR] Processing: {image_path}")
    
    # We ask for JSON only to avoid conversational filler
    raw_response = agent.analyze_image(DIGITIZATION_PROMPT + "\nOutput ONLY the JSON code block.", image_path)
    
    # Clean the response to ensure it's valid JSON
    try:
        clean_json = raw_response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()
            
        data = json.loads(clean_json)
        return data
    except Exception as e:
        print(f"[ERROR] Failed to parse Gemma response: {e}")
        print(f"[DEBUG] Raw response: {raw_response[:500]}...")
        return None

if __name__ == "__main__":
    # Test logic
    test_img = "sample_schedule.jpg"
    if os.path.exists(test_img):
        result = digitize_schedule_image(test_img)
        print(json.dumps(result, indent=2))
    else:
        print(f"Please provide a sample image at {test_img} to test.")
