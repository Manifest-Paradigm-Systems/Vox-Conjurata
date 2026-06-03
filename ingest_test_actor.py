import httpx
import json

def ingest_test_actor():
    url = "http://localhost:8080/api/ingest-actor"
    payload = {
        "actorId": "garrick_rogue",
        "name": "Garrick the Rogue",
        "lore": "A dashing half-elf rogue with a penchant for trouble.",
        "stats": {"race": "half-elf", "gender": "male"},
        "artPath": "tokens/garrick.png" # Dummy path
    }
    
    print(f"Ingesting actor {payload['name']}...")
    try:
        resp = httpx.post(url, json=payload, timeout=300.0)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    ingest_test_actor()
