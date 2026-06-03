import pytest
from production_brain import (
    SpeechPipelineFactory,
    FishSpeechEngine,
    CosyVoiceEngine,
    standardize_speech_text,
    is_named_character,
    resolve_archetype,
    load_voice_registry,
    register_character_voice,
    resolve_seed_path,
    ActorMetadata,
)


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


# ---------------------------------------------------------------------------
# Engine routing tests
# ---------------------------------------------------------------------------

def test_monster_routing(factory, base_config):
    engine = factory.get_engine(is_monster=True, stats={}, config=base_config, vram_triggered=False)
    assert isinstance(engine, FishSpeechEngine)


def test_humanoid_routing_normal(factory, base_config):
    engine = factory.get_engine(is_monster=False, stats={"race": "human", "level": 3}, config=base_config, vram_triggered=False)
    assert isinstance(engine, CosyVoiceEngine)


def test_monster_engine_config_override(factory, base_config):
    config = {**base_config, "tier_routing": {"monster_engine": "cosyvoice"}}
    engine = factory.get_engine(is_monster=True, stats={}, config=config, vram_triggered=False)
    assert isinstance(engine, CosyVoiceEngine)


def test_humanoid_engine_config_override(factory, base_config):
    config = {**base_config, "tier_routing": {"humanoid_engine": "fishspeech"}}
    engine = factory.get_engine(is_monster=False, stats={}, config=config, vram_triggered=False)
    assert isinstance(engine, FishSpeechEngine)


def test_missing_tier_routing_defaults_to_cosyvoice(factory):
    engine = factory.get_engine(is_monster=False, stats={}, config={}, vram_triggered=False)
    assert isinstance(engine, CosyVoiceEngine)


def test_missing_tier_routing_defaults_to_fishspeech(factory):
    engine = factory.get_engine(is_monster=True, stats={}, config={}, vram_triggered=False)
    assert isinstance(engine, FishSpeechEngine)


# ---------------------------------------------------------------------------
# standardize_speech_text tests
# ---------------------------------------------------------------------------

def test_cosyvoice_strips_all_bracket_tags():
    result = standardize_speech_text("[angry] You dare enter? (shouting)", "cosyvoice", "Neutral")
    # Should strip [angry] and (shouting), prepend "Neutral <|endofprompt|>"
    assert result == "Neutral <|endofprompt|> You dare enter?"


def test_cosyvoice_translates_asterisk_actions():
    result = standardize_speech_text("Hello *gasps* what is that?", "cosyvoice", "Surprised")
    assert "<gasps>" in result
    assert "*" not in result


def test_fish_speech_preserves_inline_tags():
    result = standardize_speech_text(
        "[angry] You dare enter? [snarl] I will crush you!",
        "fish-speech", "enraged growl"
    )
    # Inline [snarl] preserved, [angry] preserved (both are inline delivery tags)
    assert "[snarl]" in result
    assert "[enraged growl]" in result
    assert "I will crush you" in result


def test_fish_speech_strips_metadata_prefixes():
    result = standardize_speech_text("Mood: angry You dare enter?", "fish-speech", "neutral")
    assert "Mood:" not in result
    assert "You dare enter?" in result


def test_fish_speech_strips_asterisk_actions():
    result = standardize_speech_text("*roars* Get back!", "fish-speech", "angry")
    assert "*roars*" not in result
    assert "Get back!" in result


# ---------------------------------------------------------------------------
# Name detection tests
# ---------------------------------------------------------------------------

def test_named_character_detected():
    actor = ActorMetadata(actorId="abc123", name="Garrick the Rogue", lore="A cunning thief", stats={"race": "human"}, artPath="")
    assert is_named_character(actor) is True


def test_generic_guard_not_named():
    actor = ActorMetadata(actorId="abc123", name="Guard", lore="", stats={"race": "human"}, artPath="")
    assert is_named_character(actor) is False


def test_human_soldier_not_named():
    actor = ActorMetadata(actorId="abc123", name="Human Soldier", lore="", stats={"race": "human"}, artPath="")
    assert is_named_character(actor) is False


def test_skeleton_not_named():
    actor = ActorMetadata(actorId="abc123", name="Skeleton", lore="", stats={"race": "undead"}, artPath="", isMonster=True)
    assert is_named_character(actor) is False


def test_empty_name_not_named():
    actor = ActorMetadata(actorId="abc123", name="", lore="", stats={}, artPath="")
    assert is_named_character(actor) is False


# ---------------------------------------------------------------------------
# Archetype resolution tests
# ---------------------------------------------------------------------------

def test_dwarf_gets_scottish():
    actor = ActorMetadata(actorId="d1", name="Thorin", lore="A dwarf warrior", stats={"race": "dwarf"}, artPath="")
    profile = {"gender": "male", "description": "A gruff Scottish-accented dwarf"}
    assert resolve_archetype(actor, profile) == "dwarf_male_scottish"


def test_halfling_gets_irish():
    actor = ActorMetadata(actorId="h1", name="Bilbo", lore="A halfling burglar", stats={"race": "halfling"}, artPath="")
    profile = {"gender": "male", "description": "A cheerful halfling"}
    assert resolve_archetype(actor, profile) == "halfling_male_irish"


def test_barbarian_gets_german():
    actor = ActorMetadata(actorId="b1", name="Conan", lore="A fierce barbarian", stats={"race": "human"}, artPath="")
    profile = {"gender": "male", "description": "A powerful warrior"}
    assert resolve_archetype(actor, profile) == "barbarian_male_german"


def test_elder_gets_elder_archetype():
    actor = ActorMetadata(actorId="e1", name="Old Man Willow", lore="An ancient wizard", stats={"race": "human"}, artPath="")
    profile = {"gender": "male", "description": "A wizened old man"}
    assert resolve_archetype(actor, profile) == "elder_male_british"


def test_dragon_is_monster():
    actor = ActorMetadata(actorId="m1", name="Smaug", lore="A great dragon", stats={"race": "dragon"}, artPath="", isMonster=True)
    profile = {"gender": "male", "description": "An ancient dragon"}
    assert resolve_archetype(actor, profile) == "monster_dragon"


def test_skeleton_is_undead():
    actor = ActorMetadata(actorId="m2", name="Skeleton Archer", lore="A rattling skeleton", stats={"race": "undead"}, artPath="", isMonster=True)
    profile = {"gender": "male", "description": "A hollow voice"}
    assert resolve_archetype(actor, profile) == "monster_undead"


def test_goblin_is_monster():
    actor = ActorMetadata(actorId="m3", name="Goblin Scout", lore="A shrieking goblin", stats={"race": "goblin"}, artPath="", isMonster=True)
    profile = {"gender": "male", "description": "A screechy voice"}
    assert resolve_archetype(actor, profile) == "monster_goblin"


def test_demon_is_demon():
    actor = ActorMetadata(actorId="m4", name="Balrog", lore="A demon of the pit", stats={"race": "fiend"}, artPath="", isMonster=True)
    profile = {"gender": "male", "description": "A deep infernal voice"}
    assert resolve_archetype(actor, profile) == "monster_demon"


def test_default_human_male_british():
    actor = ActorMetadata(actorId="h2", name="Aldric", lore="A human paladin", stats={"race": "human"}, artPath="")
    profile = {"gender": "male", "description": "A deep, clear voice"}
    assert resolve_archetype(actor, profile) == "human_male_british"


def test_default_human_female_british():
    actor = ActorMetadata(actorId="h3", name="Elara", lore="A human cleric", stats={"race": "human"}, artPath="")
    profile = {"gender": "female", "description": "A bright, warm voice"}
    assert resolve_archetype(actor, profile) == "human_female_british"


# ---------------------------------------------------------------------------
# Voice registry tests
# ---------------------------------------------------------------------------

def test_registry_roundtrip(tmp_path, monkeypatch):
    """Test save/load/resolve cycle for the voice registry."""
    import production_brain as pb
    test_reg_path = tmp_path / "voice_registry.json"
    monkeypatch.setattr(pb, "VOICE_REGISTRY_PATH", test_reg_path)
    # Ensure clean state
    test_reg_path.write_text("{}")

    register_character_voice("test_actor", "cosyvoice", "test_actor_seed_male.wav", voice_prompt="A test voice")
    registry = load_voice_registry()
    assert "test_actor" in registry
    assert registry["test_actor"]["engine"] == "cosyvoice"
    assert registry["test_actor"]["is_archetype"] is False


def test_resolve_seed_path_falls_back_to_glob(tmp_path, monkeypatch):
    """resolve_seed_path falls back to filesystem glob when not in registry."""
    import production_brain as pb
    test_reg_path = tmp_path / "voice_registry.json"
    test_seeds_dir = tmp_path / "voice_seeds"
    test_seeds_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(pb, "VOICE_REGISTRY_PATH", test_reg_path)
    monkeypatch.setattr(pb, "VOICE_SEEDS_DIR", test_seeds_dir)
    test_reg_path.write_text("{}")

    # Create a seed file on disk
    (test_seeds_dir / "abc123_seed_male.wav").write_text("fake wav data")

    result = resolve_seed_path("abc123")
    assert result.endswith("abc123_seed_male.wav")


def test_is_named_character_edge_cases():
    # Narrator is not named
    actor = ActorMetadata(actorId="n1", name="Narrator", lore="", stats={}, artPath="")
    assert is_named_character(actor) is False

    # Unknown is not named
    actor = ActorMetadata(actorId="x1", name="Unknown", lore="", stats={}, artPath="")
    assert is_named_character(actor) is False

    # Multi-word proper name is named
    actor = ActorMetadata(actorId="p1", name="Sir Aldric the Bold", lore="A knight", stats={"race": "human"}, artPath="")
    assert is_named_character(actor) is True