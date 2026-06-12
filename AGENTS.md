# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: ALL CORE INFERENCE MODELS (Text, HD Vision, Audio, Image Generation) MUST BE GPU RESIDENT OR HOT-SWAPPABLE. `MiniCPM-V 2.6` (Vision Reader) is the default resident "Monster Sight" analyzer.
- **Vision Gen (Always-On)**: `DreamShaper XL Turbo` is now resident in **4-bit Quantization** (~4.5GB VRAM). Base resolution is 1344x768 upscaled to 1080p (Lanczos).
- **Vision Reader (Always-On)**: `MiniCPM-V 2.6` remains resident (~4.8GB VRAM), enabling persistent environmental awareness even during cinematic renders.
- **LLM Context**: `Kunou 14B` (Qwen 2.5) utilizes an **8-bit (FP8) KV Cache** with a restored **32,768 token** context window.
- **Audio Stack**: 
    - **Voice Core**: `VoxCPM2` (2.1B) resident (~4.2GB VRAM) for sub-200ms dialogue generation.
    - **SFX Engine**: `Stable Audio Open` resident on primary GPU (~0.8GB) for maximum responsiveness (~1.5s renders).
    - **Music Gen**: `Stable Audio` utilizes **JIT (Just-In-Time) loading** (~4GB burst) to prevent base-load OOM.
    - **STT (Speech-to-Text)**: `Faster-Whisper` is offloaded to the **Secondary 2GB GPU** (`ROCR_VISIBLE_DEVICES=1`) to reclaim primary VRAM.
- **System Memory**: Total RX 7900 base load is **~22.8 GB**, leaving a **~9.2 GB safety buffer** for multi-table support and OS/Foundry spikes.
- **Control Interface**: Dedicated **Workhorse WebUI** (Port 8090) provides real-time VRAM telemetry, high-watermark spike logging, and container lifecycle controls (START/STOP/IDLE).

## Recent Updates (June 11-12, 2026)
- **Deployment**: Deployed unified Workhorse Control Panel with direct manual query support for all generation services.
- **Architecture**: Optimized whole-system VRAM footprint to enable "Always-On" Vision without service swapping.
- **Safety**: Moved micro-services to secondary GPU and implemented JIT eviction for Music engine.
- **Resolution**: Standardized all cinematic generation to native 1080p output.
