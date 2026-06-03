/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Consolidates Telemetry, Chat Skinning, and Hardware PTT Engine.
 */
console.log("🚀 Vox-Conjurata: Script evaluation started.");

// ==========================================
// 1. TELEMETRY BRIDGE & SELF-HEALING
// ==========================================
(function() {
    try {
        const ORCHESTRATOR_URL = "/api/v1/diagnostics/logs";

        const shipLog = async (data) => {
            try {
                const payload = typeof data === 'string' ? { type: "info", message: data } : data;
                await fetch(ORCHESTRATOR_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
            } catch (e) {}
        };

        window.onerror = (message, source, lineno, colno, error) => {
            shipLog({ type: "exception", message: message, source: source, lineno: lineno, error: error?.stack || "No stack trace" });
        };

        const originalConsoleError = console.error;
        console.error = (...args) => {
            try {
                shipLog({
                    type: "console-error",
                    message: args.map(arg => {
                        try { return typeof arg === 'object' ? JSON.stringify(arg) : String(arg); } 
                        catch (e) { return "[Unserializable Object]"; }
                    }).join(' '),
                    source: "console.error override"
                });
            } catch (err) {}
            originalConsoleError.apply(console, args);
        };

        console.log("📡 Vox-Conjurata: Telemetry Bridge Active.");
        shipLog({ type: "startup", message: "Client-side module loaded and telemetry bridge active." });
    } catch (e) {
        console.warn("⚠️ Vox-Conjurata: Telemetry Bridge failed to initialize.", e);
    }
})();

// ==========================================
// 2. GLOBAL STATE & CONFIGURATION
// ==========================================
const voxHost = window.location.hostname || "127.0.0.1";
globalThis.voxState = globalThis.voxState || { 
    narratorActive: false, 
    puppetActive: false,
    playerActive: false,
    activeSpeakerName: "",
    activeMicType: "", 
    activeActorId: "", 
    activeIsMonster: false,
    mediaRecorder: null,
    audioChunks: [],
    sttEndpoint: "/api/v1/audio/transcriptions",
    voiceConversionEndpoint: "/api/voice-conversion",
    ingestEndpoint: "/api/ingest-actor"
};

/**
 * resolveIsMonster(actor)
 * ─────────────────────────────────────────────────────────────────────────────
 * Single authoritative source of truth for monster/humanoid routing.
 *
 * Priority order (first match wins):
 *  1. actor.type === "character"  → always PC, never monster.
 *  2. actor.type === "npc"        → always NPC; treat as monster unless the
 *     PF2e creature-type value is explicitly "humanoid".
 *  3. Fallback keyword scan on name (Dragon, Skeleton, Guard, Warrior…)
 *     used only when actor.type is neither "character" nor "npc" (rare).
 *
 * Reasoning: actor.type is set by Foundry at document creation and is always
 * reliable. hasPlayerOwner can return false for unassigned PCs (e.g. freshly
 * placed tokens), causing false positives. Keyword/folder heuristics are
 * unreliable when token data hasn't fully hydrated.
 */
function resolveIsMonster(actor) {
    if (!actor) return false;

    // 1. PCs are never monsters
    if (actor.type === "character") return false;

    // 2. Specific beast/monster race keywords in the name override everything else.
    //    This ensures tokens named "Zulgath Warrior" or "Goblin Scout" get routed
    //    to the monster voice engine (Fish Speech) even though they are mechanically humanoids in PF2e.
    const strictMonsterKeywords = [
        "dragon", "skeleton", "zombie", "undead", "fiend", "demon", "devil", 
        "beast", "monster", "aberration", "xulgath", "zulgath", "goblin", 
        "kobold", "orc", "troll", "ogre", "bugbear", "ghoul", "lich"
    ];
    const nameLower = actor.name?.toLowerCase() ?? "";
    if (strictMonsterKeywords.some(kw => nameLower.includes(kw))) {
        return true;
    }

    // 3. For standard NPCs, check creature type
    if (actor.type === "npc") {
        const creatureType = actor.system?.details?.type?.value?.toLowerCase() ?? "";
        if (creatureType === "humanoid") return false;
        return true;  // non-humanoid NPC (beast, undead, construct, etc.)
    }

    // 4. Fallback for non-standard actor types (vehicles, hazards, etc.)
    const fallbackKeywords = ["guard", "warrior"];
    return fallbackKeywords.some(kw => nameLower.includes(kw));
}

/**
 * resolveActiveToken(isGM)
 * ─────────────────────────────────────────────────────────────────────────────
 * Unifies mouse hover and selection states to find the target token.
 * Enables the GM to speak as an NPC simply by hovering over it.
 */
function resolveActiveToken(isGM) {
    if (typeof canvas === 'undefined' || !canvas.tokens) return null;
    
    // 1. Hover target (priority)
    const hoveredToken = canvas.tokens.placeables?.find(t => t.hover);
    if (hoveredToken && hoveredToken.actor) {
        if (isGM || hoveredToken.actor.isOwner) {
            return hoveredToken;
        }
    }
    
    // 2. Selection fallback
    const controlledToken = canvas.tokens.controlled?.[0];
    if (controlledToken && controlledToken.actor) {
        return controlledToken;
    }
    
    return null;
}

// ==========================================
// 2b. EARLY AUDIO INITIALIZATION (FIX)
// ==========================================
(async function initAudio() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.error("❌ Vox Audio Fail: Secure context required.");
        if (typeof ui !== 'undefined' && ui.notifications) {
            ui.notifications.error("❌ Vox Audio Fail: Secure context (HTTPS or localhost) required for microphone access!");
        } else {
            Hooks.once("ready", () => {
                ui.notifications.error("❌ Vox Audio Fail: Secure context (HTTPS or localhost) required for microphone access!");
            });
        }
        return;
    }
    try {
        console.log("🎙️ Vox-Conjurata: Requesting microphone access...");
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        try { 
            globalThis.voxState.mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm; codecs=opus" }); 
        } catch (e) { 
            globalThis.voxState.mediaRecorder = new MediaRecorder(stream); 
        }
        
        globalThis.voxState.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) globalThis.voxState.audioChunks.push(event.data);
        };
        globalThis.voxState.mediaRecorder.onstop = async () => { await processAndSendAudio(); };
        console.log("🎙️ Vox-Conjurata: Hardware microphone pipeline ready.");
    } catch (err) {
        console.error("❌ Vox Audio Fail:", err);
        if (typeof ui !== 'undefined' && ui.notifications) {
            ui.notifications.error(`❌ Vox Audio Fail: Microphone access error: ${err.message || err}`);
        } else {
            Hooks.once("ready", () => {
                ui.notifications.error(`❌ Vox Audio Fail: Microphone access error: ${err.message || err}`);
            });
        }
    }
})();

// ==========================================
// 3. KEYBINDING REGISTRATION (INIT)
// ==========================================
function registerKeybindings() {
    if (globalThis.voxKeybindingsRegistered) return;
    console.log("🎙️ Vox-Conjurata: Registering settings and keybindings.");
    globalThis.voxKeybindingsRegistered = true;
    
    // Register settings
    try {
        game.settings.register("vox-conjurata", "narratorVoice", {
            name: "Vox: Narrator Voice Profile",
            hint: "Choose the Microsoft Neural voice profile for narration failovers.",
            scope: "world",
            config: true,
            type: String,
            default: "en-US-ChristopherNeural",
            choices: { "en-US-ChristopherNeural": "en-US-ChristopherNeural" }
        });
    } catch (e) { console.error("🎙️ Vox-Conjurata: Failed to register settings:", e); }

    // Register Keybindings
    try {
        // Y: Narrator PTT (GM)
        game.keybindings.register("vox-conjurata", "narratorPTT", {
            name: "Vox: Narrator Push-to-Talk",
            editable: [{ key: "KeyY" }],
            onDown: () => {
                console.log("🎙️ Vox-Conjurata: Narrator Key Down [Y]");
                try { playAudio("sounds/lock.wav", 0.2); } catch (err) {}
                if (!game.user.isGM || globalThis.voxState.narratorActive) return;
                globalThis.voxState.narratorActive = true;
                globalThis.voxState.activeSpeakerName = "Narrator";
                globalThis.voxState.activeActorId = "narrator";
                startRecording("vox-conjurata-gm-narrate-mic");
                statusMessage("Narrator Mic [Y]: OPEN", true);
            },
            onUp: () => {
                console.log("🎙️ Vox-Conjurata: Narrator Key Up [Y]");
                if (!game.user.isGM || !globalThis.voxState.narratorActive) return;
                globalThis.voxState.narratorActive = false;
                stopRecording();
                statusMessage("Narrator Mic [Y]: CLOSED", false);
            }
        });

        // H: Puppeteer PTT (GM)
        game.keybindings.register("vox-conjurata", "puppeteerPTT", {
            name: "Vox: Puppeteer Push-to-Talk",
            editable: [{ key: "KeyH" }],
            onDown: () => {
                console.log("🎭 Vox-Conjurata: Puppeteer Key Down [H]");
                try { playAudio("sounds/lock.wav", 0.2); } catch (err) {}
                if (!game.user.isGM || globalThis.voxState.puppetActive) return;
                const selectedToken = resolveActiveToken(true);
                if (!selectedToken) {
                    ui.notifications.warn("❌ Puppeteer: Hover over or select an NPC token first!");
                    return;
                }
                globalThis.voxState.puppetActive = true;
                globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
                globalThis.voxState.activeActorId = selectedToken.actor?.id || "unknown";
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(selectedToken.actor);
                startRecording("vox-conjurata-gm-puppet-mic");
                statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            },
            onUp: () => {
                console.log("🎭 Vox-Conjurata: Puppeteer Key Up [H]");
                if (!game.user.isGM || !globalThis.voxState.puppetActive) return;
                globalThis.voxState.puppetActive = false;
                stopRecording();
                statusMessage(`Puppeteer Mic [H] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false);
            }
        });

        // I: Character PTT (All)
        game.keybindings.register("vox-conjurata", "playerPTT", {
            name: "Vox: Character Push-to-Talk",
            editable: [{ key: "KeyI" }],
            onDown: () => {
                console.log("👤 Vox-Conjurata: Character Key Down [I]");
                try { playAudio("sounds/lock.wav", 0.2); } catch (err) {}
                if (globalThis.voxState.playerActive) return;
                const selectedToken = resolveActiveToken(false);
                const speakerActor = selectedToken?.actor || game.user.character;
                globalThis.voxState.playerActive = true;
                globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
                globalThis.voxState.activeActorId = speakerActor?.id || game.user.id;
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(speakerActor);
                startRecording("vox-conjurata-player-mic");
                statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            },
            onUp: () => {
                console.log("👤 Vox-Conjurata: Character Key Up [I]");
                if (!globalThis.voxState.playerActive) return;
                globalThis.voxState.playerActive = false;
                stopRecording();
                statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false);
            }
        });
    } catch (e) {
        console.error("🎙️ Vox-Conjurata: Keybinding registration failed:", e);
    }
}

// Ensure functions are global for cross-script access
globalThis.startRecording = startRecording;
globalThis.stopRecording = stopRecording;
globalThis.statusMessage = statusMessage;
globalThis.playAudio = playAudio;
globalThis.resolveActiveToken = resolveActiveToken;
globalThis.resolveIsMonster = resolveIsMonster;

// Register on init
Hooks.once("init", () => {
    registerKeybindings();
});

// ==========================================
// 3b. GLOBAL KEYBOARD LISTENER FALLBACKS (FAIL-SAFE)
// ==========================================
window.addEventListener("keydown", (event) => {
    // Safety check: ignore events if typing in input fields or textareas
    const target = event.target;
    if (!target) return;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable || target.closest(".editor-container") || target.closest(".prosemirror")) {
        return;
    }
    
    // Y Key Down (Narrator GM PTT)
    if (event.code === "KeyY" || event.key === "y" || event.key === "Y") {
        if (typeof game !== 'undefined' && game.user && game.user.isGM) {
            console.log("🎙️ Vox-Conjurata Fallback: Key Y Down caught globally");
            if (globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = true;
            globalThis.voxState.activeSpeakerName = "Narrator";
            globalThis.voxState.activeActorId = "narrator";
            if (typeof startRecording === 'function') startRecording("vox-conjurata-gm-narrate-mic");
            if (typeof statusMessage === 'function') statusMessage("Narrator Mic [Y]: OPEN (Fallback)", true);
        }
    }
    
    // H Key Down (Puppeteer GM PTT)
    if (event.code === "KeyH" || event.key === "h" || event.key === "H") {
        if (typeof game !== 'undefined' && game.user && game.user.isGM) {
            console.log("🎭 Vox-Conjurata Fallback: Key H Down caught globally");
            if (globalThis.voxState.puppetActive) return;
            const selectedToken = typeof resolveActiveToken === 'function' ? resolveActiveToken(true) : null;
            if (!selectedToken) {
                console.warn("🎭 Vox-Conjurata Fallback: No NPC token hovered/selected for [H]");
                return;
            }
            globalThis.voxState.puppetActive = true;
            globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
            globalThis.voxState.activeActorId = selectedToken.actor?.id || "unknown";
            globalThis.voxState.activeIsMonster = typeof resolveIsMonster === 'function' ? !!resolveIsMonster(selectedToken.actor) : false;
            if (typeof startRecording === 'function') startRecording("vox-conjurata-gm-puppet-mic");
            if (typeof statusMessage === 'function') statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN (Fallback)`, true);
        }
    }
    
    // I Key Down (Character PTT)
    if (event.code === "KeyI" || event.key === "i" || event.key === "I") {
        if (typeof game !== 'undefined' && game.user) {
            console.log("👤 Vox-Conjurata Fallback: Key I Down caught globally");
            if (globalThis.voxState.playerActive) return;
            const selectedToken = typeof resolveActiveToken === 'function' ? resolveActiveToken(false) : null;
            const speakerActor = selectedToken?.actor || game.user.character || null;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
            globalThis.voxState.activeActorId = speakerActor?.id || game.user.id;
            globalThis.voxState.activeIsMonster = typeof resolveIsMonster === 'function' ? !!resolveIsMonster(speakerActor) : false;
            if (typeof startRecording === 'function') startRecording("vox-conjurata-player-mic");
            if (typeof statusMessage === 'function') statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN (Fallback)`, true);
        }
    }
});

window.addEventListener("keyup", (event) => {
    // Y Key Up
    if (event.code === "KeyY" || event.key === "y" || event.key === "Y") {
        if (typeof game !== 'undefined' && game.user && game.user.isGM && globalThis.voxState.narratorActive) {
            console.log("🎙️ Vox-Conjurata Fallback: Key Y Up caught globally");
            globalThis.voxState.narratorActive = false;
            if (typeof stopRecording === 'function') stopRecording();
            if (typeof statusMessage === 'function') statusMessage("Narrator Mic [Y]: CLOSED (Fallback)", false);
        }
    }
    
    // H Key Up
    if (event.code === "KeyH" || event.key === "h" || event.key === "H") {
        if (typeof game !== 'undefined' && game.user && game.user.isGM && globalThis.voxState.puppetActive) {
            console.log("🎭 Vox-Conjurata Fallback: Key H Up caught globally");
            globalThis.voxState.puppetActive = false;
            if (typeof stopRecording === 'function') stopRecording();
            if (typeof statusMessage === 'function') statusMessage(`Puppeteer Mic [H] (${globalThis.voxState.activeSpeakerName}): CLOSED (Fallback)`, false);
        }
    }
    
    // I Key Up
    if (event.code === "KeyI" || event.key === "i" || event.key === "I") {
        if (globalThis.voxState.playerActive) {
            console.log("👤 Vox-Conjurata Fallback: Key I Up caught globally");
            globalThis.voxState.playerActive = false;
            if (typeof stopRecording === 'function') stopRecording();
            if (typeof statusMessage === 'function') statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): CLOSED (Fallback)`, false);
        }
    }
});

// ==========================================
// 4. MODULE LIFECYCLE (READY & SCENE SCAN)
// ==========================================
const scannedScenes = new Set();

async function scanActiveSceneBattlemap() {
    if (!game.user.isGM || !canvas.ready || !canvas.scene) return;

    const sceneId = canvas.scene.id;
    const bgImage = canvas.scene.background?.src;

    // Persistent flag survives page reload — prevents duplicate placement
    if (!bgImage || scannedScenes.has(sceneId) || canvas.scene.getFlag("vox-conjurata", "scanned")) return;

    console.log(`🗺️ Vox-Conjurata: New scene detected (${canvas.scene.name}). Triggering spatial analysis...`);
    scannedScenes.add(sceneId);

    try {
        const response = await fetch("/api/scan-battlemap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ imagePath: bgImage, sceneId: sceneId })
        });
        const data = await response.json();
        if (data.status === "success") {
            console.log("🗺️ Vox-Conjurata: Spatial analysis complete. Data received:", data.data);
            ui.notifications.info(`🗺️ Vox-Conjurata: Spatial analysis complete for ${canvas.scene.name}.`);

            // Place walls, lights, and ambient sounds from the scan contract
            await placeScanEmbedded(data.data);
        }
    } catch (err) {
        console.error("❌ Vox-Conjurata: Failed to trigger battlemap scan:", err);
    }
}

/**
 * Convert a normalized coordinate (0-1) to scene pixel position using
 * the current canvas scene dimensions.
 */
function scanNormToPx(nx, ny) {
    const dim = canvas.scene.dimensions;
    return {
        x: nx * dim.sceneWidth + dim.sceneX,
        y: ny * dim.sceneHeight + dim.sceneY,
    };
}

/**
 * Place walls, lights, and ambient sounds parsed from the scan contract
 * onto the current scene as embedded documents.
 */
async function placeScanEmbedded(contract) {
    if (!contract) return;

    const scene = canvas.scene;
    const { walls = [], lights = [], sound_sources = [] } = contract;

    // --- 1. Walls (including doors) ---
    const wallDocs = walls
        .filter(w => Array.isArray(w.c) && w.c.length === 4)
        .map(w => {
            const p0 = scanNormToPx(w.c[0], w.c[1]);
            const p1 = scanNormToPx(w.c[2], w.c[3]);
            return {
                c: [Math.round(p0.x), Math.round(p0.y), Math.round(p1.x), Math.round(p1.y)],
                move: 20, sight: 20, sound: 20, light: 20,
                dir: 0,
                door: Math.min(2, Math.max(0, parseInt(w.door, 10) || 0)),
                ds: Math.min(2, Math.max(0, parseInt(w.ds, 10) || 0)),
            };
        });

    if (wallDocs.length) {
        await scene.createEmbeddedDocuments("Wall", wallDocs);
        console.log(`🗺️  Placed ${wallDocs.length} walls`);
    }

    // --- 2. Lights ---
    const lightDocs = lights
        .filter(l => l.x !== undefined && l.y !== undefined)
        .map(l => {
            const pos = scanNormToPx(l.x, l.y);
            const animType = l.animation || null;
            return {
                x: Math.round(pos.x),
                y: Math.round(pos.y),
                elevation: 0,
                walls: true,
                vision: false,
                config: {
                    dim: Math.max(0, parseFloat(l.dim) || 6),
                    bright: Math.max(0, parseFloat(l.bright) || 3),
                    color: String(l.color || "#ffaa55"),
                    alpha: 0.5,
                    luminosity: 0.5,
                    animation: {
                        type: animType,
                        speed: 5,
                        intensity: 5,
                    },
                },
            };
        });

    if (lightDocs.length) {
        await scene.createEmbeddedDocuments("AmbientLight", lightDocs);
        console.log(`🗺️  Placed ${lightDocs.length} lights`);
    }

    // --- 3. Ambient sounds ---
    const soundDocs = sound_sources
        .filter(s => s.x !== undefined && s.y !== undefined && s.audio_path)
        .map(s => {
            const pos = scanNormToPx(s.x, s.y);
            return {
                x: Math.round(pos.x),
                y: Math.round(pos.y),
                elevation: 0,
                radius: Math.max(0, parseFloat(s.radius_units) || 8),
                path: String(s.audio_path),
                volume: 0.5,
                repeat: true,
                walls: true,
                easing: true,
                darkness: { min: 0, max: 1 },
                effects: {
                    base: { type: "", intensity: 5 },
                    muffled: { type: "", intensity: 5 },
                },
            };
        });

    if (soundDocs.length) {
        await scene.createEmbeddedDocuments("AmbientSound", soundDocs);
        console.log(`🗺️  Placed ${soundDocs.length} ambient sounds`);
    }

    // Mark scene as scanned so a page reload doesn't re-place duplicates
    await scene.setFlag("vox-conjurata", "scanned", true);
    console.log(`🗺️  Battlemap auto-population complete for "${scene.name}"`);
}

async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready) return;

    for (let token of canvas.tokens.placeables) {
        if (!token.actor) continue;
        const actor = token.actor;
        
        // Resolve monster status via shared authoritative helper
        const is_monster = resolveIsMonster(actor);
        
        const actorData = {
            actorId: actor.id, name: actor.name,
            lore: actor.system.details?.biography?.value || actor.system.description?.value || "No bio available.",
            stats: { race: actor.system.details?.race || "Unknown", alignment: actor.system.details?.alignment || "Neutral", level: actor.system.details?.level?.value || 0 },
            artPath: actor.img, isMonster: is_monster
        };
        console.log(`📦 Vox-Conjurata: Scraping metadata for ${actorData.name} (Monster: ${is_monster})...`);
        try {
            await fetch(globalThis.voxState.ingestEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(actorData) });
        } catch (err) { console.error(`❌ Vox-Conjurata: Failed to ingest token metadata for ${actorData.name}:`, err); }
    }
    
    // Also scan the battlemap itself
    await scanActiveSceneBattlemap();
}

async function onReady() {
    if (globalThis.voxReadyExecuted) return;
    console.log("vox-conjurata | System initialized.");
    globalThis.voxReadyExecuted = true;
    
    if (game.user.isGM) {
        try {
            const response = await fetch("/api/v1/narrators/voices");
            if (response.ok) {
                const voices = await response.json();
                const choices = {};
                voices.forEach(v => { choices[v] = v; });
                game.settings.settings.get("vox-conjurata.narratorVoice").choices = choices;
                if (ui.activeWindow?.id === "client-settings") ui.activeWindow.render();
                console.log("📡 Vox-Conjurata: Edge-TTS suppressed \u2014 narrator voice driven by CosyVoice seed.");
            }
        } catch (e) {
            console.error("❌ Vox-Conjurata: Failed to load dynamic narrator voices.", e);
        }

        const legacy = ["Vox: Toggle Narrator", "Vox: Toggle Puppeteer", "Vox: Toggle Character"];
        for (const name of legacy) {
            const existing = game.macros.filter(m => m.name === name);
            for (const m of existing) await m.delete();
        }
        
        // Scan current active scene tokens on startup
        await scanActiveSceneTokens();
    }
}

if (typeof game !== 'undefined' && game.ready) {
    onReady();
} else {
    Hooks.once("ready", () => {
        onReady();
    });
}

// Scan tokens whenever a scene completes loading/rendering on canvas
Hooks.on("canvasReady", async () => {
    if (game.user.isGM) {
        await scanActiveSceneTokens();
    }
});

// ==========================================
// 4b. TOKEN SPAWNING & DATA SCRAPE
// ==========================================
Hooks.on("createToken", async (tokenDoc, options, userId) => {
    if (!game.user.isGM || !tokenDoc.actor) return;
    const actor = tokenDoc.actor;
    
    // Resolve monster status via shared authoritative helper
    const is_monster = resolveIsMonster(actor);
    
    const actorData = {
        actorId: actor.id, name: actor.name,
        lore: actor.system.details?.biography?.value || actor.system.description?.value || "No bio available.",
        stats: { race: actor.system.details?.race || "Unknown", alignment: actor.system.details?.alignment || "Neutral", level: actor.system.details?.level?.value || 0 },
        artPath: actor.img, isMonster: is_monster
    };
    console.log(`📦 Vox-Conjurata: Scraping metadata for ${actorData.name} (Monster: ${is_monster})...`);
    try {
        await fetch(globalThis.voxState.ingestEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(actorData) });
    } catch (err) { console.error("❌ Vox-Conjurata: Failed to ingest actor metadata:", err); }
});

// ==========================================
// 5. CHAT SKINNING ENGINE (V13/V14 COMPATIBLE)
// ==========================================
Hooks.on("renderChatMessageHTML", (message, html, data) => {
    const voxType = message.getFlag("vox-conjurata", "type");
    if (!voxType) return;
    const jHtml = $(html);
    const content = jHtml.find(".message-content");
    const originalContent = content.html();

    if (voxType === "narration") {
        jHtml.addClass("vox-conjurata-card vox-conjurata-narration");
        jHtml.empty().append(`<div class="narration-header"><i class="fas fa-book-open gold-icon"></i><span class="narration-title">SCENE DESCRIPTION</span><i class="fas fa-book-open gold-icon"></i></div><div class="message-content narration-text">${originalContent}</div>`);
    } 
    else if (voxType === "puppet" || voxType === "ai" || voxType === "player") {
        const actor = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const actorName = actor?.name || message.speaker.alias || "Entity";
        const actorImg = actor?.img || "icons/svg/mystery-man.svg";
        const audioUrl = message.getFlag("vox-conjurata", "audioUrl");
        const engineName = message.getFlag("vox-conjurata", "engine") || "AI Engine";
        
        let skinClass = `vox-conjurata-${voxType}`;
        let tag = voxType === "puppet" ? "GM PUPPET" : (voxType === "ai" ? "AI CORE" : "TRANSCRIPT");
        let icon = voxType === "ai" ? "fa-brain" : (voxType === "player" ? "fa-waveform-lines" : "fa-mask");
        
        jHtml.addClass(`vox-conjurata-card ${skinClass}`);
        let contextLine = "";
        if (voxType === "ai") contextLine = `<div class="ai-context-line"><i class="fas fa-reply"></i> In response to <strong>${message.getFlag("vox-conjurata", "responseTo") || "Player"}</strong> <span style="margin-left: auto; opacity: 0.5; font-size: 0.8em;">${engineName}</span></div>`;
        else if (voxType === "player") contextLine = `<div class="player-target-line"><i class="fas fa-comment-lines"></i> Speaking to <strong>${game.actors.get(message.getFlag("vox-conjurata", "targetActorId"))?.name || "NPC"}</strong> <span style="margin-left: auto; opacity: 0.5; font-size: 0.8em;">${engineName}</span></div>`;
        else if (voxType === "puppet") contextLine = `<div class="ai-context-line" style="background: none; border: none;"><span style="margin-left: auto; opacity: 0.5; font-size: 0.8em;">${engineName}</span></div>`;

        const audioHtml = audioUrl ? `<div class="vox-conjurata-audio-container"><button class="vox-conjurata-audio-play-btn" data-audio-src="${audioUrl}"><i class="fas fa-volume-high"></i> Play Generated Voice</button></div>` : "";
        jHtml.empty().append(`${contextLine}<div class="puppet-layout"><img class="puppet-avatar ${voxType === 'ai' ? 'ai-border' : ''}" src="${actorImg}"/><div class="puppet-body"><header class="message-header ${voxType}-header"><span class="sender ${voxType}-name">${actorName}</span><span class="${voxType}-tag"><i class="fas ${icon}"></i> ${tag}</span></header><div class="message-content ${voxType}-text">${originalContent}</div>${audioHtml}</div></div>`);

        if (audioUrl) {
            // Wire up the manual replay button only.
            // processAndSendAudio() already plays audio immediately on the GM's
            // machine the moment the server responds. Auto-playing here causes
            // a double-play (and can trigger additional Edge TTS via AudioHelper).
            jHtml.find(".vox-conjurata-audio-play-btn").on("click", (e) => { playAudio(audioUrl, 1.0); });
        }
    }
});

// ==========================================
// 6. HELPER FUNCTIONS
// ==========================================
function playAudio(audioUrl, volume = 1.0) {
    if (!audioUrl) return;
    console.log(`🎙️ Vox-Conjurata: Playing audio: ${audioUrl} at volume ${volume}`);
    try {
        if (typeof AudioHelper !== "undefined" && typeof AudioHelper.play === "function") {
            AudioHelper.play({ src: audioUrl, volume: volume }, false);
            return;
        }
    } catch (err) {
        console.warn("🎙️ Vox-Conjurata: AudioHelper.play failed, falling back", err);
    }
    try {
        if (game.audio && typeof game.audio.play === "function") {
            game.audio.play(audioUrl);
            return;
        }
    } catch (err) {
        console.warn("🎙️ Vox-Conjurata: game.audio.play failed, falling back", err);
    }
    try {
        const audio = new Audio(audioUrl);
        audio.volume = volume;
        audio.play();
    } catch (err) {
        console.error("🎙️ Vox-Conjurata: HTML5 Audio play failed", err);
    }
}

function startRecording(micType) {
    try {
        if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
            globalThis.voxState.audioChunks = [];
            globalThis.voxState.activeMicType = micType;
            globalThis.voxState.mediaRecorder.start(250);
            console.log(`🎙️ Vox-Conjurata: Recording started (${micType}).`);
        } else {
            const reason = !globalThis.voxState.mediaRecorder 
                ? "Microphone recorder not initialized. Ensure secure context (HTTPS/localhost) and grant mic permissions." 
                : `Recorder state is "${globalThis.voxState.mediaRecorder.state}" (expected "inactive").`;
            console.warn("🎙️ Vox-Conjurata: startRecording called but MediaRecorder not ready.", reason);
            if (typeof ui !== 'undefined' && ui.notifications) {
                ui.notifications.error(`🎙️ Vox-Conjurata: Microphone not ready! ${reason}`);
            }
        }
    } catch (e) { 
        console.error("🎙️ Vox-Conjurata: Failed to start recording:", e);
        if (typeof ui !== 'undefined' && ui.notifications) {
            ui.notifications.error(`🎙️ Vox-Conjurata: Failed to start recording: ${e.message || e}`);
        }
    }
}

function stopRecording() {
    try { if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "recording") globalThis.voxState.mediaRecorder.stop(); }
    catch (e) { console.error("🎙️ Vox-Conjurata: Failed to stop recording:", e); }
}

function statusMessage(text, isOpen) {
    try {
        const recipients = game.user.isGM ? ChatMessage.getWhisperRecipients("GM") : [];
        ChatMessage.create({
            speaker: { alias: "Vox Core" },
            content: `<div style="display: flex; align-items: center; gap: 8px;"><span style="font-size: 1.2rem;">${isOpen ? '🎙️' : '🤫'}</span><div><strong>${text}</strong></div></div>`,
            whisper: recipients.length > 0 ? recipients.map(u => u.id) : []
        });
    } catch (e) { console.error("🎙️ Vox-Conjurata: Failed to create status message:", e); }
}

async function processAndSendAudio() {
    const chunks = globalThis.voxState.audioChunks;
    if (chunks.length === 0) return;
    const audioBlob = new Blob(chunks, { type: globalThis.voxState.mediaRecorder.mimeType || "audio/webm" });
    const micType = globalThis.voxState.activeMicType;
    
    // Unify state using locked-in details resolved at Key Down
    const is_monster = !!globalThis.voxState.activeIsMonster;
    const activeActorId = globalThis.voxState.activeActorId;
    const activeSpeakerName = globalThis.voxState.activeSpeakerName;

    const formData = new FormData();
    formData.append("audio_blob", audioBlob, "voice_capture.webm");
    formData.append("metadata", JSON.stringify({ 
        activeSpeakerName: activeSpeakerName, 
        actorId: activeActorId, 
        micType: micType, 
        isMonster: is_monster, 
        userId: game.user.id 
    }));

    console.log(`📦 Vox-Conjurata: Sending audio blob (${audioBlob.size} bytes) to Orchestrator context: [${micType}] for Actor: [${activeActorId}] (Monster: ${is_monster})`);

    try {
        const response = await fetch(globalThis.voxState.voiceConversionEndpoint, { method: "POST", body: formData });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        console.log("🎯 Vox-Conjurata: Orchestrator response received:", data);
        
        if (data.status === "success") {
            const transcription = data.transcription;
            const enrichment = data.enrichment || {};
            const voxType = data.voxType || "player";
            const engine = data.engine || "Unknown";
            const audioUrl = data.audio_data || data.audioUrl; 

            if (audioUrl) { 
                console.log(`🎙️ Vox-Conjurata: Auto-playing generated voice via ${engine}...`); 
                playAudio(audioUrl, 1.0);
            }
            
            // Unconditionally retrieve the actor using the locked-in activeActorId to prevent selection switch bugs
            const speakerActor = game.actors.get(activeActorId) || null;
            const speakerData = ChatMessage.getSpeaker({ 
                actor: speakerActor, 
                alias: activeSpeakerName 
            });

            console.log(`💬 Vox-Conjurata: Creating chat message for ${activeSpeakerName} (Type: ${voxType})`);

            const message = await ChatMessage.create({ 
                content: transcription, 
                type: CONST.CHAT_MESSAGE_TYPES.IC, // Use IC for bubbles
                speaker: speakerData, 
                flags: { 
                    "vox-conjurata": { 
                        type: voxType, 
                        emotionalResonance: enrichment.emotional_resonance, 
                        vocalDelivery: enrichment.vocal_delivery_prompt, 
                        audioUrl: audioUrl, 
                        engine: engine 
                    } 
                } 
            });

            // Trigger speech bubble explicitly
            const tokenId = message.speaker.token;
            if (canvas.ready && tokenId) {
                const token = canvas.tokens.get(tokenId) || canvas.tokens.placeables.find(t => t.id === tokenId);
                if (token) {
                    console.log(`🗯️ Vox-Conjurata: Triggering speech bubble for token ${token.name}`);
                    if (typeof canvas.bubbles?.say === 'function') {
                        canvas.bubbles.say(token, transcription);
                    }
                }
            }
        }
    } catch (err) { 
        console.error("❌ Vox-Conjurata: Pipeline failure:", err); 
        if (typeof ui !== 'undefined' && ui.notifications) {
            ui.notifications.error(`❌ Vox-Conjurata: Pipeline failure! Ensure the orchestrator is reachable. Error: ${err.message || err}`);
        }
    }
}

// Expose functions globally for cross-script access
globalThis.startRecording = startRecording;
globalThis.stopRecording = stopRecording;
globalThis.statusMessage = statusMessage;
globalThis.processAndSendAudio = processAndSendAudio;
globalThis.playAudio = playAudio;
globalThis.resolveActiveToken = resolveActiveToken;
globalThis.resolveIsMonster = resolveIsMonster;

// Aggressive Registration: Handle both early and late script loading
if (typeof game !== 'undefined' && game.keybindings) {
    console.log("🎙️ Vox-Conjurata: Game already initialized, registering immediately.");
    registerKeybindings();
} else {
    Hooks.once("init", () => {
        console.log("🎙️ Vox-Conjurata: Init hook fired, registering keybindings.");
        registerKeybindings();
    });
}
