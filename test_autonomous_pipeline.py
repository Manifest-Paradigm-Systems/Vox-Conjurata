#!/usr/bin/env python3
import time
import json
import requests
import base64
from pathlib import Path

def test_autonomous_npc_pipeline():
    print("🎙️ Vox Conjurata - NPC Autonomous Pipeline Test")
    print("=" * 60)

    # Use the existing test_angry.wav which has actual speech (not silence)
    audio_path = Path("test_angry.wav")
    if not audio_path.exists():
        print(f"❌ Error: {audio_path} not found. Please run this in the repository root directory.")
        return

    print(f"📁 Using input audio: {audio_path} ({audio_path.stat().st_size:,} bytes)")

    with open(audio_path, "rb") as f:
        audio_content = f.read()

    # Metadata simulating a Player (Ezren) speaking and targeting the Xulgath Warrior
    metadata = {
        "activeSpeakerName": "Ezren",
        "actorId": "WNX5OQKPh4uaV7mW",
        "micType": "vox-conjurata-player-mic",
        "isMonster": False,
        "stats": {
            "race": "PC",
            "level": 1
        },
        "dsp_presets": {
            "pitch_shift": 0,
            "distortion_db": 0,
            "highpass_hz": 0,
            "chorus_depth": 0,
            "reverb_size": 0
        },
        "useVoxVoice": True,
        "useVoxActor": True,
        "userId": "gm",  # Charge GM's funded wallet
        "isAutonomousTrigger": True,
        "targetActorId": "5vBG8a8dnJfmVd3Y", # Xulgath Warrior
        "targetVoxVoice": True,
        "npc_context": {
            "name": "Xulgath Warrior",
            "lore": "A guttural lizardfolk warrior of the dark caverns. Hostile but talks in raspy growls.",
            "is_monster": True,
            "memory": "Ezren previously cast a spell that scared them.",
            "world_lore": "Xulgaths hate the light and seek to capture surface dwellers.",
            "local_lore": "Location: Dark Cave depth"
        }
    }

    url = "http://localhost:8080/api/voice-conversion"
    files = {
        "audio_blob": ("test_angry.wav", audio_content, "audio/wav")
    }
    data = {
        "metadata": json.dumps(metadata)
    }

    print("\n📡 Sending voice prompt to VTT Orchestrator...")
    print(f"👉 Speaker: {metadata['activeSpeakerName']}")
    print(f"👉 Target NPC: {metadata['npc_context']['name']}")
    print("⏱️  Measuring full pipeline roundtrip latency...")

    start_time = time.time()
    try:
        response = requests.post(url, files=files, data=data, timeout=120.0)
        end_time = time.time()
        elapsed = end_time - start_time

        print(f"\n⏱️  Total Latency: {elapsed:.2f} seconds")
        print(f"📡 HTTP Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            status = result.get("status")
            print(f"📈 Pipeline Status: {status}")

            if status == "success":
                transcription = result.get("transcription", "")
                print(f"\n🗣️  Speaker Transcription: '{transcription}'")
                
                # Check Speaker converted voice
                audio_data = result.get("audio_data")
                if audio_data:
                    print(f"🎵 Speaker Audio Generated: {len(audio_data):,} chars (base64)")
                else:
                    print("🎵 Speaker Audio: None")

                # Check Autonomous NPC Reply
                ai_reply = result.get("ai_reply")
                if ai_reply:
                    print("\n🤖 Autonomous NPC Reply received:")
                    print(f"   • Text: '{ai_reply.get('transcription')}'")
                    ai_audio = ai_reply.get("audio_data")
                    if ai_audio:
                        print(f"   • Audio: Generated ({len(ai_audio):,} chars base64)")
                        # Save the audio
                        try:
                            # Strip the header if it exists
                            if ai_audio.startswith("data:audio"):
                                ai_audio_bytes = base64.b64decode(ai_audio.split(",")[1])
                            else:
                                ai_audio_bytes = base64.b64decode(ai_audio)
                            
                            output_path = Path("test_audio/xulgath_reply.wav")
                            output_path.parent.mkdir(exist_ok=True)
                            with open(output_path, "wb") as out_f:
                                out_f.write(ai_audio_bytes)
                            print(f"   💾 Saved NPC audio response to: {output_path}")
                        except Exception as e:
                            print(f"   ⚠️ Could not save NPC audio: {e}")
                    else:
                        print("   • Audio: None")
                    print(f"   • Image Prompt: '{ai_reply.get('image_prompt')}'")
                else:
                    print("\n❌ No Autonomous NPC Reply generated in the response.")

            else:
                print(f"❌ Pipeline failed: {result.get('error', 'Unknown error')}")
        else:
            print(f"❌ Server returned error: {response.text}")

    except Exception as e:
        print(f"❌ Request failed with exception: {e}")

if __name__ == "__main__":
    test_autonomous_npc_pipeline()
