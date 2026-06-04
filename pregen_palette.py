import httpx
import asyncio

PALETTE_KEYS = [
    "human_male_british", "human_female_british", "elf_male_french", "elf_female_french",
    "dwarf_male_scottish", "dwarf_female_scottish", "halfling_male_irish", "halfling_female_irish",
    "barbarian_male_german", "barbarian_female_german", "elder_male_british", "elder_female_british",
    "monster_beast", "monster_undead", "monster_dragon", "monster_demon", "monster_goblin"
]

async def pregenerate_palette():
    url = "http://localhost:8080/api/ingest-actor"
    # We trigger these via dummy actors that match the archetypes
    tasks = []
    for key in PALETTE_KEYS:
        # Construct a dummy actor to trigger the palette generation
        is_monster = key.startswith("monster")
        gender = "female" if "female" in key else "male"
        race = key.split("_")[0]
        if race == "monster": race = key.split("_")[1]
        
        payload = {
            "actorId": f"palette_trigger_{key}",
            "name": f"Palette Trigger {key}",
            "lore": "trigger",
            "stats": {"race": race, "gender": gender},
            "artPath": "",
            "isMonster": is_monster
        }
        tasks.append(payload)
        
    print(f"🚀 Triggering pre-generation of {len(tasks)} palette foundation seeds...")
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        # Process one by one to ensure foundation is built correctly
        for payload in tasks:
            print(f"  Forging foundation: {payload['actorId']}...")
            try:
                resp = await client.post(url, json=payload)
                print(f"  ✅ {payload['actorId']}: {resp.status_code}")
            except Exception as e:
                print(f"  ❌ {payload['actorId']} failed: {e}")

if __name__ == "__main__":
    asyncio.run(pregenerate_palette())
