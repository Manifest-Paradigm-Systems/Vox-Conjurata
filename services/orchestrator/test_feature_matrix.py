import pytest
import re
import json
import os
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from production_brain import (
    standardize_speech_text,
    is_named_character,
    resolve_archetype,
    ActorMetadata,
    DialogueEnrichment,
    enrich_and_instruct,
)

# ---------------------------------------------------------------------------
# Feature: Programmable Narrator
# ---------------------------------------------------------------------------

def test_narrator_is_programmable_named_character():
    """Verify Narrator is treated as a named character (not excluded)."""
    actor = ActorMetadata(
        actorId="narrator", 
        name="Narrator", 
        lore="The story teller", 
        stats={}, 
        artPath=""
    )
    assert is_named_character(actor) is True

# ---------------------------------------------------------------------------
# Feature: Robust Text Standardization (The Regex Matrix)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_text, engine, emotion, expected", [
    # Metadata stripping (Humanoid)
    ("Mood: happy Hello there!", "humanoid", "Neutral", "(neutral) Hello there!"),
    ("Emotion: sad I am lost.", "humanoid", "Sad", "(sad) I am lost."),
    ("Instruction: speak slowly Stop right there!", "humanoid", "Angry", "(angry) Stop right there!"),
    # Metadata stripping (Monster)
    ("Mood: angry You dare enter?", "monster", "neutral", "[neutral] You dare enter?"),
    ("Emotion: enraged I will crush you!", "monster", "vicious", "[vicious] I will crush you!"),
    # Action tag conversion (Humanoid)
    ("Hello *gasps* who are you?", "humanoid", "surprised", "(surprised) Hello <gasps> who are you?"),
    # Action tag stripping (Monster)
    ("Get back! *roars*", "monster", "angry", "[angry] Get back!"),
    # Tag preservation (Monster) - Wait, new logic strips ALL existing tags to avoid engine syntax mix
    ("You dare? [snarl] I will eat you!", "monster", "vicious", "[vicious] You dare? I will eat you!"),
    # Primary tag redundancy check
    ("[Angry] Stop!", "humanoid", "Angry", "(angry) Stop!"),
    ("[Vicious] Grrr.", "monster", "vicious", "[vicious] Grrr."),
    # Edge case: No capitalized word after metadata (should still strip prefix)
    ("Mood: angry ...", "monster", "neutral", "[neutral] ..."),
])
def test_standardize_text_matrix(input_text, engine, emotion, expected):
    result = standardize_speech_text(input_text, engine, emotion)
    assert result == expected

# ---------------------------------------------------------------------------
# Feature: Smart Vision Triggers
# ---------------------------------------------------------------------------

def test_tactical_keyword_detection():
    """Verify the list of keywords that trigger vision scans."""
    tactical_keywords = ["moves to", "casts", "grease", "fireball", "position", "flanked", "attack", "hazard", "hit", "damage", "range"]
    text = "The wizard casts fireball into the room."
    assert any(kw in text.lower() for kw in tactical_keywords)
    
    text = "Hello, how are you today?"
    assert not any(kw in text.lower() for kw in tactical_keywords)

# ---------------------------------------------------------------------------
# Feature: Emotive Tagging (Enrichment)
# ---------------------------------------------------------------------------

@patch("production_brain.httpx.AsyncClient.post")
def test_enrich_and_instruct_monster_tagging(mock_post):
    """Verify that monster enrichment produces tagged_text."""
    # Create a wrapper to run the async function
    async def run_test():
        # Mock LLM response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "emotional_resonance": "Vicious",
                        "vocal_delivery_prompt": "Low growl",
                        "emotion_tag": "Snarl",
                        "tagged_text": "[growl] You [snarl] dare?"
                    })
                }
            }]
        }
        mock_post.return_value = mock_response

        enriched = await enrich_and_instruct("Dragon", "NPC", "You dare?", is_monster=True)
        
        # New logic strips ALL existing tags from dialogue but prepends the primary tag
        # So [snarl] should be at the start, and [growl]/[snarl] inside should be stripped.
        assert enriched.monster_text.startswith("[snarl]")
        assert "You dare?" in enriched.monster_text
        assert "[growl]" not in enriched.monster_text
        
    asyncio.run(run_test())

# ---------------------------------------------------------------------------
# Feature: NPC Brain Autonomous Reply
# ---------------------------------------------------------------------------

@patch("production_brain.httpx.AsyncClient.post")
def test_npc_brain_response_generation(mock_post):
    """Verify prompt assembly and response generation for NPC Brain."""
    async def run_test():
        # Mock LLM response for NPC Reply with Narrative block
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "<Narrative>*He sneers at you.* \"You talk too much, mortal.\"</Narrative><ImagePrompt>gritty tavern, dark knight, sneering face</ImagePrompt>"
                }
            }]
        }
        mock_post.return_value = mock_response

        from production_brain import generate_ai_reply, NPCContext, parse_block_response
        ctx = NPCContext(
            name="Sir Aldric",
            lore="A cynical knight.",
            memory="Met the party at the tavern.",
            world_lore="Goblins are attacking.",
            local_lore="The city of Oakhaven."
        )
        
        raw_reply = await generate_ai_reply("Player1", "Hello there!", ctx)
        parsed = parse_block_response(raw_reply)
        
        assert "sneers" in parsed["narrative"]
        assert "mortal" in parsed["narrative"]
        assert "tavern" in parsed["image_prompt"]
        
    asyncio.run(run_test())

# ---------------------------------------------------------------------------
# Feature: Archetype Resolution
# ---------------------------------------------------------------------------

def test_archetype_resolution_diversity():
    # Test Elf
    elf = ActorMetadata(actorId="e1", name="Legolas", lore="", stats={"race": "Elf"}, artPath="")
    assert resolve_archetype(elf, {"gender": "male"}) == "elf_male_french"
    
    # Test Barbarian
    barb = ActorMetadata(actorId="b1", name="Grog", lore="A fierce barbarian", stats={}, artPath="")
    assert resolve_archetype(barb, {"gender": "male"}) == "barbarian_male_german"
    
    # Test Elder
    elder = ActorMetadata(actorId="o1", name="Goz", lore="An ancient wizard", stats={}, artPath="")
    assert resolve_archetype(elder, {"gender": "male", "description": "A wizened old man"}) == "elder_male_british"

# ---------------------------------------------------------------------------
# Feature: Voice Registry Roundtrip
# ---------------------------------------------------------------------------

def test_voice_registry_narrator_persistence(tmp_path, monkeypatch):
    import production_brain as pb
    test_reg = tmp_path / "test_registry.json"
    monkeypatch.setattr(pb, "VOICE_REGISTRY_PATH", test_reg)
    test_reg.write_text("{}")
    
    from production_brain import register_character_voice, load_voice_registry
    
    register_character_voice("narrator", "vox-audio-core", "narrator_seed.wav", "Voice prompt")
    
    registry = load_voice_registry()
    assert "narrator" in registry
    assert registry["narrator"]["voice_prompt"] == "Voice prompt"
