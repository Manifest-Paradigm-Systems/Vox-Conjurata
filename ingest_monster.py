import httpx
import json

def ingest_monster():
    url = "http://localhost:8080/api/ingest-actor"
    payload = {
        "actorId": "ancient_dragon",
        "name": "Ancient Red Dragon",
        "lore": "A massive, fire-breathing wyrm with a deep, booming, ancient voice.",
        "stats": {"race": "dragon", "gender": "male"},
        "artPath": "tokens/dragon.png",
        "isMonster": True
    }
    
    print(f"Ingesting monster {payload['name']}...")
    try:
        resp = httpx.post(url, json=payload, timeout=300.0)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    ingest_monster()
