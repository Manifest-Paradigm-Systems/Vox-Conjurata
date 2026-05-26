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
                // Ensure data is an object
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
globalThis.voxState = globalThis.voxState || { 
    narratorActive: false, 
    puppetActive: false,
    playerActive: false,
    activeSpeakerName: "",
    activeMicType: "", // Locked context for the current recording session
    mediaRecorder: null,
    audioChunks: [],
    // Points to the Orchestrator for the full pipeline
    voiceConversionEndpoint: "http://127.0.0.1:8080/api/voice-conversion" 
};

// ==========================================
// 3. KEYBINDING REGISTRATION (INIT)
// ==========================================
Hooks.once("init", () => {
    console.log("🎙️ Vox-Conjurata: init hook fired.");
    
    // Y: Narrator PTT (GM)
    game.keybindings.register("vox-conjurata", "narratorPTT", {
        name: "Vox: Narrator Push-to-Talk",
        editable: [{ key: "KeyY" }],
        onDown: () => {
            console.log("🎙️ Vox-Conjurata: Narrator Key Down");
            if (!game.user.isGM) {
                console.warn("🎙️ Vox-Conjurata: Attempted Narrator Mic usage by non-GM.");
                return;
            }
            if (globalThis.voxState.narratorActive) return;

            globalThis.voxState.narratorActive = true;
            globalThis.voxState.activeSpeakerName = "Narrator";
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
            if (!game.user.isGM) return;
            if (globalThis.voxState.puppetActive) return;

            const selectedToken = canvas.tokens.controlled[0];
            if (!selectedToken) {
                ui.notifications.warn("❌ Puppeteer: Select an NPC token first!");
                return;
            }
            globalThis.voxState.puppetActive = true;
            globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
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
            if (globalThis.voxState.playerActive) return;
            const speakerActor = canvas.tokens.controlled[0]?.actor || game.user.character;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
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
// 4. AUDIO ENGINE & LIFECYCLE (READY)
// ==========================================
Hooks.once("ready", async () => {
    console.log("vox-conjurata | System initialized.");
    
    // Initialize Audio
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.error("❌ Vox Audio Fail: Secure context (HTTPS/Localhost) required for microphone access.");
    } else {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            try {
                globalThis.voxState.mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm; codecs=opus" });
            } catch (e) {
                globalThis.voxState.mediaRecorder = new MediaRecorder(stream);
            }
            
            globalThis.voxState.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) globalThis.voxState.audioChunks.push(event.data);
            };

            globalThis.voxState.mediaRecorder.onstop = async () => {
                await processAndSendAudio();
            };
            console.log("🎙️ Vox-Conjurata: Hardware microphone pipeline active.");
        } catch (err) {
            console.error("❌ Vox Audio Fail:", err);
        }
    }

    // Database Cleanup (GM Only)
    if (game.user.isGM) {
        const legacy = ["Vox: Toggle Narrator", "Vox: Toggle Puppeteer", "Vox: Toggle Character"];
        for (const name of legacy) {
            const existing = game.macros.filter(m => m.name === name);
            for (const m of existing) await m.delete();
        }
    }
});

// ==========================================
// 5. CHAT SKINNING ENGINE (V13/V14 COMPATIBLE)
// ==========================================
Hooks.on("renderChatMessageHTML", (message, html, data) => {
    const voxType = message.getFlag("vox-conjurata", "type");
    if (!voxType) return;

    // Convert HTMLElement to jQuery for backwards compatibility with our existing skinning logic
    const jHtml = $(html);
    const content = jHtml.find(".message-content");
    const originalContent = content.html();

    if (voxType === "narration") {
        jHtml.addClass("vox-conjurata-card vox-conjurata-narration");
        jHtml.empty().append(`
            <div class="narration-header"><i class="fas fa-book-open gold-icon"></i><span class="narration-title">SCENE DESCRIPTION</span><i class="fas fa-book-open gold-icon"></i></div>
            <div class="message-content narration-text">${originalContent}</div>
        `);
    } 
    else if (voxType === "puppet" || voxType === "ai" || voxType === "player") {
        const actor = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const actorName = actor?.name || message.speaker.alias || "Entity";
        const actorImg = actor?.img || "icons/svg/mystery-man.svg";
        const audioUrl = message.getFlag("vox-conjurata", "audioUrl");
        
        let skinClass = `vox-conjurata-${voxType}`;
        let tag = voxType === "puppet" ? "GM PUPPET" : (voxType === "ai" ? "AI CORE" : "TRANSCRIPT");
        let icon = voxType === "ai" ? "fa-brain" : (voxType === "player" ? "fa-waveform-lines" : "fa-mask");
        
        jHtml.addClass(`vox-conjurata-card ${skinClass}`);

        let contextLine = "";
        if (voxType === "ai") {
            const responseTo = message.getFlag("vox-conjurata", "responseTo") || "Player";
            contextLine = `<div class="ai-context-line"><i class="fas fa-reply"></i> In response to <strong>${responseTo}</strong></div>`;
        } else if (voxType === "player") {
            const targetName = game.actors.get(message.getFlag("vox-conjurata", "targetActorId"))?.name || "NPC";
            contextLine = `<div class="player-target-line"><i class="fas fa-comment-lines"></i> Speaking to <strong>${targetName}</strong></div>`;
        }

        const audioHtml = audioUrl ? `<div class="vox-conjurata-audio-container"><button class="vox-conjurata-audio-play-btn" data-audio-src="${audioUrl}"><i class="fas fa-volume-high"></i> Play Generated Voice</button></div>` : "";

        jHtml.empty().append(`
            ${contextLine}
            <div class="puppet-layout">
                <img class="puppet-avatar ${voxType === 'ai' ? 'ai-border' : ''}" src="${actorImg}"/>
                <div class="puppet-body">
                    <header class="message-header ${voxType}-header">
                        <span class="sender ${voxType}-name">${actorName}</span>
                        <span class="${voxType}-tag"><i class="fas ${icon}"></i> ${tag}</span>
                    </header>
                    <div class="message-content ${voxType}-text">${originalContent}</div>
                    ${audioHtml}
                </div>
            </div>
        `);

        jHtml.find(".vox-conjurata-audio-play-btn").on("click", (e) => {
            new Audio($(e.currentTarget).data("audio-src")).play();
        });
    }
});

// ==========================================
// 6. HELPER FUNCTIONS
// ==========================================
function startRecording(micType) {
    try {
        if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
            globalThis.voxState.audioChunks = [];
            globalThis.voxState.activeMicType = micType; // Lock the mic type context
            globalThis.voxState.mediaRecorder.start();
            console.log(`🎙️ Vox-Conjurata: Recording started (${micType}).`);
        } else {
            console.warn("🎙️ Vox-Conjurata: startRecording called but MediaRecorder not ready.", {
                exists: !!globalThis.voxState.mediaRecorder,
                state: globalThis.voxState.mediaRecorder?.state
            });
        }
    } catch (e) {
        console.error("🎙️ Vox-Conjurata: Failed to start recording:", e);
    }
}

function stopRecording() {
    try {
        if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "recording") {
            globalThis.voxState.mediaRecorder.stop();
            console.log("🎙️ Vox-Conjurata: Recording stopped.");
        }
    } catch (e) {
        console.error("🎙️ Vox-Conjurata: Failed to stop recording:", e);
    }
}

function statusMessage(text, isOpen) {
    try {
        const recipients = game.user.isGM ? ChatMessage.getWhisperRecipients("GM") : [];
        ChatMessage.create({
            speaker: { alias: "Vox Core" },
            content: `<div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 1.2rem;">${isOpen ? '🎙️' : '🤫'}</span>
                        <div><strong>${text}</strong></div>
                      </div>`,
            whisper: recipients.length > 0 ? recipients.map(u => u.id) : []
        });
    } catch (e) {
        console.error("🎙️ Vox-Conjurata: Failed to create status message:", e);
    }
}

async function processAndSendAudio() {
    const chunks = globalThis.voxState.audioChunks;
    if (chunks.length === 0) {
        console.warn("🎙️ Vox-Conjurata: No audio chunks captured.");
        return;
    }

    const audioBlob = new Blob(chunks, { type: globalThis.voxState.mediaRecorder.mimeType || "audio/webm" });
    const micType = globalThis.voxState.activeMicType; // Use the locked context
    
    const formData = new FormData();
    formData.append("audio_blob", audioBlob, "voice_capture.webm");
    formData.append("metadata", JSON.stringify({
        activeSpeakerName: globalThis.voxState.activeSpeakerName,
        micType: micType,
        userId: game.user.id
    }));

    console.log(`📦 Vox-Conjurata: Sending audio blob (${audioBlob.size} bytes) to Orchestrator context: [${micType}]`);

    try {
        const response = await fetch(globalThis.voxState.voiceConversionEndpoint, { 
            method: "POST", 
            body: formData 
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        if (data.status === "success") {
            const { transcription, enrichment, voxType, audioUrl } = data;
            
            // Create the chat message with appropriate flags for the skinning engine
            const messageData = {
                content: transcription,
                speaker: ChatMessage.getSpeaker({
                    actor: canvas.tokens.controlled[0]?.actor || game.user.character,
                    alias: globalThis.voxState.activeSpeakerName
                }),
                flags: {
                    "vox-conjurata": {
                        type: voxType,
                        emotionalResonance: enrichment.emotional_resonance,
                        vocalDelivery: enrichment.vocal_delivery_prompt,
                        audioUrl: audioUrl
                    }
                }
            };

            await ChatMessage.create(messageData);

        } else {
            console.warn("🎙️ Vox-Conjurata: Backend returned non-success status:", data);
        }

    } catch (err) {
        console.error("❌ Vox-Conjurata: Pipeline failure:", err);
        ui.notifications.error("Vox-Conjurata: Backend pipeline failure.");
    }
}
