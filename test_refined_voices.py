import httpx
import json
import time

def test_ingestion():
    url = "http://localhost:8080/api/ingest-actor"
    
    # Test 1: Female Elf
    payload_elf = {
        "actorId": "merisiel_elf",
        "name": "Merisiel",
        "lore": "A female elven rogue, agile and deadly.",
        "stats": {"race": "elf", "gender": "female"},
        "artPath": "tokens/elf.png"
    }
    
    # Test 2: Ancient Dragon
    payload_dragon = {
        "actorId": "ancient_dragon_2",
        "name": "Ancient Red Dragon",
        "lore": "A massive, fire-breathing wyrm.",
        "stats": {"race": "dragon", "gender": "male"},
        "artPath": "tokens/dragon.png",
        "isMonster": True
    }
    
    for payload in [payload_elf, payload_dragon]:
        print(f"\nIngesting {payload['name']}...")
        start = time.time()
        try:
            resp = httpx.post(url, json=payload, timeout=300.0)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
            print(f"Time taken: {time.time() - start:.2f}s")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_ingestion()
