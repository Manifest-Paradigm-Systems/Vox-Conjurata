import httpx
import json

def test_ingestion():
    url = "http://localhost:8080/api/ingest-actor"
    
    # Test: British Male
    payload = {
        "actorId": "aldric_knight",
        "name": "Sir Aldric",
        "lore": "A noble knight with a deep, authoritative British voice.",
        "stats": {"race": "human", "gender": "male"},
        "artPath": "tokens/knight.png"
    }
    
    print(f"\nIngesting {payload['name']}...")
    try:
        resp = httpx.post(url, json=payload, timeout=300.0)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_ingestion()
