import httpx
import time
import json
import os

ORCHESTRATOR_URL = "http://localhost:8080/api/v1/diagnostics/latest"

def check_for_errors():
    try:
        response = httpx.get(ORCHESTRATOR_URL, timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") != "nominal":
                print(f"🚨 [HEALING WATCHER] Error detected: {json.dumps(data, indent=2)}")
                return data
    except Exception as e:
        pass
    return None

if __name__ == "__main__":
    print("🧠 [HEALING WATCHER] Active. Monitoring orchestrator for client-side telemetry...")
    last_error = None
    while True:
        current_error = check_for_errors()
        if current_error and current_error != last_error:
            # We found a new error. In a real self-healing loop, 
            # I would trigger code modifications here.
            # For now, I just print it so it appears in my background logs.
            last_error = current_error
        time.sleep(5)
