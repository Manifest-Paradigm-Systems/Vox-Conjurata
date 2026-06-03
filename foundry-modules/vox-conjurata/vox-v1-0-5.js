/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Consolidates Telemetry, Chat Skinning, and Hardware PTT Engine.
 */
console.log("🚀 Vox-Conjurata: Script evaluation started.");

// ==========================================
// 0. TERMINAL ENGINE (BULLETPROOF INTERCEPT)
// ==========================================

// Global Command Handler
async function handleVoxCommand(command, param) {
    if (!game.user.isGM) return;
    const token = resolveActiveToken(true);
    
    if (command === "forge" || command === "voice") {
        if (!token) { ui.notifications.warn("⚠️ Vox Terminal: Select or hover a token!"); return; }
        const desc = command === "voice" ? param : "";
        statusMessage(`VOX TERMINAL: Re-forging voice for ${token.actor.name}...`, true);
        
        try {
            const resp = await fetch("/api/ingest-actor?force_refresh=true", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    actorId: token.actor.id, name: token.actor.name,
                    lore: token.actor.system.details?.biography?.value || "",
                    artPath: token.actor.img, isMonster: resolveIsMonster(token.actor),
                    customDescription: desc
                })
            });
            const data = await resp.json();
            if (data.status === "created") {
                statusMessage(`✅ VOX TERMINAL: Voice forged for ${token.actor.name}!`, false);
                ui.notifications.info(`🎙️ Vox: Voice seed created for ${token.actor.name}`);
            }
        } catch (e) { statusMessage("❌ VOX TERMINAL: Forge failed.", false); }
    }
    else if (command === "status") {
        fetch("/api/status").then(r => r.json()).then(data => {
            createVoxChatMessage({
                speaker: { alias: "Vox Console" },
                content: `<div style="font-family: monospace; background: #000; color: #0f0; padding: 10px; border: 1px solid #333; border-radius: 5px;">
                    <strong>SYSTEM TELEMETRY</strong><br/>
                    ---------------------<br/>
                    VRAM: ${data.vram_used_gb?.toFixed(1)}GB / 32GB<br/>
                    ENGINES: COSYVOICE, FISH<br/>
                    VISION: HOT
                </div>`,
                whisper: [game.user.id]
            });
        });
    }
    else {
        createVoxChatMessage({
            speaker: { alias: "Vox Help" },
            content: `<div style="background: #1a1a1a; color: #fff; padding: 12px; border-left: 4px solid #00ff00; border-radius: 5px;">
                <h3 style="color: #00ff00; margin: 0 0 10px 0;">🎙️ VOX COMMANDS</h3>
                <strong>/vox forge</strong> - AI auto-voice re-roll<br/>
                <strong>/vox voice [desc]</strong> - Manual voice description<br/>
                <strong>/vox status</strong> - Check system health
            </div>`,
            whisper: [game.user.id]
        });
    }
}

// Aggressive Command Intercept
(function() {
    // Intercept 1: standard hook
    Hooks.on("chatMessage", (chatLog, message, chatData) => {
        if (message.trim().toLowerCase().startsWith("/vox")) {
            console.log("🎙️ Vox: Hook intercepted command.");
            const parts = message.trim().split(/\s+/);
            handleVoxCommand(parts[1]?.toLowerCase() || "help", parts.slice(2).join(" "));
            return false;
        }
    });

    // Intercept 2: Prototype Monkeypatch (PF2e override)
    Hooks.once("ready", () => {
        if (typeof ChatLog === 'undefined') return;
        const originalProcess = ChatLog.prototype.processMessage;
        ChatLog.prototype.processMessage = function(message) {
            if (message.trim().toLowerCase().startsWith("/vox")) {
                console.log("🎙️ Vox: Prototype intercepted command.");
                const parts = message.trim().split(/\s+/);
                handleVoxCommand(parts[1]?.toLowerCase() || "help", parts.slice(2).join(" "));
                return;
            }
            return originalProcess.call(this, message);
        };
        console.log("🎙️ Vox: Terminal Engine fully armed.");
    });
})();

/**
 * createVoxChatMessage(data)
 * ─────────────────────────────────────────────────────────────────────────────
 * V12 / PF2e compatible message creator. 
 */
async function createVoxChatMessage(data) {
    // PF2e 8.x+ is extremely picky. Style MUST be a number, not a string.
    const messageData = {
        ...data,
        style: 2 // IC
    };
    
    // Some versions use 'type', some use 'style'. We try both separately to satisfy validation.
    try {
        const message = new ChatMessage(messageData);
        return await ChatMessage.create(message.toObject());
    } catch (err) {
        console.warn("🎙️ Vox: Standard creation failed, trying legacy fallback...");
        return await ChatMessage.create({ ...data, type: 2 });
    }
}

// ==========================================
// 1. TELEMETRY & UTILITIES
// ==========================================
(function() {
    try {
        const ORCHESTRATOR_URL = "/api/v1/diagnostics/logs";
        const shipLog = async (data) => {
            try { await fetch(ORCHESTRATOR_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(typeof data === 'string' ? { type: "info", message: data } : data) }); } catch (e) {}
        };
        const originalError = console.error;
        console.error = (...args) => {
            try { shipLog({ type: "error", message: args.join(' ') }); } catch (err) {}
            originalError.apply(console, args);
        };
    } catch (e) {}
})();

globalThis.voxState = globalThis.voxState || { 
    narratorActive: false, puppetActive: false, playerActive: false,
    activeSpeakerName: "", activeMicType: "", activeActorId: "", activeIsMonster: false,
    mediaRecorder: null, audioChunks: [],
    voiceConversionEndpoint: "/api/voice-conversion", ingestEndpoint: "/api/ingest-actor"
};

function resolveIsMonster(actor) {
    if (!actor) return false;
    if (actor.type === "character") return false;
    const keywords = ["dragon", "skeleton", "zombie", "undead", "fiend", "demon", "devil", "beast", "monster", "aberration", "xulgath", "zulgath", "goblin", "kobold", "orc", "troll", "ogre", "bugbear", "ghoul", "lich"];
    const name = actor.name?.toLowerCase() ?? "";
    if (keywords.some(kw => name.includes(kw))) return true;
    return actor.type === "npc" && actor.system?.details?.type?.value?.toLowerCase() !== "humanoid";
}

function resolveActiveToken(isGM) {
    if (typeof canvas === 'undefined' || !canvas.tokens) return null;
    const hovered = canvas.tokens.placeables?.find(t => t.hover);
    if (hovered?.actor && (isGM || hovered.actor.isOwner)) return hovered;
    const controlled = canvas.tokens.controlled?.[0];
    return controlled?.actor ? controlled : null;
}

// ==========================================
// 2. AUDIO & PTT ENGINE
// ==========================================
(async function initAudio() {
    if (!navigator.mediaDevices?.getUserMedia) return;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        globalThis.voxState.mediaRecorder = recorder;
        recorder.ondataavailable = (e) => { if (e.data.size > 0) globalThis.voxState.audioChunks.push(e.data); };
        recorder.onstop = async () => { await processAndSendAudio(); };
        console.log("🎙️ Vox-Conjurata: Mic ready.");
    } catch (err) { console.error("❌ Vox Audio Fail:", err); }
})();

function registerKeybindings() {
    if (globalThis.voxKeybindingsRegistered) return;
    globalThis.voxKeybindingsRegistered = true;
    game.keybindings.register("vox-conjurata", "narratorPTT", { name: "Vox: Narrator PTT [Y]", editable: [{ key: "KeyY" }], onDown: () => {}, onUp: () => {} });
    game.keybindings.register("vox-conjurata", "puppeteerPTT", { name: "Vox: Puppet PTT [H]", editable: [{ key: "KeyH" }], onDown: () => {}, onUp: () => {} });
    game.keybindings.register("vox-conjurata", "playerPTT", { name: "Vox: Char PTT [I]", editable: [{ key: "KeyI" }], onDown: () => {}, onUp: () => {} });
}

(function() {
    const activeKeys = new Set();
    window.addEventListener("keydown", (event) => {
        if (event.repeat || activeKeys.has(event.code)) return;
        const target = event.target;
        if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable || target.closest(".prosemirror"))) return;
        const code = event.code;
        if (code === "KeyY" || code === "KeyH" || code === "KeyI") {
            activeKeys.add(code);
            try { playAudio("sounds/lock.wav", 0.1); } catch (e) {}
            if (code === "KeyY" && game.user.isGM) {
                globalThis.voxState.narratorActive = true; globalThis.voxState.activeSpeakerName = "Narrator"; globalThis.voxState.activeActorId = "narrator"; globalThis.voxState.activeIsMonster = false;
                startRecording("vox-conjurata-gm-narrate-mic"); statusMessage("Narrator Mic [Y]: OPEN", true);
            } 
            else if (code === "KeyH" && game.user.isGM) {
                const t = resolveActiveToken(true);
                if (!t) { ui.notifications.warn("❌ Puppeteer: Select/hover NPC!"); activeKeys.delete(code); return; }
                globalThis.voxState.puppetActive = true; globalThis.voxState.activeSpeakerName = t.actor.name; globalThis.voxState.activeActorId = t.actor.id; globalThis.voxState.activeIsMonster = !!resolveIsMonster(t.actor);
                startRecording("vox-conjurata-gm-puppet-mic"); statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            }
            else if (code === "KeyI") {
                const t = resolveActiveToken(false); const a = t?.actor || game.user.character;
                globalThis.voxState.playerActive = true; globalThis.voxState.activeSpeakerName = a?.name || game.user.name; globalThis.voxState.activeActorId = a?.id || game.user.id; globalThis.voxState.activeIsMonster = !!resolveIsMonster(a);
                startRecording("vox-conjurata-player-mic"); statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            }
        }
    });
    window.addEventListener("keyup", (event) => {
        const code = event.code;
        if (activeKeys.has(code)) {
            activeKeys.delete(code);
            if (code === "KeyY") { globalThis.voxState.narratorActive = false; stopRecording(); statusMessage("Narrator Mic [Y]: CLOSED", false); } 
            else if (code === "KeyH") { globalThis.voxState.puppetActive = false; stopRecording(); statusMessage(`Puppeteer Mic [H] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false); }
            else if (code === "KeyI") { globalThis.voxState.playerActive = false; stopRecording(); statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false); }
        }
    });
})();

// ==========================================
// 3. MODULE LIFECYCLE
// ==========================================
const ingestedActors = new Set();
async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready) return;
    for (let token of canvas.tokens.placeables) {
        if (!token.actor || ingestedActors.has(token.actor.id)) continue;
        ingestedActors.add(token.actor.id);
        const a = token.actor;
        try { 
            ui.notifications.info(`🔍 Vox: Ingesting ${a.name}...`);
            await fetch(globalThis.voxState.ingestEndpoint, { 
                method: "POST", headers: { "Content-Type": "application/json" }, 
                body: JSON.stringify({
                    actorId: a.id, name: a.name, artPath: a.img, isMonster: resolveIsMonster(a),
                    lore: a.system.details?.biography?.value || a.system.description?.value || "No bio available.",
                    stats: { race: a.system.details?.race || "Unknown", level: a.system.details?.level?.value || 0 }
                }) 
            }); 
        } catch (e) {}
    }
}

async function onReady() {
    if (globalThis.voxReadyExecuted) return; globalThis.voxReadyExecuted = true;
    if (game.user.isGM) await scanActiveSceneTokens();
}

if (typeof game !== 'undefined' && game.ready) onReady(); else Hooks.once("ready", onReady);
Hooks.on("canvasReady", async () => { if (game.user.isGM) await scanActiveSceneTokens(); });

function playAudio(url, vol = 1.0) {
    if (!url) return; const a = new Audio(url); a.volume = vol; a.play().catch(() => {});
}

function startRecording(micType) {
    if (globalThis.voxState.mediaRecorder?.state === "inactive") {
        globalThis.voxState.audioChunks = []; globalThis.voxState.activeMicType = micType;
        globalThis.voxState.mediaRecorder.start(250);
    }
}

function stopRecording() { if (globalThis.voxState.mediaRecorder?.state === "recording") globalThis.voxState.mediaRecorder.stop(); }

function statusMessage(text, isOpen) {
    createVoxChatMessage({
        speaker: { alias: "Vox Core" },
        content: `<div style="display: flex; align-items: center; gap: 8px;"><span>${isOpen ? '🎙️' : '🤫'}</span><strong>${text}</strong></div>`,
        whisper: [game.user.id]
    });
}

async function processAndSendAudio() {
    const chunks = globalThis.voxState.audioChunks; if (chunks.length === 0) return;
    const blob = new Blob(chunks, { type: "audio/webm" });
    const { activeMicType, activeActorId, activeSpeakerName, activeIsMonster } = globalThis.voxState;
    const formData = new FormData(); formData.append("audio_blob", blob, "v.webm");
    formData.append("metadata", JSON.stringify({ activeSpeakerName, actorId: activeActorId, micType: activeMicType, isMonster: activeIsMonster, userId: game.user.id }));
    try {
        const r = await fetch(globalThis.voxState.voiceConversionEndpoint, { method: "POST", body: formData });
        const d = await r.json();
        if (d.status === "success") {
            const { transcription, audio_data, engine, voxType } = d;
            if (audio_data) playAudio(audio_data, 1.0);
            
            const message = await createVoxChatMessage({ 
                content: transcription,
                speaker: { actor: activeActorId === 'narrator' ? null : activeActorId, alias: activeSpeakerName },
                flags: { "vox-conjurata": { type: voxType, audioUrl: audio_data, engine: engine } } 
            });
            
            if (message && canvas.ready) {
                const tId = message.speaker.token || canvas.tokens.placeables.find(t => t.actor?.id === activeActorId)?.id;
                const token = canvas.tokens.get(tId);
                if (token && typeof canvas.bubbles?.say === 'function') canvas.bubbles.say(token, transcription);
            }
        }
    } catch (err) { console.error("❌ Vox Pipeline fail:", err); }
}

globalThis.startRecording = startRecording; globalThis.stopRecording = stopRecording; globalThis.statusMessage = statusMessage; globalThis.playAudio = playAudio; globalThis.resolveActiveToken = resolveActiveToken; globalThis.resolveIsMonster = resolveIsMonster;

if (typeof game !== 'undefined' && game.keybindings) registerKeybindings(); else Hooks.once("init", registerKeybindings);
