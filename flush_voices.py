import json

file_path = "settings/token_voice_mappings.json"

with open(file_path, "r") as f:
    data = json.load(f)

for token_id, details in data.get("token_voice_mappings", {}).items():
    # Keep the structure but wipe engine and seed assignments
    # We want to force a blank slate for genuine local mapping
    details["engine"] = ""
    details["voice_seed"] = ""
    # We also wipe any potentially stale voice labels if they exist
    if "voice" in details:
        details["voice"] = ""

with open(file_path, "w") as f:
    json.dump(data, f, indent=2)

print("Voice assignments flushed for all tokens.")
