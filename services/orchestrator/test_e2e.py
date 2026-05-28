import httpx
import json
import os
import sys
from pathlib import Path

def run_e2e_test():
    url = "http://localhost:8080/api/voice-conversion"
    
    # Dynamically find a seed audio file in voice_seeds directory
    seeds_dir = Path("services/orchestrator/voice_seeds")
    wav_files = list(seeds_dir.glob("*.wav"))
    if not wav_files:
        print("❌ Error: No .wav voice seeds found in services/orchestrator/voice_seeds")
        sys.exit(1)
        
    target_audio = wav_files[0]
    print(f"Using test audio file: {target_audio}")
    
    with open(target_audio, "rb") as f:
        audio_content = f.read()
        
    metadata = {
        "activeSpeakerName": "Garrick the Rogue",
        "actorId": "garrick_rogue",
        "micType": "vox-conjurata-gm-puppet-mic",
        "isMonster": False,
        "stats": {
            "race": "human",
            "level": 3
        }
    }
    
    files = {
        "audio_blob": (target_audio.name, audio_content, "audio/wav")
    }
    data = {
        "metadata": json.dumps(metadata)
    }
    
    print("Sending POST request to /api/voice-conversion...")
    try:
        # Increase timeout to 90 seconds to allow for cold-start TTS/STT generation
        resp = httpx.post(url, files=files, data=data, timeout=90.0)
        print(f"Status Code: {resp.status_code}")
        if resp.status_code != 200:
            print(f"❌ Error Output: {resp.text}")
            sys.exit(1)
            
        res_json = resp.json()
        print("Response JSON structure:")
        for k, v in res_json.items():
            if k == "audio_data":
                val_repr = f"{v[:50]}... ({len(v)} chars)" if v else "None"
                print(f"  {k}: {val_repr}")
            else:
                print(f"  {k}: {v}")
                
        # Validate critical response fields
        if res_json.get("status") not in ["success", "empty"]:
            print("❌ Failure: Response status is not success or empty.")
            sys.exit(1)
            
        print("✅ E2E integration flow completed successfully!")
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_e2e_test()
