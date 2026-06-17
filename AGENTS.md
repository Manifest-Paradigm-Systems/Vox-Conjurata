# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: ALL CORE INFERENCE MODELS (Text, HD Vision, Audio, Image Generation) MUST BE GPU RESIDENT. No swapping or JIT loading is required.
- **Vision Gen (Always-On)**: `CyberRealisticPony_V12.7-vae_f16-q4_0.gguf` via `stable-diffusion.cpp` (~3.6GB VRAM). 
    - **Resolution**: 1024x1024 base upscaled to 1920x1080 (Lanczos).
    - **Inference**: 20 steps, 7.0 CFG.
    - **Injection**: Automatic permanent system prefixes (`score_9`, `cinematic photo`, etc.) for high-fidelity dark fantasy output.
- **Vision Reader (Always-On)**: `MiniCPM-V 2.6` resident (~4.8GB VRAM), enabling persistent environmental awareness.
- **LLM Context (Always-On)**: `Kunou 14B` (Qwen 2.5) via official `llama.cpp:server-rocm` image. 
    - **VRAM**: Full GPU offload (~9GB VRAM).
    - **Context**: 32,768 tokens with FP8 KV-Cache.
    - **Speed**: ~45-50 tokens/sec on RX 7900.
- **Audio Stack**: 
    - **Voice Core**: `VoxCPM2` (2.1B) resident (~4.2GB VRAM) for sub-200ms dialogue generation.
    - **SFX Engine**: `Stable Audio Open` resident on primary GPU (~0.8GB) for maximum responsiveness (~1.5s renders).
    - **STT (Speech-to-Text)**: `Faster-Whisper` is offloaded to the **Secondary 2GB GPU** (`ROCR_VISIBLE_DEVICES=1`) to reclaim primary VRAM.
- **System Memory**: Total RX 7900 base load is **~15.5 GB**, leaving a **~16.5 GB safety buffer** (over 50%) for massive multi-table support and OS/Foundry spikes.
- **Compute Queue**: Orchestrator (`ResourceManager`) enforces sequential GPU execution (`gpu_compute_lock`) across all resident models to prevent simultaneous compute starvation.
- **Control Interface**: Dedicated **Workhorse WebUI** (Port 8090) provides real-time VRAM telemetry, high-watermark spike logging, rich media previews, and container lifecycle controls.

## Recent Updates (June 16-17, 2026)
- **Vision Upgrade**: Migrated to CyberRealistic Pony XL with permanent quality-tag injection.
- **LLM Latency Fix**: Standardized on official llama.cpp ROCm server for guaranteed hardware acceleration.
- **VRAM Optimization**: Slashed footprint to 15.5GB while maintaining full 32k context and 1080p generation.
- **Pop-Out Feature**: Added full-size image overlays to the Control Panel.
