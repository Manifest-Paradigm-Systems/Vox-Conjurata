import asyncio
import time
import json
import httpx
from pathlib import Path
from typing import Dict, List, Any
import statistics
import sys

class NPCResponseTimeTester:
    def __init__(self):
        self.base_url = "http://localhost:8080"
        self.test_dir = Path("test_audio_seeds")
        self.test_dir.mkdir(exist_ok=True)

    async def generate_test_audio(self) -> str:
        """Create a simple test audio file if needed"""
        test_file = self.test_dir / "test_npc_response.wav"

        # If no test audio exists, create a minimal WAV
        if not test_file.exists():
            import wave
            import struct
            with wave.open(str(test_file), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                # Generate some simple audio data
                for i in range(int(16000 * 0.5)):  # 0.5 seconds of audio
                    # Generate a simple tone
                    sample = int(1000 * 0.5 * (i % 100) / 100)
                    wf.writeframes(struct.pack('<h', sample))

            print(f"Created test audio file: {test_file}")

        return str(test_file)

    async def test_single_npc_response(self, actor_id: str, voice_profile: Dict, response_text: str = "Hello, how are you today?") -> Dict:
        """Test a single NPC response and measure timing"""
        test_file = await self.generate_test_audio()

        with open(test_file, "rb") as f:
            audio_content = f.read()

        metadata = {
            "activeSpeakerName": voice_profile.get("name", "Test NPC"),
            "actorId": actor_id,
            "micType": "vox-conjurata-player-mic",
            "isMonster": voice_profile.get("type") == "NPC",
            "stats": {
                "race": voice_profile.get("class", "human"),
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

        # Prepare multipart form data
        files = {
            "audio_blob": (test_file.name, audio_content, "audio/wav")
        }
        data = {
            "metadata": json.dumps(metadata)
        }

        url = f"{self.base_url}/api/voice-conversion"

        # Measure response time
        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, files=files, data=data)

            end_time = time.time()
            response_time = end_time - start_time

            if resp.status_code == 200:
                result = resp.json()
                return {
                    "success": True,
                    "actor_id": actor_id,
                    "response_time_seconds": response_time,
                    "response_status": result.get("status", "unknown"),
                    "transcription": result.get("transcription", ""),
                    "audio_data_size": len(result.get("audio_data", "")) if result.get("audio_data") else 0,
                    "engine": result.get("engine", "unknown")
                }
            else:
                return {
                    "success": False,
                    "actor_id": actor_id,
                    "response_time_seconds": response_time,
                    "error": resp.text,
                    "status_code": resp.status_code
                }

        except Exception as e:
            end_time = time.time()
            response_time = end_time - start_time
            return {
                "success": False,
                "actor_id": actor_id,
                "response_time_seconds": response_time,
                "error": str(e)
            }

    async def run_performance_tests(self):
        """Run performance tests on different NPC types"""
        print("🧪 Starting NPC Response Time Performance Tests")
        print("=" * 60)

        # Test scenarios - different NPC types and conditions
        test_scenarios = [
            # (actor_id, voice_profile, description)
            ("valeros_fighter", {
                "name": "Valeros",
                "type": "PC",
                "class": "Fighter",
                "profile": "Confident warrior voice"
            }, "Player Character - Fighter"),

            ("merisiel_rogue", {
                "name": "Merisiel",
                "type": "PC",
                "class": "Rogue",
                "profile": "Quick-tongued rogue voice"
            }, "Player Character - Rogue"),

            ("xulgath_warrior", {
                "name": "Xulgath Warrior",
                "type": "NPC",
                "class": "Beast",
                "profile": "Guttural monster voice"
            }, "Monster NPC - Beast"),

            ("generic_guard", {
                "name": "Generic Guard",
                "type": "NPC",
                "class": "Human",
                "profile": "Generic soldier voice"
            }, "Generic Monster NPC"),
        ]

        results = []

        for actor_id, voice_profile, description in test_scenarios:
            print(f"\n📊 Testing: {description}")
            print(f"   Actor ID: {actor_id}")
            print(f"   Profile: {voice_profile.get('profile')}")

            # Run the test
            result = await self.test_single_npc_response(actor_id, voice_profile)

            if result.get("success"):
                status = result.get("response_status", "unknown")
                resp_time = result.get("response_time_seconds", 0)
                audio_size = result.get("audio_data_size", 0)

                print(f"   ✅ SUCCESS")
                print(f"   📈 Response Time: {resp_time:.2f} seconds")
                print(f"   🎵 Audio Data: {audio_size:,} bytes")
                print(f"   🔧 Engine: {result.get('engine', 'unknown')}")

                results.append(result)

                # Wait between tests to avoid overwhelming the system
                await asyncio.sleep(5)

            else:
                print(f"   ❌ FAILED")
                error = result.get("error", "Unknown error")
                print(f"   Error: {error}")
                results.append(result)

        return results

    def analyze_results(self, results: List[Dict]) -> Dict:
        """Analyze performance test results"""
        successful_results = [r for r in results if r.get("success")]
        failed_results = [r for r in results if not r.get("success")]

        if not successful_results:
            return {
                "status": "failed",
                "message": "No successful tests to analyze"
            }

        # Calculate statistics
        response_times = [r.get("response_time_seconds", 0) for r in successful_results]
        audio_sizes = [r.get("audio_data_size", 0) for r in successful_results]
        engines = [r.get("engine", "unknown") for r in successful_results]

        stats = {
            "total_tests": len(results),
            "successful_tests": len(successful_results),
            "failed_tests": len(failed_results),
            "success_rate": len(successful_results) / len(results) * 100,

            "response_time_stats": {
                "min": min(response_times),
                "max": max(response_times),
                "mean": statistics.mean(response_times),
                "median": statistics.median(response_times),
                "stdev": statistics.stdev(response_times) if len(response_times) > 1 else 0,
                "p95": statistics.quantiles(response_times, n=20)[-1] if len(response_times) >= 20 else response_times[-1]
            },

            "audio_size_stats": {
                "min": min(audio_sizes),
                "max": max(audio_sizes),
                "mean": statistics.mean(audio_sizes)
            },

            "engine_distribution": {engine: engines.count(engine) for engine in set(engines)},

            "detailed_results": successful_results
        }

        return stats

    def generate_report(self, stats: Dict) -> str:
        """Generate a human-readable performance report"""
        report = []
        report.append("=" * 80)
        report.append("🎙️ VOX CONJURATA - NPC RESPONSE TIME PERFORMANCE REPORT")
        report.append("=" * 80)
        report.append(f"📊 Test Summary:")
        report.append(f"   • Total Tests: {stats['total_tests']}")
        report.append(f"   • Successful Tests: {stats['successful_tests']}")
        report.append(f"   • Failed Tests: {stats['failed_tests']}")
        report.append(f"   • Success Rate: {stats['success_rate']:.1f}%")
        report.append("")

        if stats['successful_tests'] > 0:
            rt = stats['response_time_stats']
            report.append(f"⏱️  Response Time Performance:")
            report.append(f"   • Fastest Response: {rt['min']:.2f}s")
            report.append(f"   • Slowest Response: {rt['max']:.2f}s")
            report.append(f"   • Average Response: {rt['mean']:.2f}s")
            report.append(f"   • Median Response: {rt['median']:.2f}s")
            report.append(f"   • 95th Percentile: {rt['p95']:.2f}s")
            report.append(f"   • Standard Deviation: {rt['stdev']:.2f}s")
            report.append("")

            audio = stats['audio_size_stats']
            report.append(f"🎵 Audio Data Generation:")
            report.append(f"   • Min Size: {audio['min']:,} bytes")
            report.append(f"   • Max Size: {audio['max']:,} bytes")
            report.append(f"   • Average Size: {audio['mean']:.0f:,} bytes")
            report.append("")

            if stats['engine_distribution']:
                report.append(f"🔧 Engine Usage:")
                for engine, count in stats['engine_distribution'].items():
                    report.append(f"   • {engine}: {count} tests")
                report.append("")

        report.append("=" * 80)
        return "\n".join(report)

    async def run_complete_test_suite(self):
        """Run the complete performance test suite"""
        print("🚀 Starting Complete NPC Response Time Test Suite")
        print("This will measure how quickly NPCs respond to voice prompts.")
        print("")

        # Run tests
        results = await self.run_performance_tests()

        # Analyze results
        stats = self.analyze_results(results)

        # Generate and display report
        report = self.generate_report(stats)
        print(report)

        # Save results to file
        results_file = Path("npc_response_test_results.json")
        with open(results_file, 'w') as f:
            json.dump(stats, f, indent=2, default=str)

        print(f"📄 Detailed results saved to: {results_file}")

        return stats

async def main():
    """Main test runner"""
    tester = NPCResponseTimeTester()

    try:
        stats = await tester.run_complete_test_suite()

        # Provide optimization recommendations
        print("\n🎯 PERFORMANCE OPTIMIZATION RECOMMENDATIONS:")
        print("=" * 80)

        if stats['successful_tests'] > 0:
            avg_time = stats['response_time_stats']['mean']
            max_time = stats['response_time_stats']['max']

            print("📋 Key Insights:")
            if avg_time > 10:
                print(f"   ⚠️  Average response time ({avg_time:.1f}s) is slow - optimization needed")
                print("   • Consider increasing VRAM allocation for voice models")
                print("   • Optimize the VTT orchestrator pipeline")
                print("   • Implement better caching for frequently used NPC responses")

            if max_time > 30:
                print(f"   🚨 Maximum response time ({max_time:.1f}s) indicates system bottlenecks")
                print("   • Check resource manager for concurrent task limits")
                print("   • Monitor VRAM usage during test runs")
                print("   • Consider implementing predictive rendering")

            print(f"\n✅ Current Performance:")
            print(f"   • Average NPC Response: {avg_time:.2f}s")
            print(f"   • Time Range: {min([r.get('response_time_seconds', 0) for r in stats['detailed_results']]):.2f}s - {max_time:.2f}s")

    except Exception as e:
        print(f"❌ Test suite failed: {e}")
        return 1

    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)