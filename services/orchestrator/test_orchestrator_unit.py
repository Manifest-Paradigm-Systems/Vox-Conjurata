import pytest
from production_brain import SpeechPipelineFactory, EdgeTTSEngine, FishSpeechEngine, CosyVoiceEngine

@pytest.fixture
def factory():
    return SpeechPipelineFactory()

@pytest.fixture
def base_config():
    return {
        "narrator_preferences": {
            "default_voice": "en-US-ChristopherNeural",
            "rate_adjustment": "+0%"
        },
        "tier_routing": {
            "monster_engine": "fish-speech",
            "humanoid_engine": "cosyvoice"
        }
    }

def test_vram_triggered_fallback(factory, base_config):
    # When VRAM threshold is triggered, the engine must fall back to Edge-TTS
    engine = factory.get_engine(
        is_monster=False,
        stats={},
        config=base_config,
        vram_triggered=True
    )
    assert isinstance(engine, EdgeTTSEngine)
    assert engine.voice_name == "en-US-ChristopherNeural"

def test_monster_routing(factory, base_config):
    # When is_monster is True, it routes to Fish Speech
    engine = factory.get_engine(
        is_monster=True,
        stats={},
        config=base_config,
        vram_triggered=False
    )
    assert isinstance(engine, FishSpeechEngine)

def test_humanoid_routing_normal(factory, base_config):
    # When is_monster is False, stats are normal, it routes to CosyVoice
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "human", "level": 3},
        config=base_config,
        vram_triggered=False
    )
    assert isinstance(engine, CosyVoiceEngine)

def test_humanoid_routing_high_level(factory, base_config):
    # If level > 5, it should fall back to FishSpeech (monster engine)
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "human", "level": 6},
        config=base_config,
        vram_triggered=False
    )
    assert isinstance(engine, FishSpeechEngine)

def test_humanoid_routing_special_race(factory, base_config):
    # If race in ["undead", "fiend", "aberration", "dragon"], it routes to FishSpeech
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "fiend", "level": 1},
        config=base_config,
        vram_triggered=False
    )
    assert isinstance(engine, FishSpeechEngine)

def test_edge_tts_explicit_routing(factory, base_config):
    # If config explicitly routes humanoid to edge-tts
    custom_config = dict(base_config)
    custom_config["tier_routing"] = {
        "monster_engine": "fish-speech",
        "humanoid_engine": "edge-tts"
    }
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "human", "level": 1},
        config=custom_config,
        vram_triggered=False
    )
    assert isinstance(engine, EdgeTTSEngine)
    assert engine.voice_name == "en-US-ChristopherNeural"
