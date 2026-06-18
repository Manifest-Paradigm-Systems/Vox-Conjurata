from voice_seed_manager import VoiceSeedManager

# Initialize permanent voice seed cache
voice_seed_manager = VoiceSeedManager()

# Modify the resolve_seed_path function to use the cache
def resolve_seed_path(actor_id: str) -> str:
    """
    Resolve seed path using permanent voice seed cache with multi-layer fallback.

    This optimized function replaces the previous simple filesystem scan with
    intelligent caching that dramatically improves performance.

    Cache Lookup Order:
    1. Memory cache (microseconds)
    2. Registry cache (milliseconds)
    3. Filesystem cache (pre-scanned)
    4. Permanent generation (only if truly new)

    Returns:
        str: Path to voice seed file, or empty string if not found.
    """
    # 🎯 Use the permanent voice seed cache system
    seed_path = voice_seed_manager.get_seed_path(actor_id)

    if seed_path:
        logger.info(f"🎵 CACHE HIT: NPC {actor_id} using cached seed at {seed_path}")
        return seed_path

    logger.debug(f"❌ No cached seed found for {actor_id}")
    return ""

# Add voice seed management endpoints to FastAPI app
@chat_controller.post("/api/admin/voice-seeds/refresh")
async def refresh_specific_seed(request: RefreshSeedRequest):
    """
    DM-only endpoint to refresh a specific NPC's voice seed.

    This is the ONLY way voice seeds are regenerated after initial generation.
    """
    try:
        success, seed_path = voice_seed_manager.refresh_seed(
            request.npcId,
            request.voiceDescription
        )

        if success:
            return {
                "status": "success",
                "npcId": request.npcId,
                "seedPath": seed_path,
                "message": f"Voice seed refreshed for NPC: {request.npcId}"
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to refresh seed for NPC: {request.npcId}"
            }

    except Exception as e:
        logger.error(f"❌ Failed to refresh seed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@chat_controller.post("/api/admin/voice-seeds/refresh-all")
async def refresh_all_seeds(request: RefreshAllSeedsRequest):
    """
    DM emergency endpoint to refresh ALL NPC voice seeds.

    This is an emergency action that should be used sparingly.
    """
    try:
        # Verify DM authorization
        if not await is_dm_user(request.userId):
            raise HTTPException(status_code=403, detail="DM authorization required")

        refreshed_count = voice_seed_manager.refresh_all_seeds(
            request.voiceDescriptionPattern
        )

        return {
            "status": "success",
            "refreshed_count": refreshed_count,
            "message": f"Refreshed {refreshed_count} NPC voice seeds"
        }

    except Exception as e:
        logger.error(f"❌ Failed to refresh all seeds: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@chat_controller.get("/api/admin/voice-seeds/cache-stats")
async def get_voice_seed_cache_stats():
    """
    Get voice seed cache performance statistics.

    Useful for monitoring cache effectiveness and performance.
    """
    try:
        stats = voice_seed_manager.get_cache_stats()
        return stats
    except Exception as e:
        logger.error(f"❌ Failed to get cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@chat_controller.post("/api/admin/voice-seeds/cleanup")
async def cleanup_cache():
    """
    Clean up old cache entries (maintenance operation).

    This is typically run weekly or monthly to free memory.
    Note: Voice seeds are permanent once generated, so this mainly
    removes entries for inactive actors.
    """
    try:
        # Verify DM authorization
        if not await is_dm_user(request.userId):
            raise HTTPException(status_code=403, detail="DM authorization required")

        cleaned_count = voice_seed_manager.cleanup_old_cache_entries()

        return {
            "status": "success",
            "cleaned_count": cleaned_count,
            "message": f"Cleaned up {cleaned_count} old cache entries"
        }

    except Exception as e:
        logger.error(f"❌ Failed to cleanup cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Add caching information to orchestrator startup message
@app.on_event("startup")
async def startup_event():
    resource_manager.start_worker()
    asyncio.create_task(prewarm_palette_foundations())
    # Ensure system starts in default HOT state
    asyncio.create_task(container_manager.swap_to_hot_combat())

    # Log cache initialization stats
    stats = voice_seed_manager.get_cache_stats()
    logger.info(f"✅ Voice Seed Cache System initialized")
    logger.info(f"   📊 Cache Statistics: {json.dumps(stats, indent=2)}")

# Output cache stats on shutdown for monitoring
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    stats = voice_seed_manager.get_cache_stats()
    logger.info(f"🗄️  Voice Seed Cache shutdown complete")
    logger.info(f"📊 Final Cache Statistics: {json.dumps(stats, indent=2)}")