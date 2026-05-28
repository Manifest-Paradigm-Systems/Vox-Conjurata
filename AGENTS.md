# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: ALL CORE INFERENCE MODELS (Text, HD Vision, Audio, Image Generation) MUST BE GPU RESIDENT. No CPU-bound execution paths are permitted. `MiniCPM-V 2.6` is the primary vision handler. `SDXL` and `Stable Audio 3 Music` operate as JIT services, loading/evicting dynamically to conserve VRAM.
- **Vision Protocol**: `MiniCPM-V 2.6` remains permanently resident in GPU space for OCR and layout parsing. `SDXL` operates dynamically under a Just-In-Time (JIT) protocol, loading on-demand and immediately evicting itself after image generation to reclaim VRAM.
- **JIT Audio Protocol**: `Parler-TTS` (Voice Prototyping) and `Stable Audio 3 Music` operate as Just-In-Time services. They load on-demand (1.20 GB and 1.80 GB VRAM bursts respectively) and are immediately evicted after execution to reclaim VRAM.
- **GPU Resident Engines**: The GM Brain (`EVA-Qwen`), HD Vision (`MiniCPM-V 2.6`), Voice Suite (`CosyVoice`, `Fish Speech`), and `Stable Audio 3 SFX` are strictly resident in GPU VRAM. `SDXL` and `Stable Audio 3 Music` are dynamically managed via JIT.
- **VRAM Headroom**: Baseline load is optimized, recovering ~7.30 GB of VRAM when JIT services (SDXL and Stable Audio 3 Music) are idle. A 5.5GB+ buffer is reserved for OS and VTT canvas rendering on our 32GB hardware.
- **PTT Priority**: Live speech/vocal conversions take absolute priority over background rendering.
- **SELinux Compliance**: Podman rootless mounts must append `:Z` for proper host folder label inheritance.

## Local LLM (Qwen) Memory & Token Boundaries
* **Engine Quantization:** Qwen text inference uses `EVA-Qwen2.5-14B-v0.2-Q4_K_M.gguf` with an active 8-bit (FP8) quantized KV Cache.
* **Context Budgeting:** The active runtime context length is hard-capped at 4,096 tokens. Raw conversation histories must never exceed a rolling 20-message buffer.
* **State Management:** All campaign data exceeding the 20-message window must be purged from active LLM memory and tracked via VTT journal flushes.

---

## Session Summary: 2026-05-27 (Last 7 Hours)

### 1. Foundry VTT Containerization
- **Transition**: Migrated Foundry VTT from a host-resident process to a containerized service within the Podman stack.
- **Service**: Added `foundry-vtt` service to `compose.yaml` using `node:24-slim`.
- **Connectivity**: Updated `orchestrator` to use internal network resolution (`http://foundry-vtt:30000`).
- **Persistence**: Mounted existing `app` and `data` directories from the host to preserve all campaign state and settings.
- **Fixes**: Resolved a stale `options.json.lock` issue that was blocking container startup.

### 2. Automated Git Backup System
- **Implementation**: Deployed `git-failsafe.sh`, a background daemon for continuous workspace synchronization.
- **AI Integration**: Integrated `agy`/`gemini` CLI for automated, contextual commit message generation.
- **Resiliency**: Added failsafe logic for offline operations, rebase conflict avoidance, and network push retries.
- **Persistence**: Registered the backup daemon as a `systemd` user service (`git-backup.service`) with auto-restart policies.

### 3. Orchestrator & Voice Routing
- **Configuration**: Restored missing `FOUNDRY_API_URL` and `FOUNDRY_API_KEY` environment variables to the orchestrator.
- **Assets**: Added several new voice seed files (`.wav`) to the orchestrator's voice prototyping directory.
- **Network**: Removed `extra_hosts` dependencies in favor of native container DNS resolution for Foundry connectivity.
