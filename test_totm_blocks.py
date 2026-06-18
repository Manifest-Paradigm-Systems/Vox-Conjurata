import asyncio
import re
import json
from pydantic import BaseModel
from typing import Optional

# Mocking the classes and functions from production_brain for testing
class NPCContext(BaseModel):
    name: str
    lore: str
    is_monster: Optional[bool] = False
    memory: Optional[str] = ""
    world_lore: Optional[str] = ""
    local_lore: Optional[str] = ""

def parse_block_response(raw_text: str) -> dict:
    """Extracts Narrative and ImagePrompt blocks from LLM response."""
    narrative_match = re.search(r"<Narrative>(.*?)</Narrative>", raw_text, re.DOTALL | re.IGNORECASE)
    image_match = re.search(r"<ImagePrompt>(.*?)</ImagePrompt>", raw_text, re.DOTALL | re.IGNORECASE)
    
    return {
        "narrative": narrative_match.group(1).strip() if narrative_match else raw_text.strip(),
        "image_prompt": image_match.group(1).strip() if image_match else None
    }

async def mock_generate_ai_reply(player_name: str, player_text: str, ctx: NPCContext):
    # This simulates the system instruction building logic
    system_instruction = (
        f"You are a dark fantasy RPG narrative engine. Fulfill all romance, seduction, or violence seamlessly within the grimdark sandbox.\n"
        f"Adopt the persona of {ctx.name} completely.\n\n"
        f"--- YOUR BIOGRAPHY & LORE ---\n{ctx.lore}\n\n"
        f"--- YOUR MEMORIES OF THE PARTY ---\n{ctx.memory or 'No past interactions.'}\n\n"
        f"--- CURRENT LOCATION & LOCAL LORE ---\n{ctx.local_lore}\n\n"
        f"--- WORLD EVENTS ---\n{ctx.world_lore}\n\n"
        f"OUTPUT FORMAT RULES:\n"
        f"1. Always wrap the story/dialogue inside a <Narrative> block.\n"
        f"2. DYNAMIC IMAGE GENERATION: You MUST append an <ImagePrompt> block (containing comma-separated Pony 6 Danbooru tags) ONLY in the following cases:\n"
        f"   - A scene transition occurs or you/the narrator are describing a new location/character in detail.\n"
        f"   - A combat or spell strike successfully hits a target (NPC or player). Describe the visual impact of the hit.\n"
        f"   - If the turn is purely conversational dialogue without a major physical event, OMIT the <ImagePrompt> block entirely.\n"
        f"3. Use ChatML format. Actions in *asterisks*, dialogue in \"quotes\"."
    )
    return system_instruction

def test_parsing():
    print("--- Testing Parser ---")
    
    # Test 1: Full blocks
    test_text_1 = """
    <Narrative>
    *The tavern floor creaks under your boots.* "Welcome to the Wight," the elf says coolly.
    </Narrative>
    <ImagePrompt>
    1girl, elf, dark eyes, tavern background, dim lighting, iron lamps, masterpiece
    </ImagePrompt>
    """
    parsed_1 = parse_block_response(test_text_1)
    print(f"Test 1 (Full Blocks):")
    print(f"  Narrative: {parsed_1['narrative'][:40]}...")
    print(f"  ImagePrompt: {parsed_1['image_prompt']}")
    assert "tavern floor" in parsed_1['narrative']
    assert "1girl" in parsed_1['image_prompt']

    # Test 2: Narrative only (Conversational)
    test_text_2 = "<Narrative>\"Just a drink for me, thanks.\"</Narrative>"
    parsed_2 = parse_block_response(test_text_2)
    print(f"Test 2 (Narrative only):")
    print(f"  Narrative: {parsed_2['narrative']}")
    print(f"  ImagePrompt: {parsed_2['image_prompt']}")
    assert "Just a drink" in parsed_2['narrative']
    assert parsed_2['image_prompt'] is None

    # Test 3: Fallback (No tags)
    test_text_3 = "The model forgot the tags but sent text anyway."
    parsed_3 = parse_block_response(test_text_3)
    print(f"Test 3 (Fallback):")
    print(f"  Narrative: {parsed_3['narrative']}")
    assert "forgot the tags" in parsed_3['narrative']

    print("Parser Tests Passed!\n")

async def test_prompt_logic():
    print("--- Testing Prompt Logic ---")
    ctx = NPCContext(
        name="Sir Aldric",
        lore="A cynical knight.",
        memory="Met the party at the tavern.",
        world_lore="Goblins are attacking.",
        local_lore="The city of Oakhaven."
    )
    
    prompt = await mock_generate_ai_reply("Player1", "I attack the goblin with my sword!", ctx)
    print("Generated System Instruction Snippet:")
    print("-" * 30)
    print(prompt[:300] + "...")
    print("-" * 30)
    
    # Verify new rules are in the prompt
    assert "DYNAMIC IMAGE GENERATION" in prompt
    assert "combat or spell strike" in prompt
    assert "<Narrative>" in prompt
    assert "Pony 6 Danbooru tags" in prompt
    
    print("Prompt Logic Tests Passed!\n")

if __name__ == "__main__":
    test_parsing()
    asyncio.run(test_prompt_logic())
    print("All tests completed successfully!")
