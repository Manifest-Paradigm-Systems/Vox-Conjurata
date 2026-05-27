# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: The `stable-audio-3-small-sfx` model runs strictly on host CPU execution lanes (device="cpu") utilizing a dedicated 4GB execution buffer in System RAM, bypassing GPU paths.
- **GPU Resident Engines**: The core voice engines (`CosyVoice`, `Fish Speech`), `SDXL` image engine, and `Stable Audio 3 Music` engine remain resident in GPU VRAM (no model CPU offloading/onloading swaps).
- **VRAM Headroom**: Heavy engines (Qwen-2.5-7B, SDXL) run in 8-bit precision (INT8/FP8) to preserve a ~10GB VRAM buffer for multi-user concurrency spikes.
- **PTT Priority**: Live speech/vocal conversions take absolute priority over background rendering.
- **Edge-TTS Fallback**: Live narrator voices are dynamically loaded via Edge-TTS, which also acts as a VRAM deficit intercept (>18.0 GB usage).
- **SELinux Compliance**: Podman rootless mounts must append `:Z` for proper host folder label inheritance.
