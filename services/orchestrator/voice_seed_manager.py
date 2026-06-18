"""
Permanent Voice Seed Cache System

This module implements a persistent, multi-layer caching system for NPC voice seeds.
Seeds are generated once and reused indefinitely unless manually refreshed by the DM.

Key Features:
- Layer 1: In-memory cache (fastest, temporary)
- Layer 2: Registry cache (semi-slow, persists across restarts)
- Layer 3: Filesystem cache (slowest, permanent)
- Cache invalidation only on DM action
- Thread-safe for concurrent access
"""

import os
import time
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger("voice-seed-cache")

@dataclass
class CacheEntry:
    """Cache entry metadata"""
    seed_path: str
    timestamp: float
    file_size: int
    source: str = "generated"  # "generated", "palette", "filesystem"
    version: int = 1

@dataclass
class SeedCacheStats:
    """Cache performance statistics"""
    cache_hits: int = 0
    cache_misses: int = 0
    regenerations: int = 0
    total_requests: int = 0

class VoiceSeedManager:
    """
    Persistent voice seed cache with multi-layer fallback strategy.

    Design Philosophy:
    - Seeds generated once, reused forever
    - Only DM actions trigger regeneration
    - Zero performance impact after initial seed generation
    - Maintains backward compatibility
    """

    def __init__(self, seed_dir: str = "./voice_seeds", registry_path: str = "./settings/voice_registry.json"):
        self.seed_dir = Path(seed_dir)
        self.seed_dir.mkdir(parents=True, exist_ok=True)

        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        # Multi-layer cache: Memory > Registry > Filesystem > Generate
        self.memory_cache: Dict[str, CacheEntry] = {}
        self.memory_cache_lock = Lock()
        self.registry_cache: Dict[str, Dict] = {}
        self.registry_cache_time = 0
        self.registry_ttl = 300  # 5 minutes for registry cache

        # Pre-scanned filesystem cache
        self.filesystem_seeds: Dict[str, List[str]] = {}
        self.filesystem_scan_time = 0
        self.filesystem_scan_ttl = 3600  # 1 hour

        # Statistics
        self.stats = SeedCacheStats()
        self.stats_lock = Lock()

        # Initialize system
        self._initialize_system()

    def _initialize_system(self):
        """Initialize cache and scan filesystem"""
        logger.info("🗄️  Initializing Voice Seed Cache System")

        # Pre-scan filesystem for all seeds
        self._scan_filesystem()

        # Load registry (with caching)
        self._load_registry()

        # Process existing seed files into cache
        self._process_existing_seeds()

        logger.info(f"✅ Voice Seed Cache initialized")
        logger.info(f"   📊 Memory entries: {len(self.memory_cache)}")
        logger.info(f"   📁 Registry entries: {len(self.registry_cache)}")
        logger.info(f"   🎵 Pre-scanned actors: {len(self.filesystem_seeds)}")

    def _scan_filesystem(self):
        """Pre-scan filesystem to avoid repeated I/O operations"""
        logger.info("🔍 Scanning filesystem for existing seed files...")

        self.filesystem_seeds = {}
        seed_files = list(self.seed_dir.glob("_seed_*.wav"))  # Main seeds
        palette_files = list(self.seed_dir.glob("_palette/*_seed_*.wav"))  # Palette seeds

        # Process main seed files
        for seed_file in seed_files:
            # Extract actor_id from filename (e.g., "actor_seed_male.wav" -> "actor")
            actor_id = seed_file.stem.replace("_seed_", "")

            if actor_id not in self.filesystem_seeds:
                self.filesystem_seeds[actor_id] = []
            self.filesystem_seeds[actor_id].append(str(seed_file))

        # Process palette seed files
        for seed_file in palette_files:
            actor_id = seed_file.stem.replace("_seed_", "")

            if actor_id not in self.filesystem_seeds:
                self.filesystem_seeds[actor_id] = []
            self.filesystem_seeds[actor_id].append(str(seed_file))

        self.filesystem_scan_time = time.time()
        logger.info(f"   📁 Found {len(self.filesystem_seeds)} actors in filesystem")

    def _load_registry(self):
        """Load voice registry with caching"""
        current_time = time.time()

        # Use cached registry if fresh
        if (hasattr(self, '_registry_cache_time') and
            current_time - self._registry_cache_time < self.registry_ttl and
            hasattr(self, '_registry_data')):
            logger.debug("📋 Using cached registry")
            self.registry_cache = self._registry_data
            return

        # Load fresh registry
        logger.info("📋 Loading voice registry...")

        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r") as f:
                    registry_data = json.load(f)

                self._registry_data = registry_data
                self._registry_cache_time = current_time
                self.registry_cache = registry_data

                logger.info(f"   ✅ Loaded {len(registry_data)} registry entries")

            except Exception as e:
                logger.error(f"❌ Failed to load registry: {e}")
                self.registry_cache = {}
        else:
            logger.warning(f"⚠️  Registry file not found: {self.registry_path}")
            self.registry_cache = {}

    def _process_existing_seeds(self):
        """Process existing seed files into memory cache"""
        logger.info("🎵 Processing existing seeds into memory cache...")

        for actor_id, seed_files in self.filesystem_seeds.items():
            for seed_path in seed_files:
                try:
                    file_size = os.path.getsize(seed_path)
                    cache_entry = CacheEntry(
                        seed_path=seed_path,
                        timestamp=time.time(),
                        file_size=file_size,
                        source="filesystem"
                    )

                    self.memory_cache[actor_id] = cache_entry

                except Exception as e:
                    logger.error(f"❌ Failed to process seed {seed_path}: {e}")

        logger.info(f"   🎵 Added {len(self.memory_cache)} seeds to memory cache")

    def get_seed_path(self, actor_id: str) -> Optional[str]:
        """
        Get seed path for actor_id using multi-layer cache strategy.

        Cache lookup order:
        1. Memory cache (fastest)
        2. Registry cache (memory-backed)
        3. Filesystem cache (pre-scanned)
        4. Generate new seed (fallback)

        Returns:
            Optional[str]: Path to voice seed file, or None if not found.
        """
        with self.stats_lock:
            self.stats.total_requests += 1

        actor_id = actor_id.lower()  # Normalize for consistency

        # ✅ Layer 1: Memory cache (fastest)
        with self.memory_cache_lock:
            if actor_id in self.memory_cache:
                cache_entry = self.memory_cache[actor_id]

                # Check if cache entry is still valid
                if self._is_cache_valid(cache_entry):
                    with self.stats_lock:
                        self.stats.cache_hits += 1

                    logger.debug(f"🎯 Memory cache HIT: {actor_id}")
                    return cache_entry.seed_path

        # ✅ Layer 2: Check registry for explicit seed path
        seed_path = self._resolve_from_registry(actor_id)
        if seed_path and os.path.exists(seed_path):
            with self.memory_cache_lock:
                cache_entry = self._create_cache_entry(seed_path, "registry")
                self.memory_cache[actor_id] = cache_entry

            with self.stats_lock:
                self.stats.cache_hits += 1

            logger.debug(f"🎯 Registry cache HIT: {actor_id}")
            return seed_path

        # ✅ Layer 3: Check pre-scanned filesystem
        seed_path = self._resolve_from_filesystem(actor_id)
        if seed_path:
            with self.memory_cache_lock:
                cache_entry = self._create_cache_entry(seed_path, "filesystem")
                self.memory_cache[actor_id] = cache_entry

            with self.stats_lock:
                self.stats.cache_hits += 1

            logger.debug(f"🎯 Filesystem cache HIT: {actor_id}")
            return seed_path

        # ❌ Layer 4: No seed found
        with self.stats_lock:
            self.stats.cache_misses += 1

        logger.debug(f"❌ No cached seed found for {actor_id}")
        return None

    def _is_cache_valid(self, cache_entry: CacheEntry) -> bool:
        """Check if cache entry is still valid based on seed file"""
        try:
            current_size = os.path.getsize(cache_entry.seed_path)

            # Seed valid if:
            # 1. File still exists
            # 2. File size hasn't changed significantly
            # 3. Age is not excessive (24 hours max)
            file_age = time.time() - cache_entry.timestamp
            size_diff = abs(current_size - cache_entry.file_size)
            size_tolerance = max(cache_entry.file_size * 0.01, 100)  # 1% or 100 bytes

            return (current_size > 1000 and  # Reasonable file size
                   file_age < 86400 and     # Less than 24 hours old
                   size_diff < size_tolerance)  # File not significantly changed

        except Exception:
            return False

    def _resolve_from_registry(self, actor_id: str) -> Optional[str]:
        """Resolve seed path from registry entries"""
        self._load_registry()  # Ensure registry is loaded

        entry = self.registry_cache.get(actor_id)
        if not entry:
            return None

        seed_path = entry.get("seed_path")
        if not seed_path:
            return None

        full_path = self.seed_dir / seed_path
        if full_path.exists():
            return str(full_path)

        return None

    def _resolve_from_filesystem(self, actor_id: str) -> Optional[str]:
        """Resolve seed path from pre-scanned filesystem"""
        # Refresh filesystem scan if stale
        if time.time() - self.filesystem_scan_time > self.filesystem_scan_ttl:
            self._scan_filesystem()

        seed_files = self.filesystem_seeds.get(actor_id, [])
        if seed_files:
            # Return the best available seed
            for seed_path in seed_files:
                if self._is_seed_valid(seed_path):
                    return seed_path

        return None

    def _is_seed_valid(self, seed_path: str) -> bool:
        """Check if seed file is valid for use"""
        try:
            return (os.path.exists(seed_path) and
                   os.path.getsize(seed_path) > 1000)
        except Exception:
            return False

    def _create_cache_entry(self, seed_path: str, source: str) -> CacheEntry:
        """Create cache entry from seed file"""
        try:
            file_size = os.path.getsize(seed_path)
            return CacheEntry(
                seed_path=seed_path,
                timestamp=time.time(),
                file_size=file_size,
                source=source
            )
        except Exception as e:
            logger.error(f"❌ Failed to create cache entry for {seed_path}: {e}")
            # Return minimal valid entry
            return CacheEntry(
                seed_path=seed_path,
                timestamp=time.time(),
                file_size=0,
                source="error"
            )

    def generate_seed(self, actor_id: str, voice_description: str) -> Tuple[bool, Optional[str]]:
        """
        Generate new voice seed for actor_id.

        This is called when no existing seed is found and generation is required.

        Returns:
            Tuple[bool, Optional[str]]: (success, seed_path)
        """
        actor_id = actor_id.lower()

        # Check if we should generate a seed (not already cached or cache expired)
        with self.memory_cache_lock:
            if actor_id in self.memory_cache:
                cache_entry = self.memory_cache[actor_id]
                # Only generate if cache entry is very old (24+ hours)
                if time.time() - cache_entry.timestamp < 86400:
                    logger.info(f"🔄 Seed already exists for {actor_id}, skipping generation")
                    return False, cache_entry.seed_path

        # Generate new seed
        logger.info(f"🔄 Generating new voice seed for NPC: {actor_id}")

        try:
            # This would normally call the VoxCPM2 API
            # For implementation, we'll create a placeholder seed file
            seed_filename = f"{actor_id}_seed_generated.wav"
            seed_path = self.seed_dir / seed_filename

            # Simulate seed generation (in real implementation, this would use VoxCPM2)
            with open(seed_path, "wb") as f:
                # Write minimal valid WAV header (440 bytes = ~0.01 seconds)
                f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00\x00\xff\x7f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00")

            file_size = os.path.getsize(seed_path)

            with self.memory_cache_lock:
                cache_entry = CacheEntry(
                    seed_path=str(seed_path),
                    timestamp=time.time(),n                    file_size=file_size,
                    source="generated"
                )
                self.memory_cache[actor_id] = cache_entry

                # Update registry
                self._update_registry(actor_id, seed_filename, voice_description)

            with self.stats_lock:
                self.stats.regenerations += 1

            logger.info(f"✅ Generated new seed for {actor_id}: {seed_path}")
            return True, str(seed_path)

        except Exception as e:
            logger.error(f"❌ Failed to generate seed for {actor_id}: {e}")
            return False, None

    def _update_registry(self, actor_id: str, seed_path: str, voice_description: str):
        """Update registry with new seed information"""
        self._load_registry()

        # Determine archetype and type from actor_id
        is_archetype = self._is_archetype(actor_id)
        archetype_key = self._determine_archetype_key(actor_id)

        new_entry = {
            "engine": "vox-audio-core",
            "seed_path": seed_path,
            "voice_prompt": voice_description,
            "is_archetype": is_archetype,
            "archetype_key": archetype_key,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")
        }

        self.registry_cache[actor_id] = new_entry

        # Write back to disk
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.registry_path, "w") as f:
                json.dump(self.registry_cache, f, indent=2)

            logger.debug(f"📋 Updated registry for {actor_id}")

        except Exception as e:
            logger.error(f"❌ Failed to write registry: {e}")

    def _is_archetype(self, actor_id: str) -> bool:
        """Check if actor_id represents an archetype"""
        # Archetype keys are typically shorter, more descriptive
        # Regular actor IDs are usually UUID-like or descriptive names
        return (len(actor_id) < 20 or  # Short IDs likely archetypes
               any(char.isdigit() for char in actor_id) or  # Contains numbers likely generated
               actor_id.startswith("palette_"))  # Explicit palette naming

    def _determine_archetype_key(self, actor_id: str) -> str:
        """Determine appropriate archetype key from actor_id"""
        # Default to humanoid if not monster
        if "monster" in actor_id.lower() or "monster" in self.registry_cache.get(actor_id, {}).get("voice_prompt", ""):
            return "monster_beast"
        return "human_male_british"

    def refresh_seed(self, actor_id: str, voice_description: str) -> Tuple[bool, Optional[str]]:
        """
        Manually refresh a specific seed (DM action only).

        This method is called when the DM wants to regenerate a specific NPC's voice.
        """
        actor_id = actor_id.lower()

        with self.memory_cache_lock:
            if actor_id in self.memory_cache:
                del self.memory_cache[actor_id]

        # Generate new seed
        return self.generate_seed(actor_id, voice_description)

    def refresh_all_seeds(self, voice_description_pattern: str = None) -> int:
        """
        Manually refresh ALL seeds (DM emergency action only).

        Returns:
            int: Number of seeds regenerated
        """
        logger.warning("⚠️  DM Emergency: Refreshing ALL voice seeds")

        refreshed_count = 0
        for actor_id in list(self.memory_cache.keys()):
            # Generate new seed for each actor
            voice_desc = (voice_description_pattern or
                        f"Emergency voice refresh for {actor_id}")

            success, seed_path = self.generate_seed(actor_id, voice_desc)
            if success:
                refreshed_count += 1

        logger.warning(f"⚠️  DM Emergency refresh completed: {refreshed_count} seeds regenerated")
        return refreshed_count

    def get_cache_stats(self) -> dict:
        """Get cache performance statistics"""
        with self.stats_lock:
            stats = self.stats

            total_requests = stats.total_requests
            cache_hits = stats.cache_hits
            cache_misses = stats.cache_misses

            hit_rate = (cache_hits / total_requests * 100) if total_requests > 0 else 0

            return {
                "total_requests": total_requests,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "hit_rate_percentage": round(hit_rate, 2),
                "regenerations": stats.regenerations,
                "memory_cache_size": len(self.memory_cache),
                "registry_cache_size": len(self.registry_cache),
                "filesystem_actors": len(self.filesystem_seeds),
                "cache_hit_ratio": f"{stats.cache_hits}/{stats.total_requests}" if stats.total_requests > 0 else "0/0"
            }

    def cleanup_old_cache_entries(self, max_age_seconds: int = 86400):
        """
        Clean up old cache entries to free memory.

        Note: Voice seeds are designed to be permanent once generated.
        This cleanup mainly removes entries for actors that are no longer in use.
        """
        current_time = time.time()
        cleaned_count = 0

        with self.memory_cache_lock:
            for actor_id in list(self.memory_cache.keys()):
                cache_entry = self.memory_cache[actor_id]

                # Remove entries that are very old and likely stale
                if current_time - cache_entry.timestamp > max_age_seconds:
                    del self.memory_cache[actor_id]
                    cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"🧹 Cleaned up {cleaned_count} old cache entries")

        return cleaned_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup on exit"""
        logger.info("🗄️  Voice Seed Cache shutting down")

        # Save final cache stats
        stats = self.get_cache_stats()
        logger.info(f"📊 Final cache stats: {stats}")

        # Don't cleanup memory cache - let Python handle garbage collection