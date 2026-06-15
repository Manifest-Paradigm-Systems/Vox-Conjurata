# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: ALL CORE INFERENCE MODELS (Text, HD Vision, Audio, Image Generation) MUST BE GPU RESIDENT OR HOT-SWAPPABLE. `MiniCPM-V 2.6` (Vision Reader) is the default resident "Monster Sight" analyzer.
- **Vision Gen (Always-On)**: Reverted to `SDXL-Lightning 4-step Q4` GGUF model via `stable-diffusion.cpp` (~3.6GB VRAM). Base resolution is 1024x1024. Diffusers implementation deprecated due to severe AMD ROCm fragmentation and SDPA bugs.
- **Vision Reader (Always-On)**: `MiniCPM-V 2.6` remains resident (~4.8GB VRAM), enabling persistent environmental awareness.
- **LLM Context**: `Kunou 14B` (Qwen 2.5) utilizes an **8-bit (FP8) KV Cache** with a restored **32,768 token** context window.
- **Audio Stack**: 
    - **Voice Core**: `VoxCPM2` (2.1B) resident (~4.2GB VRAM) for sub-200ms dialogue generation.
    - **SFX Engine**: `Stable Audio Open` resident on primary GPU (~0.8GB) for maximum responsiveness (~1.5s renders).
    - **Music Gen**: `Stable Audio` resident on primary GPU (~4.0GB). JIT eviction deprecated in favor of asynchronous compute queue.
    - **STT (Speech-to-Text)**: `Faster-Whisper` is offloaded to the **Secondary 2GB GPU** (`ROCR_VISIBLE_DEVICES=1`) to reclaim primary VRAM.
- **System Memory**: Total RX 7900 base load is **~23.6 GB**, leaving a **~8.4 GB safety buffer** for multi-table support and OS/Foundry spikes.
- **Compute Queue**: The Orchestrator (`ResourceManager`) enforces sequential GPU execution (`gpu_compute_lock`) across all resident models (Image, Music, Vision Reader) to prevent simultaneous compute starvation while maintaining zero-latency residency.
- **Control Interface**: Dedicated **Workhorse WebUI** (Port 8090) provides real-time VRAM telemetry, high-watermark spike logging, rich media previews, and container lifecycle controls (START/STOP/IDLE).

## Recent Updates (June 11-12, 2026)
- **Deployment**: Deployed unified Workhorse Control Panel with direct manual query support and rich media previews.
- **Architecture**: Fully realized the "Always-On" multi-resident architecture. All swapping and JIT behavior removed.
- **Safety**: Moved micro-services to secondary GPU. Established an 8.4GB safety buffer with an automated 92% VRAM OOM kill-switch.
- **Latency Fix**: Bypassed ROCm compute bugs by standardizing on `stable-diffusion.cpp` for vision generation.
