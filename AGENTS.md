# Vox Conjurata Engineering & System Layout Rules

- **Compute Boundaries**: ALL CORE INFERENCE MODELS (Text, HD Vision, Audio, Image Generation) MUST BE GPU RESIDENT OR HOT-SWAPPABLE. `MiniCPM-V 2.6` (Vision Reader) is the default resident vision handler but can be dynamically evicted to make room for heavy burst services.
- **Hot-Swap Protocol**: The Orchestrator manages a dynamic VRAM state machine via `resource_manager.py`. When a burst task (SDXL Image Gen or Music Gen) is enqueued, the Orchestrator stops `vox-vision-reader` (~4.8GB reclaimed), starts the burst container, executes the task, and restores the reader upon completion.
- **Asynchronous Pipeline**: All multi-modal triggers (Spells, Effects, Music) are enqueued in an asynchronous Task Queue with 15s semantic deduplication to ensure zero-latency player interaction.
- **VRAM Headroom**: Baseline load is optimized via hot-swapping, ensuring total VRAM utilization remains within the 32GB boundary even during concurrent Image and SFX generation. A 5.5GB+ buffer is reserved for OS and VTT canvas rendering.
- **PTT Priority**: Live speech/vocal conversions take absolute priority over background rendering.
- **SELinux Compliance**: Podman rootless mounts must append `:Z` for proper host folder label inheritance.

## Local LLM (Qwen) Memory & Token Boundaries
* **Engine Quantization:** Qwen text inference uses `Qwen2.5-7B-Instruct` (Q4_K_M / 4-bit) with an active 8-bit (FP8) quantized KV Cache.
* **Context Budgeting:** The active runtime context length is expanded to **8,192 tokens**, enabling deep lore and complex historical record integration.
* **Autonomous NPC Brain**: EVA-Qwen acts as the cognitive engine for all NPCs, monsters, and deities. It ingests identity, local lore, world events, and personal memories to generate contextually aware responses.
* **Memory Management**: Long-term state is persisted via dynamic **Memory Journal** flushes back to Foundry VTT, ensuring continuity across sessions.

---

## Session Summary: 2026-06-06 (Hot-Swap & NPC Brain)

### 1. Hot-Swap VRAM Management
- **Implementation**: Deployed `resource_manager.py` to orchestrate container lifecycles via the host's Podman socket.
- **Task Queueing**: Built an asynchronous task queue with semantic debouncing to manage multi-modal triggers (SFX, Images, Music).
- **HUD Interface**: Injected a draggable **Event Queue HUD** into Foundry VTT with stackable progress bars for real-time tracking of backend processing.

### 2. Autonomous NPC Brain Protocol
- **Implementation**: Deployed the `/api/v1/npc-brain/reply` architecture for sub-second, context-aware AI responses.
- **GM Controls**: Injected `VOX-ACTOR` (Personality) and `VOX-VOICE` (TTS) checkboxes into Foundry actor sheets for granular GM override.
- **Persistent Memory**: Enhanced the `Chronicle` system to automatically summarize interactions and update NPC-specific memory journals in real-time.
- **Validation**: Established a 41-case test matrix and a self-healing diagnostic engine (`run_diagnostics.py`) to maintain system integrity.

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
