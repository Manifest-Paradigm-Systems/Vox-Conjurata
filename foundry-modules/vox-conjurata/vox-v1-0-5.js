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

function resolveIsMonster(actor) {
    if (!actor) return false;
    if (actor.type === "character") return false;
    const strictMonsterKeywords = ["dragon", "skeleton", "zombie", "undead", "fiend", "demon", "devil", "beast", "monster", "aberration", "xulgath", "zulgath", "goblin", "kobold", "orc", "troll", "ogre", "bugbear", "ghoul", "lich"];
    const nameLower = actor.name?.toLowerCase() ?? "";
    if (strictMonsterKeywords.some(kw => nameLower.includes(kw))) return true;
    if (actor.type === "npc") {
        const creatureType = actor.system?.details?.type?.value?.toLowerCase() ?? "";
        return creatureType !== "humanoid";
    }
    return false;
}

function resolveActiveToken(isGM) {
    if (typeof canvas === 'undefined' || !canvas.tokens) return null;
    const hoveredToken = canvas.tokens.placeables?.find(t => t.hover);
    if (hoveredToken && hoveredToken.actor) {
        if (isGM || hoveredToken.actor.isOwner) return hoveredToken;
    }
    const controlledToken = canvas.tokens.controlled?.[0];
    return (controlledToken && controlledToken.actor) ? controlledToken : null;
}

// ==========================================
// 2b. EARLY AUDIO INITIALIZATION
// ==========================================
(async function initAudio() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        try { globalThis.voxState.mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm; codecs=opus" }); } 
        catch (e) { globalThis.voxState.mediaRecorder = new MediaRecorder(stream); }
        
        globalThis.voxState.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) globalThis.voxState.audioChunks.push(event.data);
        };
        globalThis.voxState.mediaRecorder.onstop = async () => { await processAndSendAudio(); };
        console.log("🎙️ Vox-Conjurata: Hardware microphone pipeline ready.");
    } catch (err) { console.error("❌ Vox Audio Fail:", err); }
})();

// ==========================================
// 3. KEYBINDING & KEYBOARD ENGINE
// ==========================================
function registerKeybindings() {
    if (globalThis.voxKeybindingsRegistered) return;
    globalThis.voxKeybindingsRegistered = true;
    console.log("🎙️ Vox-Conjurata: Registering settings and keybindings.");

    // Core Settings
    game.settings.register("vox-conjurata", "narratorVoice", {
        name: "Vox: Narrator Voice Profile",
        hint: "Default fallback voice.",
        scope: "world", config: true, type: String, default: "en-US-ChristopherNeural"
    });

    // Dummy keybindings for Foundry UI (actual logic in unified listener below)
    game.keybindings.register("vox-conjurata", "narratorPTT", { name: "Vox: Narrator PTT [Y]", editable: [{ key: "KeyY" }], onDown: () => {}, onUp: () => {} });
    game.keybindings.register("vox-conjurata", "puppeteerPTT", { name: "Vox: Puppeteer PTT [H]", editable: [{ key: "KeyH" }], onDown: () => {}, onUp: () => {} });
    game.keybindings.register("vox-conjurata", "playerPTT", { name: "Vox: Character PTT [I]", editable: [{ key: "KeyI" }], onDown: () => {}, onUp: () => {} });
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
            console.log(`🎙️ Vox-Conjurata: Key Down [${code}]`);
            try { playAudio("sounds/lock.wav", 0.1); } catch (e) {}

            if (code === "KeyY" && game.user.isGM) {
                globalThis.voxState.narratorActive = true;
                globalThis.voxState.activeSpeakerName = "Narrator";
                globalThis.voxState.activeActorId = "narrator";
                globalThis.voxState.activeIsMonster = false;
                startRecording("vox-conjurata-gm-narrate-mic");
                statusMessage("Narrator Mic [Y]: OPEN", true);
            } 
            else if (code === "KeyH" && game.user.isGM) {
                const selectedToken = resolveActiveToken(true);
                if (!selectedToken) {
                    ui.notifications.warn("❌ Puppeteer: Hover over or select an NPC token first!");
                    activeKeys.delete(code);
                    return;
                }
                globalThis.voxState.puppetActive = true;
                globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
                globalThis.voxState.activeActorId = selectedToken.actor?.id || "unknown";
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(selectedToken.actor);
                startRecording("vox-conjurata-gm-puppet-mic");
                statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            }
            else if (code === "KeyI") {
                const selectedToken = resolveActiveToken(false);
                const speakerActor = selectedToken?.actor || game.user.character;
                globalThis.voxState.playerActive = true;
                globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
                globalThis.voxState.activeActorId = speakerActor?.id || game.user.id;
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(speakerActor);
                startRecording("vox-conjurata-player-mic");
                statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            }
        }
    });

    window.addEventListener("keyup", (event) => {
        const code = event.code;
        if (activeKeys.has(code)) {
            activeKeys.delete(code);
            console.log(`🎙️ Vox-Conjurata: Key Up [${code}]`);
            if (code === "KeyY") { globalThis.voxState.narratorActive = false; stopRecording(); statusMessage("Narrator Mic [Y]: CLOSED", false); } 
            else if (code === "KeyH") { globalThis.voxState.puppetActive = false; stopRecording(); statusMessage(`Puppeteer Mic [H] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false); }
            else if (code === "KeyI") { globalThis.voxState.playerActive = false; stopRecording(); statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false); }
        }
    });
})();

// ==========================================
// 4. MODULE LIFECYCLE
// ==========================================
const scannedScenes = new Set();
const ingestedActors = new Set();

async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready) return;
    for (let token of canvas.tokens.placeables) {
        if (!token.actor || ingestedActors.has(token.actor.id)) continue;
        ingestedActors.add(token.actor.id);
        const actor = token.actor;
        const actorData = {
            actorId: actor.id, name: actor.name,
            lore: actor.system.details?.biography?.value || actor.system.description?.value || "No bio available.",
            stats: { race: actor.system.details?.race || "Unknown", level: actor.system.details?.level?.value || 0 },
            artPath: actor.img, isMonster: resolveIsMonster(actor)
        };
        try { await fetch(globalThis.voxState.ingestEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(actorData) }); } 
        catch (e) {}
    }
}

async function onReady() {
    if (globalThis.voxReadyExecuted) return;
    globalThis.voxReadyExecuted = true;
    if (game.user.isGM) await scanActiveSceneTokens();
}

if (typeof game !== 'undefined' && game.ready) onReady();
else Hooks.once("ready", onReady);
Hooks.on("canvasReady", async () => { if (game.user.isGM) await scanActiveSceneTokens(); });

// ==========================================
// 5. CHAT SKINNING & TERMINAL ENGINE
// ==========================================
Hooks.on("chatMessage", (chatLog, message, chatData) => {
    if (message.startsWith("/vox ")) {
        const args = message.slice(5).split(" ");
        const command = args[0].toLowerCase();
        const param = args.slice(1).join(" ");
        
        handleVoxCommand(command, param);
        return false; // Prevent message from being sent to regular chat
    }
});

async function handleVoxCommand(command, param) {
    if (!game.user.isGM) return;
    
    const activeToken = resolveActiveToken(true);
    
    if (command === "forge" || command === "voice") {
        if (!activeToken) {
            ui.notifications.warn("⚠️ Vox Terminal: Select or hover over a token first!");
            return;
        }
        
        const description = command === "voice" ? param : "";
        statusMessage(`VOX TERMINAL: Manually forging seed for ${activeToken.actor.name}...`, true);
        
        const actorData = {
            actorId: activeToken.actor.id,
            name: activeToken.actor.name,
            lore: activeToken.actor.system.details?.biography?.value || "",
            artPath: activeToken.actor.img,
            isMonster: resolveIsMonster(activeToken.actor),
            customDescription: description // NEW: Pass custom description to backend
        };

        try {
            const response = await fetch(globalThis.voxState.ingestEndpoint + "?force_refresh=true", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(actorData)
            });
            const data = await response.json();
            if (data.status === "created") {
                statusMessage(`✅ VOX TERMINAL: Voice forged for ${activeToken.actor.name}!`, false);
                ui.notifications.info(`🎙️ Vox: Voice seed created for ${activeToken.actor.name}`);
            }
        } catch (e) {
            console.error("❌ Vox Terminal Error:", e);
            statusMessage("❌ VOX TERMINAL: Forge failed. Check logs.", false);
        }
    }
    else if (command === "status") {
        statusMessage("VOX TERMINAL: Querying system telemetry...", true);
        // This will trigger a response from the orchestrator handled in the usual pipeline
        fetch("/api/status").then(r => r.json()).then(data => {
            ChatMessage.create({
                speaker: { alias: "Vox System Console" },
                content: `<div style="font-family: monospace; font-size: 0.8rem; background: #1a1a1a; color: #00ff00; padding: 10px; border-radius: 5px; border: 1px solid #333;">
                    <strong>SYSTEM TELEMETRY</strong><br/>
                    ---------------------<br/>
                    VRAM: ${data.vram_used_gb?.toFixed(2) || "???"} / ${data.vram_total_gb?.toFixed(2) || "32"} GB<br/>
                    FOUNDRY: CONNECTED<br/>
                    COSYVOICE: WARM<br/>
                    VISION: HOT (STANDBY)
                </div>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        });
    }
    else {
        ui.notifications.info("Available commands: /vox forge, /vox voice [desc], /vox status");
    }
}

Hooks.on("renderChatMessageHTML", (message, html, data) => {
    const voxType = message.getFlag("vox-conjurata", "type");
    if (!voxType) return;
    const jHtml = $(html);
    const content = jHtml.find(".message-content");
    const originalContent = content.html();

    if (voxType === "narration") {
        jHtml.addClass("vox-conjurata-card vox-conjurata-narration");
        jHtml.empty().append(`<div class="narration-header"><i class="fas fa-book-open gold-icon"></i><span class="narration-title">SCENE DESCRIPTION</span><i class="fas fa-book-open gold-icon"></i></div><div class="message-content narration-text">${originalContent}</div>`);
    } else {
        const actor = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const actorName = actor?.name || message.speaker.alias || "Entity";
        const actorImg = actor?.img || "icons/svg/mystery-man.svg";
        const audioUrl = message.getFlag("vox-conjurata", "audioUrl");
        const engineName = message.getFlag("vox-conjurata", "engine") || "AI Engine";
        jHtml.addClass(`vox-conjurata-card vox-conjurata-${voxType}`);
        const audioHtml = audioUrl ? `<div class="vox-conjurata-audio-container"><button class="vox-conjurata-audio-play-btn" data-audio-src="${audioUrl}"><i class="fas fa-volume-high"></i> Play Generated Voice</button></div>` : "";
        jHtml.empty().append(`<div class="puppet-layout"><img class="puppet-avatar" src="${actorImg}"/><div class="puppet-body"><header class="message-header"><span class="sender">${actorName}</span><span class="${voxType}-tag">${voxType.toUpperCase()}</span></header><div class="message-content">${originalContent}</div>${audioHtml}</div></div>`);
        if (audioUrl) jHtml.find(".vox-conjurata-audio-play-btn").on("click", () => { playAudio(audioUrl, 1.0); });
    }
});

function playAudio(audioUrl, volume = 1.0) {
    if (!audioUrl) return;
    try {
        const audio = new Audio(audioUrl);
        audio.volume = volume;
        audio.play();
    } catch (err) { console.error("🎙️ Vox-Conjurata: Audio fail", err); }
}

function startRecording(micType) {
    if (globalThis.voxState.mediaRecorder?.state === "inactive") {
        globalThis.voxState.audioChunks = [];
        globalThis.voxState.activeMicType = micType;
        globalThis.voxState.mediaRecorder.start(250);
    }
}

function stopRecording() {
    if (globalThis.voxState.mediaRecorder?.state === "recording") globalThis.voxState.mediaRecorder.stop();
}

function statusMessage(text, isOpen) {
    const recipients = game.user.isGM ? ChatMessage.getWhisperRecipients("GM") : [];
    ChatMessage.create({
        speaker: { alias: "Vox Core" },
        content: `<div style="display: flex; align-items: center; gap: 8px;"><span style="font-size: 1.2rem;">${isOpen ? '🎙️' : '🤫'}</span><div><strong>${text}</strong></div></div>`,
        whisper: recipients.length > 0 ? recipients.map(u => u.id) : []
    });
}

async function processAndSendAudio() {
    const chunks = globalThis.voxState.audioChunks;
    if (chunks.length === 0) return;
    const audioBlob = new Blob(chunks, { type: "audio/webm" });
    const { activeMicType, activeActorId, activeSpeakerName, activeIsMonster } = globalThis.voxState;

    const formData = new FormData();
    formData.append("audio_blob", audioBlob, "voice_capture.webm");
    formData.append("metadata", JSON.stringify({ activeSpeakerName, actorId: activeActorId, micType: activeMicType, isMonster: activeIsMonster, userId: game.user.id }));

    console.log(`📦 Vox-Conjurata: Processing voice for [${activeSpeakerName}]`);

    try {
        const response = await fetch(globalThis.voxState.voiceConversionEndpoint, { method: "POST", body: formData });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        
            const { transcription, audio_data, engine, voxType } = data;
            if (audio_data) playAudio(audio_data, 1.0);
            
            // Foundry V12 / PF2e 8.x strictly requires numeric style for ChatMessage
            const message = await ChatMessage.create({ 
                content: transcription,
                style: 2, // 2 is In-Character (IC)
                speaker: { actor: activeActorId === 'narrator' ? null : activeActorId, alias: activeSpeakerName },
                flags: { "vox-conjurata": { type: voxType, audioUrl: audio_data, engine: engine } } 
            });

            if (message && canvas.ready) {
                const tokenId = message.speaker.token || canvas.tokens.placeables.find(t => t.actor?.id === activeActorId)?.id;
                const token = canvas.tokens.get(tokenId);
                if (token && typeof canvas.bubbles?.say === 'function') canvas.bubbles.say(token, transcription);
            }
        }
    } catch (err) { console.error("❌ Vox-Conjurata Pipeline fail:", err); }
}

globalThis.startRecording = startRecording;
globalThis.stopRecording = stopRecording;
globalThis.statusMessage = statusMessage;
globalThis.playAudio = playAudio;
globalThis.resolveActiveToken = resolveActiveToken;
globalThis.resolveIsMonster = resolveIsMonster;

if (typeof game !== 'undefined' && game.keybindings) registerKeybindings();
else Hooks.once("init", registerKeybindings);
