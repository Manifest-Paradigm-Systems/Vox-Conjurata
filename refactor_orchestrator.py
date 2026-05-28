import re
import os

file_path = "services/orchestrator/production_brain.py"

with open(file_path, "r") as f:
    content = f.read()

# 1. Update SpeechPipelineFactory to include transcript in generate signature if needed
# Actually, the user didn't ask for it, but CosyVoice likes it.
# For now, I'll stick to the user's explicit instructions.

# 2. Update clean_tts_text to be a standardizer instead of a stripper
old_clean_func = """def clean_tts_text(text: str) -> str:
    \"\"\"Strips metadata tags, bracketed instructions, and artifacts from LLM-generated dialogue.\"\"\"
    import re
    # Remove bracketed tags like [Neutral], [enraged growl]
    text = re.sub(r'\[.*?\]', '', text)
    # Remove parenthetical tags like (neutral), (Whispering)
    text = re.sub(r'\(.*?\)', '', text)
    # Remove common metadata prefixes
    text = re.sub(r'^(Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction):\s*', '', text, flags=re.IGNORECASE)
    # Remove leading "neutral:" or similar followed by space
    text = re.sub(r'^\w+:\s+', '', text)
    # Final cleanup of whitespace
    return text.strip()"""

new_clean_func = """def standardize_speech_text(text: str, engine_type: str, emotion: str) -> str:
    \"\"\"Maps and formats emotional tags and sound effects to engine-specific syntax.\"\"\"
    import re
    
    # 1. Strip EXISTING tags to avoid double-processing and standardization
    # This removes [neutral], (happy), "Mood: sad", etc.
    clean_text = re.sub(r'\[.*?\]|\(.*?\)|\w+:\s*', '', text).strip()
    
    # 2. Sound Effect Parser (*gasp* -> <gasp> for CosyVoice)
    if engine_type == "cosyvoice":
        # Translate *action* into <action>
        clean_text = re.sub(r'\*(.*?)\*', r'<\1>', clean_text)
    else:
        # Strip SFX for engines that don't support acoustic generation
        clean_text = re.sub(r'\*.*?\*', '', clean_text)

    # 3. Engine-Specific Syntax Mapping
    if engine_type == "fish-speech":
        # Monster: strict square brackets [emotion] at start
        return f"[{emotion.lower()}] {clean_text}"
    elif engine_type == "cosyvoice":
        # Humanoid: inline parentheses (emotion) at start
        return f"({emotion.lower()}) {clean_text}"
    else:
        # Fallback/Edge-TTS: Just the clean text
        return clean_text"""

content = content.replace(old_clean_func, new_clean_func)

# 3. Update enrich_and_instruct to use the new standardizer
old_enrich_logic = """            emotion = res.get("emotion_tag", "Neutral").strip()
            
            # Clean the actual spoken text of any metadata tags or artifacts
            clean_text = clean_tts_text(text)
            
            # Formatting for Fish Speech (Monster): lowercase inline square brackets
            monster_text = f"[{emotion.lower()}] {clean_text}"
            
            # Formatting for CosyVoice (Humanoid): Title Case <|endofprompt|> Dialogue text
            instruct_text = f"{emotion.capitalize()} <|endofprompt|> {clean_text}"
            
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=res.get("emotional_resonance", emotion),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text
            )"""

new_enrich_logic = """            emotion = res.get("emotion_tag", "Neutral").strip()
            
            # Apply engine-specific syntax mapping and SFX parsing
            monster_text = standardize_speech_text(text, "fish-speech", emotion)
            instruct_text = standardize_speech_text(text, "cosyvoice", emotion)
            
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=res.get("emotional_resonance", emotion),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text
            )"""

content = content.replace(old_enrich_logic, new_enrich_logic)

# 4. Update the fallback logic to use standardize_speech_text with empty engine type
content = content.replace("clean_tts_text(transcription)", 'standardize_speech_text(transcription, "edge-tts", "neutral")')

with open(file_path, "w") as f:
    f.write(content)

print("Orchestrator refactored successfully.")
