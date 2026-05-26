/**
 * Vox-Conjurata: Native WebM Audio Stream Pipeline
 * Captures live hardware microphone streams during active PTT frames
 * and dispatches compressed WebM blobs to the local faster-whisper container.
 */

globalThis.voxState = globalThis.voxState || { 
    narratorActive: false, 
    puppetActive: false,
    playerActive: false,
    activePuppetName: "",
    activePlayerName: "",
    mediaRecorder: null,
    audioChunks: [],
    // Points to your local faster-whisper host port allocation
    sttEndpoint: "http://localhost:5000/v1/audio/transcriptions" 
};

// Initialize Browser Hardware Microphone Stream on Client Boot
Hooks.once("ready", async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.error("❌ Vox Audio Fail: Browser environment does not support secure audio capturing.");
        return;
    }

    try {
        // Request secure microphone access from the browser layout upfront
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        // Use standard compressed webm container format running native Opus audio codec
        globalThis.voxState.mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        
        // Buffer listener to capture incoming raw voice waves sequentially
        globalThis.voxState.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                globalThis.voxState.audioChunks.push(event.data);
            }
        };

        // Fire transmission routine the split-second the microphone line cuts out
        globalThis.voxState.mediaRecorder.onstop = async () => {
            await processAndSendAudio();
        };

        console.log("🎙️ Vox-Conjurata: WebM hardware microphone pipeline bound and active.");
    } catch (err) {
        console.error("❌ Vox Audio Fail: Hardware microphone access rejected by user:", err);
    }
});

// Structural Multi-Part Form Packager & Transmiter
async function processAndSendAudio() {
    const chunks = globalThis.voxState.audioChunks;
    const endpoint = globalThis.voxState.sttEndpoint;
    
    if (chunks.length === 0) return;

    // Bundle compressed chunks into a singular lightweight file payload (~20-40KB)
    const audioBlob = new Blob(chunks, { type: "audio/webm" });
    globalThis.voxState.audioChunks = []; // Purge cache array instantly for the next run

    // Resolve context metadata string identifiers
    let contextName = "Narrator";
    if (globalThis.voxState.activePuppetName) contextName = globalThis.voxState.activePuppetName;
    else if (globalThis.voxState.activePlayerName) contextName = globalThis.voxState.activePlayerName;

    console.log(`📦 Vox Pipeline: Forwarding WebM payload (${audioBlob.size} bytes) for context: [${contextName}]`);

    // Construct standard multipart/form-data schema required by faster-whisper endpoints
    const formData = new FormData();
    formData.append("file", audioBlob, "whisper_input.webm");
    formData.append("model", "base");
    formData.append("language", "en");

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            body: formData
        });

        if (!response.ok) throw new Error(`HTTP network error string received: ${response.status}`);
        
        const data = await response.json();
        const transcriptionText = data.text || "";

        if (transcriptionText.trim().length > 0) {
            // Echo verified transcription directly into secure GM telemetry chat cards
            ChatMessage.create({
                speaker: { alias: `${contextName} (AI Transcribed)` },
                content: `💬 "${transcriptionText}"`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        }
    } catch (err) {
        console.error("❌ Vox Transmission Failure: Network path to STT container blocked:", err);
        ui.notifications.error("Vox Pipeline: Local transcription container unreachable.");
    }
}

// Keybinding State Machine Activators
Hooks.once("init", () => {
    
    // Narrator PTT (Key Y)
    game.keybindings.register("vox-conjurata", "narratorPTT", {
        name: "Vox: Narrator Push-to-Talk",
        editable: [{ key: "KeyY" }],
        onDown: () => {
            if (!game.user.isGM || globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = true;
            
            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
                globalThis.voxState.mediaRecorder.start();
            }

            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🎙️ <strong>Narrator Mic [Y]:</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (!game.user.isGM || !globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = false;
            
            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "recording") {
                globalThis.voxState.mediaRecorder.stop();
            }
        }
    });

    // Puppeteer PTT (Key H)
    game.keybindings.register("vox-conjurata", "puppeteerPTT", {
        name: "Vox: Puppeteer Push-to-Talk",
        editable: [{ key: "KeyH" }],
        onDown: () => {
            if (!game.user.isGM || globalThis.voxState.puppetActive) return;
            
            const selectedToken = canvas.tokens.controlled[0];
            if (!selectedToken) {
                ui.notifications.error("❌ Puppeteer Error: You must select an NPC token first!");
                return;
            }
            
            globalThis.voxState.puppetActive = true;
            const actorName = selectedToken.actor?.name || "Unknown Entity";
            globalThis.voxState.activePuppetName = actorName;
            
            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
                globalThis.voxState.mediaRecorder.start();
            }

            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🎭 <strong>Puppeteer [H] (${actorName}):</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (!game.user.isGM || !globalThis.voxState.puppetActive) return;
            globalThis.voxState.puppetActive = false;
            globalThis.voxState.activePuppetName = "";
            
            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "recording") {
                globalThis.voxState.mediaRecorder.stop();
            }
        }
    });

    // Player PTT (Key I)
    game.keybindings.register("vox-conjurata", "playerPTT", {
        name: "Vox: Character Push-to-Talk",
        editable: [{ key: "KeyI" }],
        onDown: () => {
            if (game.user.isGM || globalThis.voxState.playerActive) return;
            
            const speakerActor = canvas.tokens.controlled[0]?.actor || game.user.character;
            const speakerName = speakerActor?.name || game.user.name;
            
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activePlayerName = speakerName;

            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "inactive") {
                globalThis.voxState.mediaRecorder.start();
            }

            ChatMessage.create({
                speaker: { alias: "Vox Proxy" },
                content: `👤 <strong>Character Mic [I]:</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span> (Speaking as: <strong>${speakerName}</strong>)`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (game.user.isGM || !globalThis.voxState.playerActive) return;
            globalThis.voxState.playerActive = false;
            globalThis.voxState.activePlayerName = "";
            
            if (globalThis.voxState.mediaRecorder && globalThis.voxState.mediaRecorder.state === "recording") {
                globalThis.voxState.mediaRecorder.stop();
            }
        }
    });
});
