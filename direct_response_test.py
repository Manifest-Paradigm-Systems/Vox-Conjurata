#!/usr/bin/env python3
"""
Direct NPC Response Time Test
Simple test to measure how long it takes for NPCs to respond to voice prompts
"""

import time
import json
import requests
import sys
from pathlib import Path

def create_test_audio():
    """Create a minimal test audio file for testing"""
    test_dir = Path("test_audio")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test_input.wav"

    # Create a simple 1-second WAV file with silence
    import wave
    import struct

    with wave.open(str(test_file), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)

        # 16000 samples = 1 second of silence (0 values)
        for i in range(16000):
            wf.writeframes(struct.pack('<h', 0))

    print(f"Created test audio: {test_file}")
    return str(test_file)

def test_npc_response_time():
    """Test how long it takes for NPC to respond"""

    print("🎙️ Vox Conjurata - NPC Response Time Test")
    print("=" * 60)

    # Test scenarios from your actual character configuration
    test_scenarios = [
        {
            "name": "Valeros",
            "actor_id": "vcwqnXHkhzFhrt7O",
            "profile": "A confident, robust, and slightly raspy warrior voice, medium pitch, direct delivery.",
            "type": "PC"
        },
        {
            "name": "Merisiel",
            "actor_id": "czQ0MaZBu3BqMpce",
            "profile": "A sharp, quick-tongued, agile female voice, slightly breathy, confident and expressive.",
            "type": "PC"
        },
        {
            "name": "Ezren",
            "actor_id": "WNX5OQKPh4uaV7mW",
            "profile": "A mature, educated, and resonant elderly male voice, clear articulation, slow and calculated pacing.",
            "type": "PC"
        },
        {
            "name": "Xulgath Warrior",
            "actor_id": "5vBG8a8dnJfmVd3Y",
            "profile": "A guttural, growling, and hissed accent, low pitch with animalistic sibilants.",
            "type": "NPC"
        }
    ]

    results = []
    total_runs = len(test_scenarios)
    current_run = 0

    for scenario in test_scenarios:
        current_run += 1
        print(f"\n📊 Test {current_run}/{total_runs}: {scenario['name']}")
        print(f"   Actor ID: {scenario['actor_id']}")
        print(f"   Profile: {scenario['profile'][:60]}...")
        print(f"   Type: {scenario['type']}")
        print("   ⏱️  Sending voice request...")

        try:
            # Create test audio if needed
            test_file = create_test_audio()

            with open(test_file, "rb") as f:
                audio_content = f.read()

            # Prepare the request
            metadata = {
                "activeSpeakerName": scenario["name"],
                "actorId": scenario["actor_id"],
                "micType": "vox-conjurata-gm-puppet-mic",
                "isMonster": scenario["type"] == "NPC",
                "stats": {
                    "race": scenario["type"],
                    "level": 1
                },
                "dsp_presets": {
                    "pitch_shift": 0,
                    "distortion_db": 0,
                    "highpass_hz": 0,
                    "chorus_depth": 0,
                    "reverb_size": 0
                },
                "useVoxActor": True,
                "userId": "test_user"
            }

            # Send request to orchestrator
            url = "http://localhost:8080/api/voice-conversion"

            files = {
                "audio_blob": ("test_input.wav", audio_content, "audio/wav")
            }

            data = {
                "metadata": json.dumps(metadata)
            }

            # Measure response time
            start_time = time.time()

            print("   📡 Sending to orchestrator...")
            response = requests.post(url, files=files, data=data, timeout=60.0)

            end_time = time.time()
            response_time = end_time - start_time

            if response.status_code == 200:
                result = response.json()
                success = result.get("status") in ["success", "empty"]

                if success:
                    print(f"   ✅ SUCCESS - Response: {response_time:.2f}s")
                    print(f"   📝 Transcription: {result.get('transcription', 'N/A')[:50]}...")
                    print(f"   🎵 Audio generated: {len(result.get('audio_data', '')):,} chars")
                else:
                    print(f"   ❌ FAILED - Response: {response_time:.2f}s")
                    print(f"   Error: {result.get('error', 'Unknown')}")

                results.append({
                    "name": scenario["name"],
                    "actor_id": scenario["actor_id"],
                    "success": success,
                    "response_time": response_time,
                    "status": result.get("status", "unknown"),
                    "transcription": result.get("transcription", ""),
                    "error": result.get("error", "")
                })

            else:
                print(f"   ❌ HTTP ERROR - Response: {response_time:.2f}s")
                print(f"   Status Code: {response.status_code}")
                print(f"   Error: {response.text}")

                results.append({
                    "name": scenario["name"],
                    "actor_id": scenario["actor_id"],
                    "success": False,
                    "response_time": response_time,
                    "status": f"HTTP_{response.status_code}",
                    "error": response.text
                })

        except requests.exceptions.ConnectError:
            print(f"   🚫 CONNECTION ERROR")
            print(f"   ❌ Cannot connect to orchestrator at {url}")
            print(f"   💡 Make sure Vox Conjurata services are running:")
            print(f"      • orchestrator (localhost:8080)")
            print(f"      • vox-audio-core (localhost:8000)")

            results.append({
                "name": scenario["name"],
                "actor_id": scenario["actor_id"],
                "success": False,
                "response_time": 0,
                "status": "connection_error",
                "error": f"Cannot connect to {url}"
            })

        except Exception as e:
            print(f"   🚫 EXCEPTION - Response: {response_time:.2f}s")
            print(f"   Error: {str(e)}")

            results.append({
                "name": scenario["name"],
                "actor_id": scenario["actor_id"],
                "success": False,
                "response_time": time.time() - start_time,
                "status": "exception",
                "error": str(e)
            })

        # Wait between tests
        if current_run < total_runs:
            print(f"   ⏳ Waiting 10 seconds before next test...")
            time.sleep(10)

    # Analyze and report results
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print("\n" + "=" * 60)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 60)
    print(f"✅ Successful Tests: {len(successful)}")
    print(f"❌ Failed Tests: {len(failed)}")
    print(f"📈 Success Rate: {len(successful)/len(results)*100:.1f}%")

    if successful:
        response_times = [r["response_time"] for r in successful]
        print(f"\n⏱️  Response Times:")
        print(f"   • Fastest: {min(response_times):.2f}s")
        print(f"   • Slowest: {max(response_times):.2f}s")
        print(f"   • Average: {sum(response_times)/len(response_times):.2f}s")
        print(f"   • 95th percentile: {sorted(response_times)[int(len(response_times)*0.95)]:.2f}s")

        print(f"\n📋 Per-Character Results:")
        for result in successful:
            print(f"   • {result['name']}: {result['response_time']:.2f}s")

    if failed:
        print(f"\n❌ Failed Tests Details:")
        for result in failed:
            print(f"   • {result['name']}: {result['status']} - {result['error']}")

    # Performance recommendations
    print(f"\n🎯 PERFORMANCE ANALYSIS & RECOMMENDATIONS")
    print("=" * 60)

    if successful:
        avg_response = sum(r["response_time"] for r in successful) / len(successful)
        max_response = max(r["response_time"] for r in successful)

        if avg_response > 10:
            print(f"⚠️  AVERAGE NPC RESPONSE ({avg_response:.1f}s) - OPTIMIZATION NEEDED")
            print("   • Consider increasing VRAM allocation")
            print("   • Check audio model caching efficiency")
            print("   • Monitor orchestration pipeline bottlenecks")

        if max_response > 20:
            print(f"🚨 SLOWEST NPC RESPONSE ({max_response:.1f}s) - SYSTEM BOTTLENECK")
            print("   • Most NPCs should respond under 10 seconds")
            print("   • Investigate audio generation pipeline")
            print("   • Check network latency between services")

        if avg_response < 5:
            print(f"✅ PERFORMANCE GOOD - Average response ({avg_response:.1f}s)")
            print("   • System is responding quickly")
            print("   • Consider increasing concurrency for scale")

    return results

if __name__ == "__main__":
    print("🚀 Starting Direct NPC Response Time Test")
    print("This test measures how long it takes for NPCs to respond to voice prompts")
    print("Make sure Vox Conjurata services are running before starting!")
    print("")

    try:
        results = test_npc_response_time()

        # Save results to file
        results_file = Path("npc_response_test_results.json")
        with open(results_file, 'w') as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "test_type": "direct_npc_response_timing",
                "results": results,
                "summary": {
                    "total_tests": len(results),
                    "successful": len([r for r in results if r["success"]]),
                    "failed": len([r for r in results if not r["success"]]),
                    "success_rate": len([r for r in results if r["success"]]) / len(results) * 100
                }
            }, f, indent=2)

        print(f"\n📄 Results saved to: {results_file}")
        print("   You can analyze the detailed results for optimization insights.")

        # Exit with appropriate code
        success_rate = len([r for r in results if r["success"]]) / len(results) * 100
        if success_rate >= 80:
            print(f"\n🎉 OVERALL SUCCESS - {success_rate:.1f}% success rate")
            sys.exit(0)
        else:
            print(f"\n⚠️  LOW SUCCESS RATE - {success_rate:.1f}%")
            print("   Immediate intervention required")
            sys.exit(1)

    except KeyboardInterrupt:
        print(f"\n⏹️  Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        sys.exit(1)