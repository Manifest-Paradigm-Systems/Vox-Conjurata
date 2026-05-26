/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Intercepts dialogue completion and offloads processing to the local orchestrator.
 */

Hooks.once("ready", () => {
    console.log("vox-conjurata | System initialized and listening for session events.");
});

Hooks.on("renderChatLog", (app, html, data) => {
    const chatForm = html.find("#chat-form");
    if (!chatForm.length) return;

    const controls = $(`<div id="vox-conjurata-controls"></div>`);

    if (game.user.isGM) {
        // GM Tone Selector
        const toneSelector = $(`
            <select id="vox-conjurata-tone-selector" title="Emotional Intent Stance">
                <option value="auto">Auto (Acoustic Analysis)</option>
                <option value="hostile">Hostile</option>
                <option value="friendly">Friendly</option>
                <option value="afraid">Afraid</option>
                <option value="deceptive">Deceptive</option>
            </select>
        `);
        controls.append(toneSelector);

        // GM Puppet Mic
        const puppetMic = $(`
            <div id="vox-conjurata-gm-puppet-mic" class="vox-conjurata-mic-btn" title="Puppet Mic (Speak as Target)">
                <i class="fas fa-microphone-lines"></i><i class="fas fa-mask" style="font-size: 0.6em; position: absolute; bottom: 2px; right: 2px;"></i>
            </div>
        `);
        controls.append(puppetMic);

        // GM Narrator Mic
        const narrateMic = $(`
            <div id="vox-conjurata-gm-narrate-mic" class="vox-conjurata-mic-btn" title="Narrator Mic (Global Scene)">
                <i class="fas fa-microphone"></i><i class="fas fa-book-open" style="font-size: 0.6em; position: absolute; bottom: 2px; right: 2px;"></i>
            </div>
        `);
        controls.append(narrateMic);
    } else {
        // Player Mic
        const playerMic = $(`
            <div id="vox-conjurata-player-mic" class="vox-conjurata-mic-btn" title="Push-to-Talk NPC Interaction">
                <i class="fas fa-microphone"></i>
            </div>
        `);
        controls.append(playerMic);
        
        // Update disabled state based on targets
        const updatePlayerMic = () => {
            const targets = Array.from(game.user.targets);
            const hasNPCTarget = targets.some(t => t.actor && !t.actor.hasPlayerOwner);
            playerMic.toggleClass("disabled", !hasNPCTarget);
        };
        Hooks.on("targetToken", updatePlayerMic);
        updatePlayerMic();
    }

    chatForm.before(controls);
    setupAudioCapture(controls);
});

let mediaRecorder;
let audioChunks = [];

function setupAudioCapture(container) {
    container.find(".vox-conjurata-mic-btn").on("mousedown touchstart", async function(e) {
        if ($(this).hasClass("disabled")) return;
        
        const button = $(this);
        const micType = button.attr("id");
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                sendToBackend(audioBlob, micType);
                stream.getTracks().forEach(track => track.stop());
            };

            mediaRecorder.start();
            button.addClass("active");
            
            // GM Voice Privacy: Mute in VTT if necessary (handled by not attaching to WebRTC)
            console.log(`vox-conjurata | Recording started: ${micType}`);

        } catch (err) {
            console.error("vox-conjurata | Mic access error:", err);
            ui.notifications.error("vox-conjurata: Microphone access denied or unavailable.");
        }
    });

    $(window).on("mouseup touchend", function() {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
            $(".vox-conjurata-mic-btn").removeClass("active");
            console.log("vox-conjurata | Recording stopped.");
        }
    });
}

Hooks.on("renderChatMessage", (message, html, data) => {
    const voxType = message.getFlag("vox-conjurata", "type");
    if (!voxType) return;

    const content = html.find(".message-content");
    const originalContent = content.html();

    if (voxType === "narration") {
        html.addClass("vox-conjurata-card vox-conjurata-narration");
        const skinnedContent = `
            <div class="narration-header">
                <i class="fas fa-book-open gold-icon"></i>
                <span class="narration-title">SCENE DESCRIPTION</span>
                <i class="fas fa-book-open gold-icon"></i>
            </div>
            <div class="message-content narration-text">
                ${originalContent}
            </div>
        `;
        html.empty().append(skinnedContent);
    } 
    
    else if (voxType === "puppet") {
        const actor = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const actorName = actor?.name || message.speaker.alias || "Unknown NPC";
        const actorImg = actor?.img || "icons/svg/mystery-man.svg";
        const audioUrl = message.getFlag("vox-conjurata", "audioUrl");

        html.addClass("vox-conjurata-card vox-conjurata-puppet");
        
        const audioHtml = audioUrl ? `
            <div class="vox-conjurata-audio-container">
                <button class="vox-conjurata-audio-play-btn" data-audio-src="${audioUrl}">
                    <i class="fas fa-volume-high"></i> Play Generated Voice
                </button>
            </div>
        ` : "";

        const skinnedContent = `
            <div class="puppet-layout">
                <img class="puppet-avatar" src="${actorImg}" title="${actorName}"/>
                <div class="puppet-body">
                    <header class="message-header puppet-header">
                        <span class="sender puppet-name">${actorName}</span>
                        <span class="puppet-tag">GM PUPPET</span>
                    </header>
                    <div class="message-content puppet-text">
                        ${originalContent}
                    </div>
                    ${audioHtml}
                </div>
            </div>
        `;
        html.empty().append(skinnedContent);

        // Audio Playback Listener
        html.find(".vox-conjurata-audio-play-btn").on("click", (e) => {
            const url = $(e.currentTarget).data("audio-src");
            const audio = new Audio(url);
            audio.play();
        });
    }

    else if (voxType === "ai") {
        const actor = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const actorName = actor?.name || message.speaker.alias || "AI NPC";
        const actorImg = actor?.img || "icons/svg/mystery-man.svg";
        const responseTo = message.getFlag("vox-conjurata", "responseTo") || "Unknown Player";

        html.addClass("vox-conjurata-card vox-conjurata-ai");

        const skinnedContent = `
            <div class="ai-context-line">
                <i class="fas fa-reply"></i> In response to <strong>${responseTo}</strong>
            </div>
            <div class="puppet-layout">
                <img class="puppet-avatar ai-border" src="${actorImg}"/>
                <div class="puppet-body">
                    <header class="message-header ai-header">
                        <span class="sender ai-name">${actorName}</span>
                        <span class="ai-tag"><i class="fas fa-brain"></i> AI CORE</span>
                    </header>
                    <div class="message-content ai-text">
                        ${originalContent}
                    </div>
                </div>
            </div>
        `;
        html.empty().append(skinnedContent);
    }

    else if (voxType === "player") {
        const character = message.speaker.actor ? game.actors.get(message.speaker.actor) : null;
        const charName = character?.name || message.speaker.alias || "Player Character";
        const charImg = character?.img || "icons/svg/mystery-man.svg";
        const targetActorId = message.getFlag("vox-conjurata", "targetActorId");
        const targetActor = targetActorId ? game.actors.get(targetActorId) : null;
        const targetName = targetActor?.name || "Unknown Target";

        html.addClass("vox-conjurata-card vox-conjurata-player");

        const skinnedContent = `
            <div class="player-target-line">
                <i class="fas fa-comment-lines"></i> Speaking to <strong>${targetName}</strong>
            </div>
            <div class="player-layout">
                <img class="player-avatar" src="${charImg}" title="${charName}"/>
                <div class="player-body">
                    <header class="message-header player-header">
                        <span class="sender player-name">${charName}</span>
                        <span class="player-tag"><i class="fas fa-waveform-lines"></i> TRANSCRIPT</span>
                    </header>
                    <div class="message-content player-text">
                        ${originalContent}
                    </div>
                </div>
            </div>
        `;
        html.empty().append(skinnedContent);
    }
});

async function sendToBackend(audioBlob, micType) {
    const tone = $("#vox-conjurata-tone-selector").val() || "auto";
    const targets = Array.from(game.user.targets);
    
    // Logic for actorId:
    // Puppet: First target
    // Player: First target
    // Narrate: null
    let actorId = null;
    if (micType !== "vox-conjurata-gm-narrate-mic") {
        actorId = targets[0]?.actor?.id || null;
    }

    const formData = new FormData();
    formData.append("audio_blob", audioBlob, "voice_capture.webm");
    formData.append("metadata", JSON.stringify({
        actorId: actorId,
        userId: game.user.id,
        intent_stance: tone,
        micType: micType
    }));

    ui.notifications.info("vox-conjurata: Processing voice capture...");

    try {
        const response = await fetch("http://localhost:8080/api/voice-conversion", {
            method: "POST",
            body: formData
        });

        if (!response.ok) throw new Error("Backend failed to process voice.");
        ui.notifications.info("vox-conjurata: Voice processed successfully.");

    } catch (err) {
        console.error("vox-conjurata | Backend Error:", err);
        ui.notifications.error("vox-conjurata: Failed to transmit audio to backend.");
    }
}


/**
 * Global function called by your game world when a scene or conversation ends.
 * Other DMs can trigger this via standard gameplay triggers or chat hooks.
 */
async function processNPCDialogueLog(npcName, rawTranscript) {
    if (!game.user.isGM) return;

    ui.notifications.info(`vox-conjurata: Summarizing conversation with ${npcName}...`);

    try {
        // Send the dialogue out to our local Python orchestrator container
        const response = await fetch("http://localhost:8080/api/v1/dialogue/end", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ npcName: npcName, transcript: rawTranscript })
        });

        if (!response.ok) throw new Error("Backend orchestrator failed to process request.");
        const data = await response.json();
        
        // Use the returned summary to build the journal entry right here in the client
        await appendMemoryToJournal(npcName, data.summary);

    } catch (error) {
        console.error("vox-conjurata | Pipeline Error:", error);
        ui.notifications.error("vox-conjurata: Failed to process conversation summary.");
    }
}

/**
 * Internal helper to natively build out folders and append pages in the database
 */
async function appendMemoryToJournal(npcName, summaryText) {
    const folderName = "NPC Memories";
    let folder = game.folders.find(f => f.name === folderName && f.type === "JournalEntry");
    if (!folder) {
        folder = await Folder.create({ name: folderName, type: "JournalEntry" });
    }

    let journal = game.journal.find(j => j.name === npcName && j.folder?.id === folder.id);
    if (!journal) {
        journal = await JournalEntry.create({ name: npcName, folder: folder.id });
    }

    const timestamp = new Date().toLocaleString('en-US', { 
        year: 'numeric', month: 'short', day: 'numeric', 
        hour: '2-digit', minute: '2-digit', hour12: true 
    });

    await journal.createEmbeddedDocuments("JournalEntryPage", [{
        name: `Session Log: ${timestamp}`,
        type: "text",
        text: {
            content: `
                <div class="vox-conjurata-log" style="border-left: 3px solid #7a52cc; padding-left: 12px; font-family: sans-serif;">
                    <span style="color: #a380f5; font-size: 0.85em; font-weight: bold; letter-spacing: 1px;">VOX-CONJURATA SYSTEM RECORD</span>
                    <p>${summaryText}</p>
                </div>
            `,
            format: 1
        }
    }]);

    ui.notifications.info(`vox-conjurata: Memory profile for ${npcName} updated.`);
}