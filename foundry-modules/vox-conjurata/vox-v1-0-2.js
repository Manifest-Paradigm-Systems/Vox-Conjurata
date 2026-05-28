/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Consolidates Telemetry, Chat Skinning, and Hardware PTT Engine.
 */

// ==========================================
// 1. TELEMETRY BRIDGE & SELF-HEALING
// ==========================================
(function() {
    try {
        const ORCHESTRATOR_URL = "http://127.0.0.1:8080/api/v1/diagnostics/logs";

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
    mediaRecorder: null,
    audioChunks: [],
    sttEndpoint: `http://${voxHost}:5000/v1/audio/transcriptions`,
    voiceConversionEndpoint: `http://${voxHost}:8080/api/voice-conversion`,
    ingestEndpoint: `http://${voxHost}:8080/api/ingest-actor`
};

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
    }
})();

// ==========================================
// 3. KEYBINDING REGISTRATION (INIT)
// ==========================================
Hooks.once("init", () => {
    console.log("🎙️ Vox-Conjurata: init hook fired.");
    
    game.settings.register("vox-conjurata", "narratorVoice", {
        name: "Vox: Narrator Voice Profile",
        hint: "Choose the Microsoft Neural voice profile for narration failovers.",
        scope: "world",
        config: true,
        type: String,
        default: "en-US-ChristopherNeural",
        choices: { "en-US-ChristopherNeural": "en-US-ChristopherNeural" }
    });

    // Y: Narrator PTT (GM)
    game.keybindings.register("vox-conjurata", "narratorPTT", {
        name: "Vox: Narrator Push-to-Talk",
        editable: [{ key: "KeyY" }],
        onDown: () => {
            console.log("🎙️ Vox-Conjurata: Narrator Key Down");
            try { if (game.audio) game.audio.play({src: "sounds/lock.wav", volume: 0}, false); } catch (err) {}
            if (!game.user.isGM || globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = true;
            globalThis.voxState.activeSpeakerName = "Narrator";
            globalThis.voxState.activeActorId = "narrator";
            startRecording("vox-conjurata-gm-narrate-mic");
            statusMessage("Narrator Mic [Y]: OPEN", true);
        },
        onUp: () => {
            console.log("🎙️ Vox-Conjurata: Narrator Key Up");
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
            console.log("🎭 Vox-Conjurata: Puppeteer Key Down");
            try { if (game.audio) game.audio.play({src: "sounds/lock.wav", volume: 0}, false); } catch (err) {}
            if (!game.user.isGM || globalThis.voxState.puppetActive) return;
            const selectedToken = canvas.tokens.controlled[0];
            if (!selectedToken) {
                ui.notifications.warn("❌ Puppeteer: Select an NPC token first!");
                return;
            }
            globalThis.voxState.puppetActive = true;
            globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
            globalThis.voxState.activeActorId = selectedToken.actor?.id || "unknown";
            startRecording("vox-conjurata-gm-puppet-mic");
            statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
        },
        onUp: () => {
            console.log("🎭 Vox-Conjurata: Puppeteer Key Up");
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
            console.log("👤 Vox-Conjurata: Character Key Down");
            try { if (game.audio) game.audio.play({src: "sounds/lock.wav", volume: 0}, false); } catch (err) {}
            if (globalThis.voxState.playerActive) return;
            const speakerActor = canvas.tokens.controlled[0]?.actor || game.user.character;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
            globalThis.voxState.activeActorId = speakerActor?.id || game.user.id;
            startRecording("vox-conjurata-player-mic");
            statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
        },
        onUp: () => {
            console.log("👤 Vox-Conjurata: Character Key Up");
            if (!globalThis.voxState.playerActive) return;
            globalThis.voxState.playerActive = false;
            stopRecording();
            statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): CLOSED`, false);
        }
    });
});

// ==========================================
// 4. MODULE LIFECYCLE (READY & SCENE SCAN)
// ==========================================
async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready) return;
    console.log("📡 Vox-Conjurata: Scanning active scene tokens for pre-session ingestion...");
    const tokens = canvas.tokens?.placeables || [];
    for (const token of tokens) {
        if (!token.actor) continue;
        const actor = token.actor;
        
        // Evaluate if the actor is a monster based on type or keywords
        const monsterTypes = ["undead", "fiend", "dragon", "monstrosity", "aberration"];
        const keywords = ["Guard", "Monster", "Warrior"];
        const actorType = actor.system.details?.type?.value?.toLowerCase() || "";
        const nameMatch = keywords.some(k => actor.name.includes(k));
        const folderMatch = actor.folder?.name ? keywords.some(k => actor.folder.name.includes(k)) : false;
        const typeMatch = monsterTypes.some(t => actorType.includes(t));
        
        const is_monster = !actor.hasPlayerOwner && (typeMatch || nameMatch || folderMatch);
        
        const actorData = {
            actorId: actor.id, name: actor.name,
            lore: actor.system.details?.biography?.value || actor.system.description?.value || "No bio available.",
            stats: { race: actor.system.details?.race || "Unknown", alignment: actor.system.details?.alignment || "Neutral", level: actor.system.details?.level?.value || 0 },
            artPath: actor.img, isMonster: is_monster
        };
        console.log(`📦 Vox-Conjurata: Pre-session scraping for ${actorData.name} (Monster: ${is_monster})...`);
        try {
            await fetch(globalThis.voxState.ingestEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(actorData) });
        } catch (err) { console.error(`❌ Vox-Conjurata: Failed to ingest token metadata for ${actorData.name}:`, err); }
    }
}

Hooks.once("ready", async () => {
    console.log("vox-conjurata | System initialized.");
    
    if (game.user.isGM) {
        try {
            const response = await fetch("http://127.0.0.1:8080/api/v1/narrators/voices");
            if (response.ok) {
                const voices = await response.json();
                const choices = {};
                voices.forEach(v => { choices[v] = v; });
                game.settings.settings.get("vox-conjurata.narratorVoice").choices = choices;
                if (ui.activeWindow?.id === "client-settings") ui.activeWindow.render();
                console.log("📡 Vox-Conjurata: Loaded dynamic Edge-TTS voices roster.");
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
});

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
    
    // Evaluate if the actor is a monster based on type or keywords
    const monsterTypes = ["undead", "fiend", "dragon", "monstrosity", "aberration"];
    const keywords = ["Guard", "Monster", "Warrior"];
    const actorType = actor.system.details?.type?.value?.toLowerCase() || "";
    const nameMatch = keywords.some(k => actor.name.includes(k));
    const folderMatch = actor.folder?.name ? keywords.some(k => actor.folder.name.includes(k)) : false;
    const typeMatch = monsterTypes.some(t => actorType.includes(t));
    
    const is_monster = !actor.hasPlayerOwner && (typeMatch || nameMatch || folderMatch);
    
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
            jHtml.find(".vox-conjurata-audio-play-btn").on("click", (e) => { try { if (game.audio) game.audio.play({src: audioUrl, volume: 1.0}, false); else new Audio(audioUrl).play(); } catch (err) {} });
            const timeSinceCreated = Date.now() - message.timestamp;
            if (timeSinceCreated < 5000) {
                console.log("🎙️ Vox-Conjurata: Auto-playing generated voice...");
                try { if (game.audio) game.audio.play({src: audioUrl, volume: 1.0}, false); else new Audio(audioUrl).play(); } catch (err) {}
            }
        }
    }
});

// ==========================================
// 6. HELPER FUNCTIONS
// ==========================================
function startRecording(micType) {
    try {
        if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
            globalThis.voxState.audioChunks = [];
            globalThis.voxState.activeMicType = micType;
            globalThis.voxState.mediaRecorder.start();
            console.log(`🎙️ Vox-Conjurata: Recording started (${micType}).`);
        } else {
            console.warn("🎙️ Vox-Conjurata: startRecording called but MediaRecorder not ready.", {
                exists: !!globalThis.voxState.mediaRecorder,
                state: globalThis.voxState.mediaRecorder?.state
            });
        }
    } catch (e) { console.error("🎙️ Vox-Conjurata: Failed to start recording:", e); }
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
    const actor = canvas.tokens.controlled[0]?.actor || game.user.character;

    // Evaluate if the actor is a monster based on type or keywords
    const monsterTypes = ["undead", "fiend", "dragon", "monstrosity", "aberration"];
    const keywords = ["Guard", "Monster", "Warrior"];
    const actorType = actor?.system.details?.type?.value?.toLowerCase() || "";
    const nameMatch = actor ? keywords.some(k => actor.name.includes(k)) : false;
    const folderMatch = actor?.folder?.name ? keywords.some(k => actor.folder.name.includes(k)) : false;
    const typeMatch = monsterTypes.some(t => actorType.includes(t));
    
    const is_monster = actor && !actor.hasPlayerOwner && (typeMatch || nameMatch || folderMatch);

    const formData = new FormData();
    formData.append("audio_blob", audioBlob, "voice_capture.webm");
    formData.append("metadata", JSON.stringify({ activeSpeakerName: globalThis.voxState.activeSpeakerName, actorId: globalThis.voxState.activeActorId, micType: micType, isMonster: !!is_monster, userId: game.user.id }));

    console.log(`📦 Vox-Conjurata: Sending audio blob (${audioBlob.size} bytes) to Orchestrator context: [${micType}] for Actor: [${globalThis.voxState.activeActorId}] (Monster: ${is_monster})`);

    try {
        const response = await fetch(globalThis.voxState.voiceConversionEndpoint, { method: "POST", body: formData });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        
        // BUG FIX: Correctly extract audio_data from the response
        if (data.status === "success") {
            const transcription = data.transcription;
            const enrichment = data.enrichment;
            const voxType = data.voxType;
            const engine = data.engine;
            // Support both audioUrl and audio_data field names for robustness
            const audioUrl = data.audio_data || data.audioUrl; 

            if (audioUrl) { 
                console.log(`🎙️ Vox-Conjurata: Auto-playing generated voice via ${engine}...`); 
                try { if (game.audio) game.audio.play({src: audioUrl, volume: 1.0}, false); else new Audio(audioUrl).play(); } catch (err) {} 
            }
            await ChatMessage.create({ content: transcription, speaker: ChatMessage.getSpeaker({ actor: canvas.tokens.controlled[0]?.actor || game.user.character, alias: globalThis.voxState.activeSpeakerName }), flags: { "vox-conjurata": { type: voxType, emotionalResonance: enrichment.emotional_resonance, vocalDelivery: enrichment.vocal_delivery_prompt, audioUrl: audioUrl, engine: engine } } });
        }
    } catch (err) { console.error("❌ Vox-Conjurata: Pipeline failure:", err); }
}

// Expose functions globally for cross-script access
globalThis.startRecording = startRecording;
globalThis.stopRecording = stopRecording;
globalThis.statusMessage = statusMessage;
globalThis.processAndSendAudio = processAndSendAudio;
