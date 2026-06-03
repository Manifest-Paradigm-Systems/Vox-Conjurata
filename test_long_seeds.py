import httpx
import json

def test_ingestion():
    url = "http://localhost:8080/api/ingest-actor"
    
    # Test 1: Humanoid (Dwarf)
    payload_dwarf = {
        "actorId": "thorin_ironfoot",
        "name": "Thorin Ironfoot",
        "lore": "A proud dwarven warrior from the Iron Hills.",
        "stats": {"race": "dwarf", "gender": "male"},
        "artPath": "tokens/dwarf.png"
    }
    
    # Test 2: Monster (Goblin)
    payload_goblin = {
        "actorId": "stinky_goblin",
        "name": "Stinky",
        "lore": "A small, mischievous goblin scout.",
        "stats": {"race": "goblin", "gender": "male"},
        "artPath": "tokens/goblin.png",
        "isMonster": True
    }
    
    for payload in [payload_dwarf, payload_goblin]:
        print(f"\nIngesting {payload['name']}...")
        try:
            resp = httpx.post(url, json=payload, timeout=300.0)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_ingestion()
