import json
import os

def cleanup():
    # 1. BusData_Phase_1.json
    path1 = r"D:\Gemma_Project_Rural-Bus-Transit\Polyline_Drawing_Pipeline\BusData_Phase_1.json"
    if os.path.exists(path1):
        with open(path1, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data["buses"] = [b for b in data.get("buses", []) if b.get("reg_no") != "WB67B8770"]
        with open(path1, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"Cleaned {path1}")

    # 2. BD_Phase1_HITL_input.json
    path2 = r"D:\Gemma_Project_Rural-Bus-Transit\HITL_Pipeline_new\BD_Phase1_HITL_input.json"
    if os.path.exists(path2):
        with open(path2, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data["buses"] = [b for b in data.get("buses", []) if b.get("reg_no") != "WB67B8770"]
        with open(path2, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"Cleaned {path2}")

    # 3. pipeline_state.json
    path3 = r"D:\Gemma_Project_Rural-Bus-Transit\pipeline_state.json"
    if os.path.exists(path3):
        with open(path3, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "WB67B8770" in data.get("buses", {}):
            del data["buses"]["WB67B8770"]
        with open(path3, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print(f"Cleaned {path3}")

    # 4. image_lineage.json
    path4 = r"D:\Gemma_Project_Rural-Bus-Transit\The_Project_Scratch\image_lineage.json"
    if os.path.exists(path4):
        with open(path4, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "image.png" in data:
            data.remove("image.png")
        with open(path4, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"Cleaned {path4}")

if __name__ == "__main__":
    cleanup()
