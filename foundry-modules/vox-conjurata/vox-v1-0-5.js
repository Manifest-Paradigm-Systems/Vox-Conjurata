/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Consolidates Telemetry, Chat Skinning, Hardware PTT, and Live Panel.
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
        
        updateIngestionProgress(0, 1, token.actor.name);
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
                updateIngestionProgress(1, 1, token.actor.name);
                statusMessage(`✅ VOX TERMINAL: Voice forged for ${token.actor.name}!`, false);
                ui.notifications.info(`🎙️ Vox: Voice seed created for ${token.actor.name}`);
            } else {
                updateIngestionProgress(1, 1, "Failed");
            }
        } catch (e) { 
            updateIngestionProgress(1, 1, "Error");
            statusMessage("❌ VOX TERMINAL: Forge failed.", false); 
        }
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

// Chat Command Intercept — dual-layer for Foundry V12+ compatibility
(function() {
    // Layer 1: chatMessage hook (catches most invocations)
    Hooks.on("chatMessage", (chatLog, message, chatData) => {
        if (message.trim().toLowerCase().startsWith("/vox")) {
            const parts = message.trim().split(/\s+/);
            handleVoxCommand(parts[1]?.toLowerCase() || "help", parts.slice(2).join(" "));
            return false;  // suppress normal chat processing
        }
    });

    // Layer 2: monkey-patch the chat message submit handler (Foundry V12+)
    Hooks.once("ready", () => {
        // Foundry V12+ uses ui.chat._onSubmit or similar internal handler.
        // Patch the lowest-level message entry point we can find.
        const chatLog = ui.chat;
        if (!chatLog) return;

        // Try Foundry V12 pattern: intercept at the form submission level
        const form = chatLog.form;
        if (form) {
            const originalSubmit = form.onsubmit;
            form.addEventListener("submit", (event) => {
                const textarea = form.querySelector('textarea[name="message"]');
                if (textarea) {
                    const msg = textarea.value.trim();
                    if (msg.toLowerCase().startsWith("/vox")) {
                        event.preventDefault();
                        event.stopPropagation();
                        const parts = msg.split(/\s+/);
                        handleVoxCommand(parts[1]?.toLowerCase() || "help", parts.slice(2).join(" "));
                        textarea.value = '';
                        return false;
                    }
                }
            }, true);  // capture phase — fires before Foundry's handler
        }
    });
})();

/**
 * createVoxChatMessage(data)
 * ─────────────────────────────────────────────────────────────────────────────
 * V12 / PF2e compatible message creator. 
 */
async function createVoxChatMessage(data) {
    const messageData = { ...data, style: 2 };
    try {
        const message = new ChatMessage(messageData);
        return await ChatMessage.create(message.toObject());
    } catch (err) {
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

function updateIngestionProgress(current, total, name) {
    let bar = document.getElementById('vox-ingestion-progress');
    if (!bar) {
        const container = document.createElement('div');
        container.id = 'vox-ingestion-progress';
        container.style = "position: fixed; top: 80px; left: 50%; transform: translateX(-50%); width: 360px; padding: 12px; background: rgba(20, 20, 25, 0.95); border: 1px solid #ff6400; border-radius: 6px; z-index: 1000; color: white; font-family: 'Signika', sans-serif; box-shadow: 0 4px 20px rgba(0,0,0,0.6); display: flex; flex-direction: column; gap: 8px;";
        container.innerHTML = `
            <div style="display: flex; justify-content: space-between; font-size: 13px; font-weight: bold; color: #ff6400; text-transform: uppercase; letter-spacing: 0.5px;">
                <span id="vox-progress-label">Voice Registry Ingestion</span>
                <span id="vox-progress-count">0/0</span>
            </div>
            <div style="width: 100%; height: 8px; background: #1a1a1a; border-radius: 4px; overflow: hidden; border: 1px solid #333;">
                <div id="vox-progress-fill" style="width: 100%; height: 100%; background: linear-gradient(90deg, #ff9d00, #ff6400); shadow: 0 0 10px #ff6400; transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);"></div>
            </div>
            <div id="vox-progress-actor" style="font-size: 11px; color: #888; text-align: center; font-style: italic;">Synchronizing neural seeds...</div>
        `;
        document.body.appendChild(container);
        bar = container;
    }
    
    // The user specifically requested a "shrinking" bar — so 100% is start, 0% is finish.
    const pctRemaining = Math.max(0, Math.round(((total - current) / total) * 100));
    const fill = document.getElementById('vox-progress-fill');
    if (fill) fill.style.width = `${pctRemaining}%`;
    
    const countEl = document.getElementById('vox-progress-count');
    if (countEl) countEl.innerText = `${current} / ${total}`;
    
    const actorEl = document.getElementById('vox-progress-actor');
    if (actorEl) actorEl.innerText = current >= total ? "Neural synchronization complete." : `Forging: ${name}`;
    
    if (current >= total) {
        const label = document.getElementById('vox-progress-label');
        if (label) { label.innerText = "✅ Systems Nominal"; label.style.color = "#00ff88"; }
        if (fill) fill.style.background = "#00ff88";
        setTimeout(() => {
            bar.style.opacity = '0';
            bar.style.transition = 'opacity 1s ease';
            setTimeout(() => bar.remove(), 1000);
        }, 3000);
    }
}

async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready) return;
    const tokensToIngest = canvas.tokens.placeables.filter(t => t.actor && !ingestedActors.has(t.actor.id));
    if (tokensToIngest.length === 0) return;

    let processed = 0;
    const total = tokensToIngest.length;
    
    updateIngestionProgress(0, total, "Starting...");

    // Process one-by-one to avoid Cloudflare 524 timeouts (>100s)
    // 15-second seeds take ~40-50s each, so batching 3 would take ~150s.
    for (let token of tokensToIngest) {
        const a = token.actor;
        if (ingestedActors.has(a.id)) { processed++; continue; }
        ingestedActors.add(a.id);
        
        updateIngestionProgress(processed, total, a.name);
        
        try { 
            await fetch(globalThis.voxState.ingestEndpoint, { 
                method: "POST", headers: { "Content-Type": "application/json" }, 
                body: JSON.stringify({
                    actorId: a.id, name: a.name, artPath: a.img, isMonster: resolveIsMonster(a),
                    lore: a.system.details?.biography?.value || a.system.description?.value || "No bio available.",
                    stats: { race: a.system.details?.race || "Unknown", level: a.system.details?.level?.value || 0 }
                }) 
            }); 
        } catch (e) { console.error("Vox Ingestion Error:", e); }
        
        processed++;
        updateIngestionProgress(processed, total, a.name);
    }
    ui.notifications.info("✅ Vox: All seeds ready for gameplay.");
}

async function onReady() {
    if (globalThis.voxReadyExecuted) return; globalThis.voxReadyExecuted = true;
    if (game.user.isGM) {
        await scanActiveSceneTokens();
        // Ensure Live Panel button is added
        if (ui.controls) ui.controls.render();
    }
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

// ==========================================
// 5. LIVE PANEL IMPLEMENTATION (CONSOLIDATED)
// ==========================================

class VoxLivePanel extends Application {
    constructor(options = {}) {
        super(options);
        this.activeActorId = null;
        this.isBypass = true;
        this.isSyncEnabled = true;
        this.settings = { pitch: 0, formant: 0, mix: 1.0, f0Detector: "rmvpe_onnx", chunkSize: 112, extraFrame: 4096 };
        this.profiles = { "elminster": { modelId: 1, tran: -3 }, "goblin": { modelId: 2, tran: 7 }, "strahd": { modelId: 3, tran: 0 } };
    }

    static get defaultOptions() {
        return mergeObject(super.defaultOptions, {
            id: "vox-live-panel", title: "🎙️ Vox Conjurata Live Panel", width: 320, height: "auto", resizable: false, dragDrop: [{ dragSelector: ".window-header" }]
        });
    }

    async _render(force = false, options = {}) {
        await super._render(force, options);
        this.element.find('.window-content').html(this._getHtml());
        this.activateListeners(this.element);
    }

    _getHtml() {
        const actors = game.actors.filter(a => a.type === "npc" || a.hasPlayerOwner).slice(0, 9);
        let actorGrid = actors.map(a => `<div class="vox-actor-btn ${this.activeActorId === a.id ? 'active' : ''}" data-actor-id="${a.id}" data-actor-name="${a.name.toLowerCase()}"><img src="${a.img}"/><div class="actor-name">${a.name}</div></div>`).join("");
        return `<div class="vox-panel-section"><div class="vox-section-title"><span>Quick-Swap Grid</span><div class="vox-sync-toggle ${this.isSyncEnabled ? 'active' : ''}"><i class="fas fa-sync"></i> SYNC</div></div><div class="vox-actor-grid">${actorGrid}</div></div>
                <div class="vox-panel-section"><div class="vox-section-title">On-The-Fly Tweaks</div><div class="vox-slider-group"><div class="vox-slider-label"><span>Pitch Shift</span><span class="vox-slider-value">${this.settings.pitch > 0 ? '+' : ''}${this.settings.pitch}</span></div><input type="range" class="vox-slider" id="pitch-slider" min="-12" max="12" step="1" value="${this.settings.pitch}"></div></div>
                <div class="vox-panel-section"><div class="vox-master-toggle ${this.isBypass ? '' : 'active'}" id="master-toggle"><i class="fas ${this.isBypass ? 'fa-microphone-slash' : 'fa-microphone'}"></i><span>${this.isBypass ? 'BYPASS (OOC)' : 'VOX ACTIVE (NPC)'}</span></div></div>`;
    }

    activateListeners(html) {
        super.activateListeners(html);
        html.find(".vox-actor-btn").click(ev => this.switchActor(ev.currentTarget.dataset.actorId, ev.currentTarget.dataset.actorName));
        html.find(".vox-sync-toggle").click(() => { this.isSyncEnabled = !this.isSyncEnabled; this.render(); });
        html.find("#pitch-slider").on("input", ev => { this.settings.pitch = parseInt(ev.target.value); this.updateBackend(); this.render(); });
        html.find("#master-toggle").click(() => { this.isBypass = !this.isBypass; this.updateBackend(); this.render(); });
    }

    async switchActor(id, name) {
        this.activeActorId = id; const p = this.profiles[name.toLowerCase()] || { modelId: 0, tran: 0 };
        this.settings.pitch = p.tran; await this.updateBackend(p.modelId); this.render();
    }

    async updateBackend(forceId = null) {
        if (this.isBypass) return;
        const payload = { modelId: forceId !== null ? forceId : (this.profiles[this.activeActorId]?.modelId || 0), f0Detector: this.settings.f0Detector, tran: this.settings.pitch, chunkSize: this.settings.chunkSize, extraFrame: this.settings.extraFrame };
        try { await fetch("/api/voice-changer/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); } catch (e) {}
    }
}

globalThis.voxLivePanel = new VoxLivePanel();

Hooks.on("controlToken", (token, selected) => {
    if (selected && globalThis.voxLivePanel.isSyncEnabled) globalThis.voxLivePanel.switchActor(token.actor.id, token.actor.name);
});

Hooks.on("getSceneControlButtons", (controls) => {
    const tokenControl = controls.find(c => c.name === "token");
    if (tokenControl) {
        tokenControl.tools.push({
            name: "vox-panel", title: "Vox Live Panel", icon: "fas fa-microphone-lines", button: true, visible: game.user.isGM, onClick: () => globalThis.voxLivePanel.render(true)
        });
    }
});

Hooks.once("ready", () => {
    game.keybindings.register("vox-conjurata", "toggleVocalMask", {
        name: "Toggle Vocal Mask", editable: [{ key: "KeyV", modifiers: [KeyboardManager.MODIFIER_KEYS.CONTROL, KeyboardManager.MODIFIER_KEYS.SHIFT] }],
        onDown: () => { globalThis.voxLivePanel.isBypass = !globalThis.voxLivePanel.isBypass; globalThis.voxLivePanel.updateBackend(); globalThis.voxLivePanel.render(); }
    });
});

globalThis.startRecording = startRecording; globalThis.stopRecording = stopRecording; globalThis.statusMessage = statusMessage; globalThis.playAudio = playAudio; globalThis.resolveActiveToken = resolveActiveToken; globalThis.resolveIsMonster = resolveIsMonster;

if (typeof game !== 'undefined' && game.keybindings) registerKeybindings(); else Hooks.once("init", registerKeybindings);
