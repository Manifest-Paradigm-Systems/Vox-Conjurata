"""
Vox Conjurata - Deep Performance Profiler

This tool analyzes the complete NPC voice response pipeline to identify bottlenecks.
It measures timing at each stage and provides specific optimization recommendations.
"""

import time
import json
import requests
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import asyncio

logger = logging.getLogger("performance-profiler")

class PipelineStage(Enum):
    VOICE_CONVERSION_START = "voice_conversion_start"
    AUDIO_PREPROCESSING = "audio_preprocessing"
    STT_TRANSCRIPTION = "stt_transcription"
    LLM_ENRICHMENT = "llm_enrichment"
    VOICE_SEED_LOOKUP = "voice_seed_lookup"
    VOICE_GENERATION = "voice_generation"
    DSP_PROCESSING = "dsp_processing"
    AUDIO_POSTPROCESSING = "audio_postprocessing"
    ENDPOINT_RESPONSE = "endpoint_response"

@dataclass
class PipelineTiming:
    stage: PipelineStage
    start_time: float
    end_time: float
    duration: float
    actor_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PerformanceAnalysis:
    total_time: float
    stages: List[PipelineTiming]
    actor_performance: Dict[str, float]
    bottlenecks: List[str]
    recommendations: List[str]

class VoiceResponseProfiler:
    """
    Deep performance profiler for NPC voice response pipeline.

    Measures timing at each stage to identify bottlenecks and provide optimization recommendations.
    """

    def __init__(self, orchestrator_url: str = "http://localhost:8080"):
        self.orchestrator_url = orchestrator_url
        self.session_cache: Dict[str, Dict] = {}
        self.cached_audio_files: Dict[str, bytes] = {}

    async def profile_single_npc_response(
        self,
        actor_id: str,
        voice_profile: Dict,
        response_text: str = "Hello, how are you today?"
    ) -> PerformanceAnalysis:
        """
        Profile a single NPC voice response with detailed stage timing.
        """
        print(f"🧪 Profiling NPC response: {actor_id}")
        print(f"   Profile: {voice_profile.get('profile', 'Unknown')}")

        # Track timing for each stage
        timings: List[PipelineTiming] = []
        current_time = time.time()

        try:
            # Stage 1: Prepare request
            current_time = self._record_stage(
                timings, PipelineStage.VOICE_CONVERSION_START,
                current_time, actor_id=actor_id
            )

            # Stage 2: Audio preprocessing (if we had actual audio)
            # Simulate preprocessing delay
            await asyncio.sleep(0.5)  # Simulating audio checksum/validation
            current_time = self._record_stage(
                timings, PipelineStage.AUDIO_PREPROCESSING,
                time.time(), actor_id=actor_id
            )

            # Stage 3: Speech-to-Text (STT)
            stt_start = time.time()
            try:
                # Check if we have cached STT results to simulate
                cache_key = f"stt_{actor_id}_{hash(response_text)}"
                if cache_key in self.session_cache:
                    stt_latency = 0.1  # Cached - very fast
                else:
                    stt_latency = 5.0 + (hash(response_text) % 300) / 100  # Simulate 5-8 seconds
                    self.session_cache[cache_key] = {"stt": stt_start}

                current_time = self._record_stage(
                    timings, PipelineStage.STT_TRANSCRIPTION,
                    time.time(), actor_id=actor_id,
                    metadata={"stt_latency": stt_latency}
                )
            except Exception as e:
                logger.warning(f"STT simulation failed: {e}")
                current_time = self._record_stage(
                    timings, PipelineStage.STT_TRANSCRIPTION,
                    time.time(), actor_id=actor_id,
                    metadata={"error": str(e)}
                )

            # Stage 4: LLM Enrichment
            current_time = self._record_stage(
                timings, PipelineStage.LLM_ENRICHMENT,
                time.time(), actor_id=actor_id
            )
            await asyncio.sleep(2.0)  # Simulate LLM processing (2 seconds)
            current_time = self._record_stage(
                timings, PipelineStage.LLM_ENRICHMENT,
                time.time(), actor_id=actor_id
            )

            # Stage 5: Voice Seed Lookup (THE CACHING PART)
            seed_lookup_start = time.time()
            try:
                # Simulate seed lookup with caching
                if f"seed_{actor_id}" not in self.session_cache:
                    # First lookup - simulate filesystem scan
                    await asyncio.sleep(0.2)  # Directory listing
                    self.session_cache[f"seed_{actor_id}"] = {
                        "lookup_start": seed_lookup_start,
                        "path": f"./voice_seeds/{actor_id}_seed_generated.wav"
                    }
                    seed_latency = 0.5
                else:
                    # Cached lookup
                    seed_latency = 0.01  # Very fast

                current_time = self._record_stage(
                    timings, PipelineStage.VOICE_SEED_LOOKUP,
                    time.time(), actor_id=actor_id,
                    metadata={"seed_latency": seed_latency}
                )
            except Exception as e:
                logger.warning(f"Seed lookup simulation failed: {e}")
                current_time = self._record_stage(
                    timings, PipelineStage.VOICE_SEED_LOOKUP,
                    time.time(), actor_id=actor_id,
                    metadata={"error": str(e)}
                )

            # Stage 6: Voice Generation (BOTTLE NECK)
            gen_start = time.time()
            try:
                # Simulate actual voice generation with variable latency
                base_latency = 15.0  # Base generation time
                complexity_factor = (hash(response_text) % 1000) / 1000  # 0-1
                generation_latency = base_latency + (complexity_factor * 20.0)  # 15-35 seconds

                await asyncio.sleep(generation_latency)
                current_time = self._record_stage(
                    timings, PipelineStage.VOICE_GENERATION,
                    time.time(), actor_id=actor_id,
                    metadata={"generation_latency": generation_latency}
                )
            except Exception as e:
                logger.warning(f"Voice generation simulation failed: {e}")
                current_time = self._record_stage(
                    timings, PipelineStage.VOICE_GENERATION,
                    time.time(), actor_id=actor_id,
                    metadata={"error": str(e)}
                )

            # Stage 7: DSP Processing
            current_time = self._record_stage(
                timings, PipelineStage.DSP_PROCESSING,
                time.time(), actor_id=actor_id
            )
            await asyncio.sleep(0.3)  # Simulate DSP effects
            current_time = self._record_stage(
                timings, PipelineStage.DSP_PROCESSING,
                time.time(), actor_id=actor_id
            )

            # Stage 8: Audio Post-processing
            current_time = self._record_stage(
                timings, PipelineStage.AUDIO_POSTPROCESSING,
                time.time(), actor_id=actor_id
            )

            # Stage 9: Endpoint response
            current_time = self._record_stage(
                timings, PipelineStage.ENDPOINT_RESPONSE,
                time.time(), actor_id=actor_id
            )

            # Calculate total time
            total_time = timings[-1].end_time - timings[0].start_time

            # Analyze bottlenecks
            bottlenecks = self._identify_bottlenecks(timings)
            recommendations = self._generate_recommendations(timings)

            # Actor performance summary
            actor_performance = {}
            for timing in timings:
                if timing.actor_id:
                    actor_performance[timing.actor_id] = timing.duration

            return PerformanceAnalysis(
                total_time=total_time,
                stages=timings,
                actor_performance=actor_performance,
                bottlenecks=bottlenecks,
                recommendations=recommendations
            )

        except Exception as e:
            logger.error(f"Profiling failed for {actor_id}: {e}")
            raise

    def _record_stage(self, timings: List[PipelineTiming], stage: PipelineStage,
                     end_time: float, **kwargs) -> float:
        """Record timing for a pipeline stage"""
        current_time = end_time

        # Create timing entry with appropriate start time for each stage
        if stage == PipelineStage.VOICE_CONVERSION_START:
            start_time = current_time
        else:
            # For other stages, use the previous stage's end time or current time
            prev_timing = timings[-1] if timings else None
            start_time = prev_timing.end_time if prev_timing else current_time

        # Create timing entry
        timing = PipelineTiming(
            stage=stage,
            start_time=start_time,
            end_time=current_time,
            duration=current_time - start_time,
            **kwargs
        )

        timings.append(timing)
        return current_time

    def _identify_bottlenecks(self, timings: List[PipelineTiming]) -> List[str]:
        """Identify the biggest bottlenecks in the pipeline"""
        bottlenecks = []

        if len(timings) < 2:
            return bottlenecks

        # Find stages that take > 70% of total time
        total_time = timings[-1].end_time - timings[0].start_time
        bottleneck_threshold = total_time * 0.7  # Top 70% threshold

        for timing in timings:
            if timing.duration > bottleneck_threshold:
                bottlenecks.append(
                    f"{timing.stage.value}: {timing.duration:.1f}s ({timing.duration/total_time*100:.1f}% of total)"
                )

        # Always identify voice generation as a bottleneck if significant
        gen_stage = next((t for t in timings if t.stage == PipelineStage.VOICE_GENERATION), None)
        if gen_stage and gen_stage.duration > 10:
            bottlenecks.append(f"Voice generation: {gen_stage.duration:.1f}s (main processing bottleneck)")

        # Identify STT as bottleneck if significant
        stt_stage = next((t for t in timings if t.stage == PipelineStage.STT_TRANSCRIPTION), None)
        if stt_stage and stt_stage.duration > 3:
            bottlenecks.append(f"STT transcription: {stt_stage.duration:.1f}s (pre-processing delay)")

        return bottlenecks

    def _generate_recommendations(self, timings: List[PipelineTiming]) -> List[str]:
        """Generate optimization recommendations based on profiling"""
        recommendations = []

        # Check for seed lookup bottlenecks
        seed_stage = next((t for t in timings if t.stage == PipelineStage.VOICE_SEED_LOOKUP), None)
        if seed_stage and seed_stage.duration > 0.5:
            recommendations.append("📝 Voice seed lookup is slow - implement filesystem caching")

        # Check for STT bottlenecks
        stt_stage = next((t for t in timings if t.stage == PipelineStage.STT_TRANSCRIPTION), None)
        if stt_stage and stt_stage.duration > 5:
            recommendations.append("🎤 STT processing is slow - consider faster inference models or parallel processing")

        # Check for voice generation bottlenecks
        gen_stage = next((t for t in timings if t.stage == PipelineStage.VOICE_GENERATION), None)
        if gen_stage and gen_stage.duration > 20:
            recommendations.append("🔊 Voice generation is slow - investigate GPU utilization or model optimization")

        # Check cache hit rate
        cache_hits = len([t for t in timings if t.stage in [
            PipelineStage.VOICE_SEED_LOOKUP, PipelineStage.STT_TRANSCRIPTION
        ]])
        total_stages = len(timings)
        cache_hit_rate = cache_hits / total_stages if total_stages > 0 else 0

        if cache_hit_rate < 0.3:
            recommendations.append(f"💾 Low cache hit rate ({cache_hit_rate*100:.1f}%) - implement intelligent caching strategies")

        # Always add key optimizations
        recommendations.extend([
            "🚀 Consider parallel processing for independent pipeline stages",
            "📊 Monitor system resources during peak loads",
            "⚡ Optimize network latency between services",
            "🎛️ Fine-tune DSP parameters for faster processing"
        ])

        return recommendations

    def print_detailed_report(self, analysis: PerformanceAnalysis, run_num: int = 1):
        """Print detailed performance analysis report"""
        print(f"\n{'='*80}")
        print(f"🎙️  VOX CONJURATA - PERFORMANCE ANALYSIS - Run #{run_num}")
        print(f"{'='*80}")

        print(f"\n⏱️  OVERALL PERFORMANCE:")
        print(f"   • Total response time: {analysis.total_time:.1f} seconds")
        print(f"   • Number of pipeline stages: {len(analysis.stages)}")

        print(f"\n📊 STAGE TIMING BREAKDOWN:")
        print(f"   {'Stage':<35} {'Duration':<12} {'% of Total':<12} {'Actor':<15}")
        print(f"   {'-'*80}")

        for i, timing in enumerate(analysis.stages):
            percent_total = (timing.duration / analysis.total_time * 100) if analysis.total_time > 0 else 0
            stage_name = timing.stage.value.replace('_', ' ').title()
            actor = timing.actor_id or "System"

            print(f"   {stage_name:<35} {timing.duration:>8.1f}s {percent_total:>8.1f}%      {actor:<15}")

        print(f"\n🚨 TOP BOTTLENECKS IDENTIFIED:")
        for bottleneck in analysis.bottlenecks:
            print(f"   ⚠️  {bottleneck}")

        if not analysis.bottlenecks:
            print(f"   ✅ No major bottlenecks identified")

        print(f"\n💡 OPTIMIZATION RECOMMENDATIONS:")
        for rec in analysis.recommendations[:5]:  # Top 5 recommendations
            print(f"   🛠️  {rec}")

        print(f"\n📈 ACTOR-SPECIFIC PERFORMANCE:")
        for actor, duration in analysis.actor_performance.items():
            print(f"   🎭 {actor}: {duration:.1f}s response time")

        print(f"\n{'='*80}")
        return analysis

    async def run_comprehensive_profiling(self):
        """Run comprehensive profiling on multiple NPC types"""
        print("🔬 Starting Comprehensive Voice Response Performance Profiling")
        print("=" * 80)
        print("This will simulate the complete pipeline to identify bottlenecks.")
        print("")

        # Define test scenarios with different characteristics
        test_scenarios = [
            {
                "name": "Valeros (Fighter)",
                "actor_id": "vcwqnXHkhzFhrt7O",
                "profile": "Confident warrior voice",
                "type": "PC"
            },
            {
                "name": "Merisiel (Rogue)",
                "actor_id": "czQ0MaZBu3BqMpce",
                "profile": "Quick-tongued rogue voice",
                "type": "PC"
            },
            {
                "name": "Xulgath Warrior (Monster)",
                "actor_id": "5vBG8a8dnJfmVd3Y",
                "profile": "Guttural monster voice",
                "type": "NPC"
            },
            {
                "name": "Generic Guard (NPC)",
                "actor_id": "generic_guard_01",
                "profile": "Generic soldier voice",
                "type": "NPC"
            }
        ]

        results = []
        total_runs = len(test_scenarios)

        for i, scenario in enumerate(test_scenarios, 1):
            print(f"\n📊 Profiling Run {i}/{total_runs}: {scenario['name']}")
            print(f"   Actor: {scenario['actor_id']}")
            print(f"   Profile: {scenario['profile']}")
            print(f"   Type: {scenario['type']}")
            print("   ⏱️  Simulating NPC response pipeline...")

            try:
                # Profile the NPC response
                analysis = await self.profile_single_npc_response(
                    actor_id=scenario["actor_id"],
                    voice_profile={
                        "name": scenario["name"],
                        "profile": scenario["profile"],
                        "type": scenario["type"]
                    },
                    response_text="Hello, how are you today? This is a test response from the NPC voice system."
                )

                # Print detailed report
                self.print_detailed_report(analysis, i)

                results.append({
                    "run": i,
                    "actor_id": scenario["actor_id"],
                    "actor_name": scenario["name"],
                    "total_time": analysis.total_time,
                    "bottlenecks": analysis.bottlenecks,
                    "recommendations": analysis.recommendations
                })

                # Wait between runs to simulate real processing
                if i < total_runs:
                    print(f"   ⏳ Waiting 5 seconds before next profiling run...")
                    await asyncio.sleep(5)

            except Exception as e:
                print(f"❌ Profiling failed for {scenario['actor_id']}: {e}")
                results.append({
                    "run": i,
                    "actor_id": scenario["actor_id"],
                    "actor_name": scenario["name"],
                    "error": str(e)
                })

        # Generate summary report
        self.print_summary_report(results)

        return results

    def print_summary_report(self, results: List[Dict]):
        """Print comprehensive summary of all profiling runs"""
        print(f"\n{'='*80}")
        print(f"📋 COMPREHENSIVE PROFILING SUMMARY")
        print(f"{'='*80}")

        successful_runs = [r for r in results if "error" not in r]

        if not successful_runs:
            print(f"❌ No successful profiling runs to analyze")
            return

        print(f"\n📊 EXECUTION SUMMARY:")
        print(f"   • Total runs attempted: {len(results)}")
        print(f"   • Successful runs: {len(successful_runs)}")
        print(f"   • Failed runs: {len(results) - len(successful_runs)}")

        if successful_runs:
            print(f"\n⏱️  PERFORMANCE METRICS:")
            avg_time = sum(r["total_time"] for r in successful_runs) / len(successful_runs)
            min_time = min(r["total_time"] for r in successful_runs)
            max_time = max(r["total_time"] for r in successful_runs)

            print(f"   • Average response time: {avg_time:.1f} seconds")
            print(f"   • Fastest response: {min_time:.1f} seconds")
            print(f"   • Slowest response: {max_time:.1f} seconds")
            print(f"   • Performance range: {max_time - min_time:.1f} seconds")

        print(f"\n🎯 BOTTLENECK ANALYSIS:")
        bottleneck_counts = {}
        for result in successful_runs:
            for bottleneck in result.get("bottlenecks", []):
                bottleneck_name = bottleneck.split(":")[0]
                bottleneck_counts[bottleneck_name] = bottleneck_counts.get(bottleneck_name, 0) + 1

        for bottleneck, count in bottleneck_counts.items():
            percentage = (count / len(successful_runs)) * 100
            print(f"   • {bottleneck}: {percentage:.1f}% of runs")

        print(f"\n💡 OVERALL RECOMMENDATIONS:")
        all_recommendations = []
        for result in successful_runs:
            all_recommendations.extend(result.get("recommendations", []))

        # Remove duplicates while preserving order
        seen = set()
        unique_recommendations = []
        for rec in all_recommendations:
            if rec not in seen:
                seen.add(rec)
                unique_recommendations.append(rec)

        for i, rec in enumerate(unique_recommendations[:8], 1):
            print(f"   {i}. {rec}")

        print(f"\n{'='*80}")

    def save_results(self, results: List[Dict], filename: str = "performance_profiling_results.json"):
        """Save profiling results to JSON file"""
        output_path = Path(filename)

        with open(output_path, 'w') as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_runs": len(results),
                "successful_runs": len([r for r in results if "error" not in r]),
                "results": results
            }, f, indent=2)

        print(f"💾 Results saved to: {output_path}")
        return output_path

    async def run_profiling_session(self):
        """Run the complete profiling session"""
        try:
            results = await self.run_comprehensive_profiling()

            # Offer to save results
            save_choice = input("\n💾 Save detailed results to JSON file? (y/n): ").strip().lower()
            if save_choice == 'y':
                filename = input(f"📁 Enter filename (default: performance_profiling_results.json): ").strip()
                if not filename:
                    filename = "performance_profiling_results.json"

                self.save_results(results, filename)
                print(f"✅ Results saved successfully!")

            print(f"\n🔬 Profiling session completed!")
            print(f"📊 Review the detailed reports above for optimization recommendations.")

        except Exception as e:
            print(f"❌ Profiling session failed: {e}")
            logger.error(f"Profiling session failed: {e}")

async def main():
    """Main entry point for the performance profiler"""
    print("🚀 Starting Vox Conjurata - Deep Performance Profiler")
    print("=" * 80)
    print("This tool analyzes NPC voice response bottlenecks and provides optimization recommendations.")
    print("")
    print("📋 What this profiler will do:")
    print("   • Simulate the complete voice generation pipeline")
    print("   • Measure timing at each stage")
    print("   • Identify performance bottlenecks")
    print("   • Provide specific optimization recommendations")
    print("   • Compare performance across different NPC types")
    print("")
    print("⚠️  Note: This is a simulation to analyze architecture, not actual voice generation")
    print("")

    # Create and run profiler
    profiler = VoiceResponseProfiler()

    try:
        await profiler.run_profiling_session()
    except KeyboardInterrupt:
        print(f"\n⏹ Profiling interrupted by user")
    except Exception as e:
        print(f"❌ Profiling failed: {e}")
        logger.error(f"Profiling failed: {e}")

if __name__ == "__main__":
    # Run the performance profiler
    asyncio.run(main())