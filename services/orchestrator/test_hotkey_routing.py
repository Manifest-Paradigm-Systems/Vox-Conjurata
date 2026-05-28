import httpx
import json
import os
import sys
from pathlib import Path

def run_routing_test():
    url = "http://localhost:8080/api/voice-conversion"
    
    # Locate a voice seed wav file to use as the input audio blob
    seeds_dir = Path("services/orchestrator/voice_seeds")
    wav_files = list(seeds_dir.glob("*.wav"))
    if not wav_files:
        print("❌ Error: No .wav voice seeds found.")
        sys.exit(1)
    sample_audio = wav_files[0]
    
    with open(sample_audio, "rb") as f:
        audio_content = f.read()

    # --- 1. DM Narrative Hotkey Test ('white key' / GM Narrate) ---
    print("\n--- Testing DM Narrative Hotkey Routing ---")
    meta_dm = {
        "activeSpeakerName": "Narrator",
        "actorId": "narrator",
        "micType": "vox-conjurata-gm-narrate-mic",
        "isMonster": False,
        "stats": {}
    }
    
    resp = httpx.post(
        url,
        files={"audio_blob": (sample_audio.name, audio_content, "audio/wav")},
        data={"metadata": json.dumps(meta_dm)},
        timeout=30.0
    )
    assert resp.status_code == 200
    res_json = resp.json()
    print(f"Status: {res_json['status']}")
    print(f"voxType resolved: {res_json.get('voxType')}")
    print(f"Engine selected: {res_json.get('engine')}")
    # GM narrate routes as narration and uses Edge-TTS (Narrator default)
    assert res_json.get("voxType") == "narration"
    assert "Edge-TTS" in res_json.get("engine")
    print("✅ DM Narrative Hotkey test passed!")

    # --- 2. 'H' Key Test (Monster / Hostile Entity) ---
    print("\n--- Testing H Key Monster Routing ---")
    meta_monster = {
        "activeSpeakerName": "Skeleton Guard",
        "actorId": "trchDxbDR2TiPMxT",
        "micType": "vox-conjurata-gm-puppet-mic",
        "isMonster": True,
        "stats": {"race": "undead", "level": 1}
    }
    
    resp = httpx.post(
        url,
        files={"audio_blob": (sample_audio.name, audio_content, "audio/wav")},
        data={"metadata": json.dumps(meta_monster)},
        timeout=30.0
    )
    assert resp.status_code == 200
    res_json = resp.json()
    print(f"Status: {res_json['status']}")
    print(f"voxType resolved: {res_json.get('voxType')}")
    print(f"Engine selected: {res_json.get('engine')}")
    # Monster routes to Fish Speech
    assert res_json.get("voxType") == "puppet"
    assert res_json.get("engine") in ["Fish Speech", "Edge-TTS (Fallback)"]
    print("✅ H Key Monster test passed!")

    # --- 3. 'I' Key Test (Character Token Voice) ---
    print("\n--- Testing I Key Character Routing ---")
    meta_char = {
        "activeSpeakerName": "Valeros",
        "actorId": "vcwqnXHkhzFhrt7O",
        "micType": "vox-conjurata-player-mic",
        "isMonster": False,
        "stats": {"race": "human", "level": 3}
    }
    
    resp = httpx.post(
        url,
        files={"audio_blob": (sample_audio.name, audio_content, "audio/wav")},
        data={"metadata": json.dumps(meta_char)},
        timeout=30.0
    )
    assert resp.status_code == 200
    res_json = resp.json()
    print(f"Status: {res_json['status']}")
    print(f"voxType resolved: {res_json.get('voxType')}")
    print(f"Engine selected: {res_json.get('engine')}")
    # Player character routes to CosyVoice
    assert res_json.get("voxType") == "player"
    assert res_json.get("engine") in ["CosyVoice", "Edge-TTS (Fallback)"]
    print("✅ I Key Character test passed!")

    print("\n🎉 ALL ROUTING TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_routing_test()
