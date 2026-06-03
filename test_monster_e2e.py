import httpx
import json
import os
import sys
from pathlib import Path

def run_e2e_test(is_monster=False):
    url = "http://localhost:8080/api/voice-conversion"
    actor_id = "ancient_dragon" if is_monster else "garrick_rogue"
    name = "Ancient Red Dragon" if is_monster else "Garrick the Rogue"
    
    # Use a dummy audio but with a descriptive name to prompt the STT (not really, but good for logs)
    target_audio = Path("services/orchestrator/voice_seeds/narrator_seed_male.wav")
    
    # Actually, STT will just transcribe the narrator_seed_male.wav which is 
    # "A clear speaking voice for narrator."
    # To test the LLM enrichment, I'd need to mock the STT or use a different audio.
    # But since STT is working, I'll just check if the logs show the LLM 
    # generating tags and the orchestrator stripping them.
    
    with open(target_audio, "rb") as f:
        audio_content = f.read()
        
    metadata = {
        "activeSpeakerName": name,
        "actorId": actor_id,
        "micType": "vox-conjurata-gm-puppet-mic",
        "isMonster": is_monster,
        "stats": {
            "race": "dragon" if is_monster else "half-elf",
            "gender": "male"
        }
    }
    
    files = {
        "audio_blob": (target_audio.name, audio_content, "audio/wav")
    }
    data = {
        "metadata": json.dumps(metadata)
    }
    
    print(f"Sending POST request to /api/voice-conversion (Monster={is_monster})...")
    try:
        resp = httpx.post(url, files=files, data=data, timeout=90.0)
        print(f"Status Code: {resp.status_code}")
        if resp.status_code != 200:
            print(f"❌ Error Output: {resp.text}")
            return
            
        res_json = resp.json()
        print(f"Transcription: {res_json.get('transcription')}")
        enrichment = res_json.get("enrichment", {})
        print(f"Monster Text: {enrichment.get('monster_text')}")
        print(f"Instruct Text: {enrichment.get('instruct_text')}")
        
        if res_json.get("audio_data"):
             print(f"✅ Audio generated ({len(res_json['audio_data'])} chars)")
        else:
             print("❌ No audio generated!")

    except Exception as e:
        print(f"❌ Exception occurred: {e}")

if __name__ == "__main__":
    print("--- Humanoid Test ---")
    run_e2e_test(is_monster=False)
    print("\n--- Monster Test ---")
    run_e2e_test(is_monster=True)
