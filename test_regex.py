import httpx
import json
import base64
from pathlib import Path

def test_narrator_instructions():
    url = "http://localhost:8080/api/voice-conversion"
    
    # Mock metadata with instructions in the transcription (text field)
    # This simulates the user speaking an instruction or the STT mis-transcribing.
    # Also includes standard metadata patterns.
    transcription = "Instruction: Speak in a deep voice. Mood: Neutral. Once upon a time in a land far away."
    
    # Use a real audio seed for the narrator
    seed_path = Path("vox-conjurata/services/orchestrator/voice_seeds/narrator_seed_male.wav")
    with open(seed_path, "rb") as f:
        audio_content = f.read()

    metadata = {
        "activeSpeakerName": "Narrator",
        "actorId": "narrator",
        "micType": "vox-conjurata-gm-narrate-mic",
        "isMonster": False,
        "stats": {}
    }
    
    files = {"audio_blob": ("v.wav", audio_content, "audio/wav")}
    data = {"metadata": json.dumps(metadata)}
    
    # We need to bypass the actual STT to test the regex
    # But /api/voice-conversion always runs STT.
    # So I'll test the standardize_speech_text function directly by mocking the 
    # orchestrator's behavior or just checking the logs.
    
    print(f"Sending request with text: '{transcription}'")
    # Actually, I'll just check if the standardization logic works by calling a 
    # temporary test script on the host that imports the function.
    
if __name__ == "__main__":
    # Instead of a full E2E, let's just test the regex directly on the host
    import re
    
    def standardize_speech_text_mock(text):
        # The exact regex from production_brain.py
        clean_text = re.sub(r'(?i)(?:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice):\s*.*?(?:[.!?]\s*|\n|$)',
                           '', text)
        clean_text = re.sub(r'\[.*?\]', '', clean_text)
        clean_text = re.sub(r'\(.*?\)', '', clean_text)
        clean_text = re.sub(r'\*.*?\*', '', clean_text)
        clean_text = re.sub(r'^\W+', '', clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        return clean_text

    test_cases = [
        "Instruction: Speak like a king. Hello world.",
        "Mood: Enraged! [growl] I will crush you.",
        "Delivery: Whisper. (softly) Can you hear me?",
        "Note: very important. This is the text.",
        "Instruction: speak like a cat, meow. Hello.", # Comma case
    ]
    
    for t in test_cases:
        result = standardize_speech_text_mock(t)
        print(f"Input:  {t}")
        print(f"Output: {result}")
        print("-" * 20)
