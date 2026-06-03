import pytest
from production_brain import SpeechPipelineFactory, FishSpeechEngine, CosyVoiceEngine


@pytest.fixture
def factory():
    return SpeechPipelineFactory()


@pytest.fixture
def base_config():
    return {
        "tier_routing": {
            "monster_engine": "fishspeech",
            "humanoid_engine": "cosyvoice",
        },
        "system_settings": {
            "vram_threshold_gb": 26.0,
        },
    }


def test_monster_routing(factory, base_config):
    """When is_monster is True, it routes to Fish Speech."""
    engine = factory.get_engine(
        is_monster=True,
        stats={},
        config=base_config,
        vram_triggered=False,
    )
    assert isinstance(engine, FishSpeechEngine)


def test_humanoid_routing_normal(factory, base_config):
    """When is_monster is False, it routes to CosyVoice."""
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "human", "level": 3},
        config=base_config,
        vram_triggered=False,
    )
    assert isinstance(engine, CosyVoiceEngine)


def test_monster_engine_config_override(factory, base_config):
    """When tier_routing.monster_engine is 'cosyvoice', monsters use CosyVoice."""
    config = {**base_config, "tier_routing": {"monster_engine": "cosyvoice"}}
    engine = factory.get_engine(
        is_monster=True,
        stats={"race": "dragon", "level": 10},
        config=config,
        vram_triggered=False,
    )
    assert isinstance(engine, CosyVoiceEngine)


def test_humanoid_engine_config_override(factory, base_config):
    """When tier_routing.humanoid_engine is 'fishspeech', humanoids use Fish Speech."""
    config = {**base_config, "tier_routing": {"humanoid_engine": "fishspeech"}}
    engine = factory.get_engine(
        is_monster=False,
        stats={"race": "human", "level": 1},
        config=config,
        vram_triggered=False,
    )
    assert isinstance(engine, FishSpeechEngine)


def test_missing_tier_routing_defaults_to_cosyvoice(factory):
    """When no tier_routing config exists, defaults to CosyVoice for non-monsters."""
    engine = factory.get_engine(
        is_monster=False,
        stats={},
        config={},
        vram_triggered=False,
    )
    assert isinstance(engine, CosyVoiceEngine)


def test_missing_tier_routing_defaults_to_fishspeech(factory):
    """When no tier_routing config exists, defaults to Fish Speech for monsters."""
    engine = factory.get_engine(
        is_monster=True,
        stats={},
        config={},
        vram_triggered=False,
    )
    assert isinstance(engine, FishSpeechEngine)