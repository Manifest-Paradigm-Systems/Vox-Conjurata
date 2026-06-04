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
        # Use generic names to avoid 'named character' unique seed generation
        # We just want to trigger the foundation palette generation
        is_monster = key.startswith("monster")
        gender = "female" if "female" in key else "male"
        
        # Map back to a name that resolve_archetype will handle generically
        name = "Generic Actor"
        race = "human"
        if "elf" in key: race = "elf"
        if "dwarf" in key: race = "dwarf"
        if "halfling" in key: race = "halfling"
        if "barbarian" in key: name = "Barbarian"
        if "elder" in key: name = "Elderly Person"
        
        if is_monster:
            race = key.split("_")[1]
            name = race.capitalize()

        payload = {
            "actorId": f"palette_trigger_{key}",
            "name": name,
            "lore": "generic",
            "stats": {"race": race, "gender": gender},
            "artPath": "", # Skip vision
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
