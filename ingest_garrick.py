import httpx
import json

def ingest_garrick():
    url = "http://localhost:8080/api/ingest-actor"
    payload = {
        "actorId": "garrick_rogue",
        "name": "Garrick the Rogue",
        "lore": "A dashing rogue.",
        "stats": {"race": "human", "gender": "male"},
        "artPath": "tokens/garrick.png"
    }
    print("Ingesting Garrick...")
    resp = httpx.post(url, json=payload, timeout=300.0)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")

if __name__ == "__main__":
    ingest_garrick()
