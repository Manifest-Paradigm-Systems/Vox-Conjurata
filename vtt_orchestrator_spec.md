# Vox Conjurata: Core AI Pipeline & VTT Orchestrator Specification

This specification establishes the structural logic for a local, context-aware AI pipeline. It links the LLM's conversational/narrative engine (`Qwen2.5/Kunou`) directly with an automated background rendering pipeline (`Pony SDXL / Flux.1`) triggered by a headless middleware orchestrator inside Foundry VTT.

```text
[Foundry VTT Hook / Chat Input] ──► [Intent Detection & Token Filter]
│
┌────────────────────────┴────────────────────────┐
[Conversational Mode]                             [Narrator / Action Mode]
│                                                 │
(Render Text Only)                               (Assemble Context Arrays)
│                                                 │
[Update UI Chat Log]                              [Extract IP-Adapter/ControlNet]
│
[Execute Branching VTT Render]
│
[Push Art to VTT Interface]
```

---

## 1. Intent Detection & Token-Formatting Logic

To eliminate corporate safety alignment behavior, hallucinated data patterns, and runaway VRAM consumption, the local LLM relies on rigid structural tokens and conditional tag extraction.

### A. The Token Safety Anchor
The model payload must strictly utilize **ChatML** format wrappers. Physical actions are contained within asterisks (`*action*`) and spoken dialogue inside quotation marks (`"dialogue"`). Framing inputs explicitly as an *offline tabletop creative writing sandbox* keeps the model inside its high-fidelity roleplay datasets, completely bypassing corporate safety-refusal layers.

### B. Conditional Image Ingestion
The system does not process heavy image rendering pipelines on rapid conversational turns. It uses **Intent-Driven Optional Blocks** parsed via your custom frontend:
* **Conversational Mode:** Processes standard back-and-forth dialogue. The LLM outputs **only** the `<Narrative>` block. The orchestrator skips the Stable Diffusion API layer completely.
* **Narrator / Scene Transition Mode:** Activated by hitting the "Narrator" shortcut or entering an explicit scene alteration. The LLM processes the physical variables and generates an extra `<ImagePrompt>` tag block containing comma-separated Danbooru weights. The presence of this tag acts as the short-circuit hook for the renderer.

---

## 2. Global Environment & Style Constraints (Hardcoded)

To maximize generation speed and maintain absolute visual cohesion across a campaign, the text-encoder weights are locked directly within the Orchestrator code. The LLM never manually invents quality modifiers.

* **Positive Prefix (Pony SDXL Anchors):** `score_9, score_8_up, classic D&D dark fantasy oil painting, gritty realism, hyper-detailed textures, moody atmospheric lighting,`
* **Speed Override LoRA:** `<lora:dmd2_pony_4step_speed:0.8>` (or equivalent verified Pony-Lightning model matrix).
* **Negative Prompt Matrix:** `score_4, score_5, score_6, anime, cartoon, source_anime, comic, 3d render, looking at viewer, static portrait, standard pose, symmetry, clean skin, modern clothing, rubber suit, costume, low quality`
* **Performance Parameters:** 
  * **Steps:** 4 passes
  * **CFG Scale:** 1.5 (Strict Maximum to avoid artifacting on accelerated inference)
  * **Sampler/Scheduler:** `Euler a` or `DPM++ SDE` with `sgm_uniform`

---

## 3. Multimodal Data Ingestion (Context Gathering)

The moment an `<ImagePrompt>` tag is generated or a system automation action (`[GEN_SCENE]`, `/strike`, `/cast`) intercepts a turn, the orchestrator halts text processing to scrape three database context tracks:

### Track A: Identity Array (IP-Adapter Mapping)
* **Active Actor UUID** $\rightarrow$ Extracts the absolute path to the master high-resolution character face/NPC image file (e.g., `oceana_face.png`).
* **Targeted Entity UUID** $\rightarrow$ Extracts the corresponding master monster/NPC reference file path (e.g., `barrow_wight_ref.png`).

### Track B: Geometry Array (ControlNet Spatial Mapping)
* The system isolates a 5x5 grid bounding box centering the active actor and target on the canvas.
* Background asset tile data within this box is cropped, excluding player/monster token art.
* This background crop is automatically fed directly into **ControlNet Depth** or **Canny** to establish spatial and architectural layout guidance.

### Track C: Narrative Foundation Track
* **Master Scenery Description:** A persistent, global scene string defined by the DM upon map initialization (e.g., *"Inside a dark, damp cavern with a roaring waterfall on the left side, slick wet limestone floors"*). This value is programmatically appended to all incoming user queries to enforce absolute environmental permanence across images.

---

## 4. Branching Combat Pipeline Logic

The orchestrator reads the distance and item metadata arrays straight out of the active Foundry action to execute one of two distinct generation tracks:

```text
                      [Foundry Token Distance Check]
                                     │
                ┌────────────────────┴────────────────────┐
       (Distance <= 1 Grid)                      (Distance > 1 Grid)
                │                                         │
     ▼ Melee Pipeline Path                     ▼ Ranged Pipeline Path
┌───────────────────────────────┐         ┌───────────────────────────────┐
│  - Regional Prompter Split    │         │  - Simultaneous Two-Pass API  │
│  - Horizontal Split (1:1)     │         │  - Panel 1: Attacker Macro    │
│  - Left: Actor IP-Adapter     │         │  - Panel 2: Target Reaction   │
│  - Right: Target IP-Adapter   │         │  - Deep Field Blurring        │
└───────────────────────────────┘         └───────────────────────────────┘
```

### Path A: Melee Combat Pipeline (Unified Single Frame)
* **Condition:** Target distance is less than or equal to 1 Grid Square or contains a `melee` descriptor tag.
* **Execution:** Generates a unified single-image canvas utilizing **Regional Prompter** extension hooks to eliminate concept bleeding across subjects.
* **Payload Structure:** Splits the viewport vertically (`Horizontal` split, `1:1` grid ratio).
  * **Left Column:** Binds Player IP-Adapter + Dynamic action text (*"Player lunging forward, executing a heavy weapon strike"*).
  * `BREAK` token string insertion.
  * **Right Column:** Binds Target IP-Adapter + Dynamic reaction text (*"Target monster reeling back, attempting to parry"*).

### Path B: Ranged Combat Pipeline (Diptych Comic-Book Panels)
* **Condition:** Target distance is greater than 1 Grid Square, contains a `ranged` descriptor tag, or registers a projectile/spell cast.
* **Execution:** Fires two simultaneous or fast sequential 4-step API calls to render two separate camera angles, entirely eliminating "ant-sized" token scaling issues over vast distances.
* **Frame 1 (The Release Panel):** 
  * *Camera Layout:* Over-the-shoulder macro focus perspective looking past the attacker's weapon/hands.
  * *Content:* Attacker's active stance in razor-sharp focus; global scenery assets (e.g., waterfall) naturally blurred out via `telephoto lens perspective, deep field compression`.
  * *Adapter Bind:* Attacker's primary face IP-Adapter.
* **Frame 2 (The Impact Panel):**
  * *Camera Layout:* Tight reaction/impact frame focusing squarely on the target entity.
  * *Content:* Target actively reeling from terminal kinetic impact or spell flash effects in the close foreground; attacking player visible only as a distant silhouette.
  * *Adapter Bind:* Target's primary creature IP-Adapter.

---

## 5. Automation JSON Payload Schema

The middleware builds out the final automation package using this structural template before sending it over local sockets to the rendering endpoint:

```json
{
  "endpoint": "/sdapi/v1/txt2img",
  "request_factory": {
    "base_params": {
      "steps": 4,
      "cfg_scale": 1.5,
      "width": 1216,
      "height": 832,
      "sampler_name": "Euler a",
      "scheduler": "sgm_uniform"
    },
    "conditional_routing": "if (action.range <= 1) { compile_melee_json() } else { compile_ranged_diptych_json() }"
  }
}
```

---

## 6. Code Generation Instructions for Gemini CLI

When passing this document into your script generator or local Gemini terminal wrapper, enforce these three structural invariants:

1. **Absolute Paths:** All lookups for character assets, background scene crops, and IP-Adapter references must resolve via absolute path maps calculated directly from active Foundry token data objects.
2. **VRAM Saturation Failsafe:** Implement a continuous monitoring check on the local GPU memory footprint. If VRAM availability falls below a strict threshold safety limit, the orchestrator must automatically kill the image generation path and fall back to standard text rendering, preventing system page-filing overhead.
3. **Socket Synchronization:** Program the final output images to write instantly to a temporary runtime cache folder inside the VTT user-data directory. The system must immediately push a WebSocket notification to update the client-side UI chat card layout in real time.
