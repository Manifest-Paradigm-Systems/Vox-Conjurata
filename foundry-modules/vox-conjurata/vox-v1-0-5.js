/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Consolidates Telemetry, Chat Skinning, Hardware PTT, and Live Panel.
 */
const $ = globalThis.jQuery;
const getSafeSetting = (scope, key, fallback = false) => {
    try { return game.settings.get(scope, key); }
    catch (e) { return fallback; }
};
console.log("🚀 Vox-Conjurata: Script evaluation started.");

// ── API Proxy ──
// Route all /api/* fetch calls through Caddy on port 30001
// so they reach the orchestrator. Foundry itself stays on port 30000.
(function() {
    const CADDY_API = "http://localhost:30001";
    const origFetch = globalThis.fetch;
    globalThis.fetch = function(url, opts) {
        if (typeof url === "string" && url.startsWith("/api/")) {
            url = CADDY_API + url;
        }
        return origFetch.call(this, url, opts);
    };
})();

// ==========================================
// 1. EVENT QUEUE HUD (REAL-TIME TRACKING)
// ==========================================

class VoxEventQueueHUD extends Application {
    constructor(options = {}) {
        super(options);
        this.tasks = [];
        this.pollInterval = null;
    }

    static get defaultOptions() {
        return foundry.utils.mergeObject(super.defaultOptions, {
            id: "vox-event-queue-hud",
            title: "Vox: Task Pipeline",
            template: null, // We'll use _renderInner
            popOut: true,
            resizable: false,
            minimizable: true,
            width: 260,
            height: "auto",
            left: 100,
            top: 100,
            classes: ["vox-hud"]
        });
    }

    async getData() {
        // Fetch balance for HUD
        let balance = { session_grant: 0, personal_wallet: 0, total_available: 0, is_out_of_credits: false, is_low_balance: false };
        try {
            const resp = await fetch(`/api/v1/ledger/balance/${game.user.id}`);
            balance = await resp.json();
        } catch (e) {}
        
        return {
            balance: balance,
            tasks: this.tasks
        };
    }

    async _renderInner(data) {
        const balance = data.balance;
        const statusColor = balance.is_out_of_credits ? '#ff4444' : (balance.is_low_balance ? '#ffbb00' : '#00ff88');
        const flashClass = balance.is_low_balance ? 'vox-low-balance-flash' : '';
        const isDry = balance.is_out_of_credits;
        const isLow = balance.is_low_balance;

        let html = `
            <div class="vox-hud-container" style="padding: 10px; background: rgba(15,15,15,0.95); color: #eee; font-family: 'Signika', sans-serif; border-radius: 8px; border: 1px solid #333; box-shadow: 0 4px 20px rgba(0,0,0,0.8); min-width: 240px;">
                <div class="vox-hud-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid #444; padding-bottom: 5px;">
                    <span style="font-size: 10px; font-weight: bold; color: #888; text-transform: uppercase; letter-spacing: 1px;"><i class="fas fa-microchip"></i> Core Pipeline</span>
                    
                    <div class="vox-stealth-wallet ${isDry ? 'force-reveal' : ''}" style="display: flex; gap: 8px; align-items: center; cursor: pointer; position: relative;">
                        <span class="vox-balance-label ${flashClass}" style="font-size: 10px; color: ${statusColor}; font-weight: bold; transition: all 0.3s ease; ${(isDry || isLow) ? '' : 'opacity: 0; width: 0; overflow: hidden;'}">
                            $${(balance.total_available ?? 0).toFixed(6)}
                        </span>
                        <i class="fas fa-coins vox-wallet-icon" style="color: ${statusColor}; font-size: 12px; filter: drop-shadow(0 0 2px ${statusColor});" title="Hover to view balance"></i>
                        
                        <div class="vox-wallet-controls" style="display: flex; gap: 4px; margin-left: 4px;">
                            <i class="fas fa-gift vox-open-transfer" style="color: #ff6400; font-size: 10px;" title="Gift Credits"></i>
                            <i class="fas fa-plus-circle vox-buy-credits" style="color: #00ff88; font-size: 10px;" title="Top up"></i>
                        </div>
                    </div>
                </div>

                <style>
                    .vox-stealth-wallet:hover .vox-balance-label { opacity: 1 !important; width: auto !important; margin-right: 5px; }
                    .vox-stealth-wallet.force-reveal .vox-balance-label { opacity: 1 !important; width: auto !important; margin-right: 5px; }
                    .vox-low-balance-flash { animation: vox-amber-glow 1.5s infinite alternate; }
                    @keyframes vox-amber-glow { from { text-shadow: 0 0 2px #ffbb00; } to { text-shadow: 0 0 8px #ffbb00, 0 0 15px #ff6400; } }
                    .vox-hud-container { transition: all 0.2s ease-in-out; }
                    .vox-hud-container:hover { border-color: #555; }
                </style>
                
                <div class="vox-sub-balances" style="display: flex; gap: 5px; margin-bottom: 10px; font-size: 8px; color: #666; ${(isDry || isLow) ? '' : 'display: none;'}">
                    <div style="flex: 1; background: rgba(255,255,255,0.03); padding: 3px 6px; border-radius: 3px; display: flex; justify-content: space-between;">
                        <span>Grant: $${(balance.session_grant ?? 0).toFixed(6)}</span>
                        ${balance.session_grant > 0 ? `<i class="fas fa-undo vox-return-grant" style="cursor: pointer; margin-left: 4px;" title="Return to pool"></i>` : ''}
                    </div>
                    <div style="flex: 1; background: rgba(255,255,255,0.03); padding: 3px 6px; border-radius: 3px; display: flex; justify-content: space-between;">
                        <span>Wallet: $${(balance.personal_wallet ?? 0).toFixed(6)}</span>
                        ${balance.personal_wallet > 0 ? `<i class="fas fa-hand-holding-usd vox-return-personal" style="cursor: pointer; margin-left: 4px;" title="Return to pool"></i>` : ''}
                    </div>
                </div>

                <div id="vox-task-list" style="display: flex; flex-direction: column; gap: 8px;">
        `;

        if (isDry) {
            html += `<div style="text-align: center; color: #ff4444; font-size: 9px; padding: 8px; background: rgba(255,0,0,0.05); border-radius: 4px; border: 1px solid rgba(255,0,0,0.2); margin-bottom: 8px;">
                <i class="fas fa-exclamation-triangle"></i> WALLET DRY
            </div>`;
        }

        if (this.tasks.length === 0) {
            html += `<div style="text-align: center; color: #666; font-size: 11px; padding: 5px;">Core Idle</div>`;
        }

        for (let task of this.tasks) {
            const isComplete = task.status === "complete";
            const statusLabel = task.type.replace("-", " ").toUpperCase();
            const barColor = isComplete ? "#00ff88" : (task.status === "cancelled" ? "#ff0000" : "#ff6400");
            const progressWidth = (task.progress * 100).toFixed(0);

            html += `
                <div class="vox-task-item" style="border-bottom: 1px solid #333; padding-bottom: 5px; position: relative;">
                    <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 4px; font-weight: bold;">
                        <span>${statusLabel}</span>
                        <div style="display: flex; gap: 5px; align-items: center;">
                            <span style="color: ${barColor}">${task.status.toUpperCase()}</span>
                            ${!isComplete && task.status !== "cancelled" ? `<i class="fas fa-times-circle vox-cancel-btn" data-task-id="${task.id}" style="color: #ff4444; cursor: pointer;" title="Abort & Refund"></i>` : ''}
                        </div>
                    </div>
                    <div style="height: 4px; background: #111; border-radius: 2px; overflow: hidden; width: 100%;">
                        <div style="height: 100%; width: ${progressWidth}%; background: ${barColor}; transition: width 0.3s ease, background 0.5s ease; ${!isComplete ? 'box-shadow: 0 0 5px #ff6400;' : ''}"></div>
                    </div>
                </div>
            `;
        }

        html += `</div></div>`;
        return $(html);
    }

    activateListeners(html) {
        super.activateListeners(html);
        $(html).find(".vox-cancel-btn").click(async (ev) => {
            const taskId = ev.currentTarget.dataset.taskId;
            try {
                const resp = await fetch("/api/v1/orchestrate/cancel", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({taskId: taskId, userId: game.user.id})
                });
                const data = await resp.json();
                if (data.status === "transaction_aborted_and_refunded") {
                    ui.notifications.info("🚫 Vox: Transaction aborted and refunded.");
                }
            } catch (e) {
                ui.notifications.error("❌ Failed to cancel task.");
            }
        });

        $(html).find(".vox-open-transfer").click(async () => {
            const users = game.users.filter(u => u.id !== game.user.id).sort((a, b) => (b.isGM ? 1 : 0) - (a.isGM ? 1 : 0));
            let userOptions = users.map(u => `<option value="${u.id}">${u.isGM ? "👑 " : ""}${u.name}${u.active ? "" : " (offline)"}</option>`).join("");
            userOptions = `<option value="POOL">--- Campaign Pool ---</option>` + userOptions;

            new Dialog({
                title: "Gift / Transfer Credits",
                content: `
                    <div style="padding: 10px; font-family: 'Signika', sans-serif;">
                        <style>
                            .vox-transfer-dialog select, .vox-transfer-dialog option { background: #111 !important; color: #eee !important; border-color: #444; }
                            .vox-transfer-dialog select:focus option:hover { background: #333 !important; }
                        </style>
                        <div class="form-group vox-transfer-dialog" style="margin-bottom: 12px;">
                            <label>Source Bucket:</label>
                            <select id="vox-transfer-source" style="width: 160px; background: #111; color: #eee; border: 1px solid #444;">
                                <option value="personal">My Persistent Wallet</option>
                                <option value="session">My Session Grant</option>
                            </select>
                        </div>
                        <div class="form-group vox-transfer-dialog" style="margin-bottom: 12px;">
                            <label>Recipient:</label>
                            <select id="vox-transfer-target" style="width: 160px; background: #111; color: #eee; border: 1px solid #444;">
                                ${userOptions}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Amount ($):</label>
                            <input type="number" id="vox-transfer-amount" step="0.10" min="0.10" value="1.00" style="width: 100%; background: #000; color: #00ff88; border: 1px solid #444;">
                        </div>
                    </div>
                `,
                buttons: {
                    gift: {
                        label: "<i class='fas fa-gift'></i> Send Gift",
                        callback: async (html) => {
                            const source = $(html).find("#vox-transfer-source").val();
                            const target = $(html).find("#vox-transfer-target").val();
                            const amount = parseFloat($(html).find("#vox-transfer-amount").val());
                            
                            try {
                                const resp = await fetch("/api/v1/ledger/transfer", {
                                    method: "POST",
                                    headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify({
                                        fromUserId: game.user.id,
                                        toUserId: target,
                                        amount: amount,
                                        fromPersonal: (source === 'personal')
                                    })
                                });
                                if (resp.ok) {
                                    ui.notifications.info(`🎁 Gift of $${(amount ?? 0).toFixed(6)} sent successfully!`);
                                    game.socket.emit("module.vox-conjurata", { type: "balance-refresh" });
                                    this.render(true);
                                } else {
                                    const err = await resp.json();
                                    ui.notifications.error(`❌ ${err.detail}`);
                                }
                            } catch (e) { ui.notifications.error("❌ Transfer service error."); }
                        }
                    }
                }
            }).render(true);
        });

        $(html).find(".vox-buy-credits").click(async () => {
            new Dialog({
                title: "Top Up Credits",
                content: `
                    <div style="padding: 10px; font-family: 'Signika', sans-serif;">
                        <p style="font-size: 13px; margin-bottom: 15px;">Secure checkout via Stripe. Credits are usually available instantly.</p>
                        <div class="form-group" style="margin-bottom: 15px;">
                            <label style="font-weight: bold;">Select Bundle:</label>
                            <select id="vox-topup-amount" style="width: 100%; height: 32px; background: #222; color: #eee; border: 1px solid #444;">
                                <option value="5.00">$5.00 Bundle (5,000 Credits)</option>
                                <option value="10.00" selected>$10.00 Bundle (10,000 Credits)</option>
                                <option value="25.00">$25.00 Bundle (25,000 Credits)</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                                <input type="checkbox" id="vox-auto-allocate" checked> Auto-allocate to MY wallet
                            </label>
                            <p style="font-size: 10px; color: #888; margin-top: 5px;">If unchecked, credits go to the table's shared Campaign Pool for the DM to distribute.</p>
                        </div>
                    </div>
                `,
                buttons: {
                    buy: {
                        label: "<i class='fas fa-credit-card'></i> Checkout",
                        callback: async (html) => {
                            const amount = $(html).find("#vox-topup-amount").val();
                            const autoAllocate = $(html).find("#vox-auto-allocate").is(":checked");
                            try {
                                const resp = await fetch(`/api/v1/billing/create-checkout-session?user_id=${game.user.id}&amount=${amount}&auto_allocate=${autoAllocate}`, { method: "POST" });
                                const data = await resp.json();
                                if (data.checkout_url) {
                                    window.open(data.checkout_url, '_blank');
                                    ui.notifications.info("🔗 Checkout opened in new tab. Waiting for verification...");
                                }
                            } catch (e) { ui.notifications.error("❌ Billing service error."); }
                        }
                    }
                }
            }).render(true);
        });

        $(html).find(".vox-return-grant").click(async () => {
            if (await Dialog.confirm({ title: "Return Grant", content: "Return unused session grant to pool?" })) {
                await fetch("/api/v1/ledger/return", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({userId: game.user.id, amount: -1}) });
                this.render(true);
            }
        });

        $(html).find(".vox-return-personal").click(async () => {
            if (await Dialog.confirm({ title: "Return Personal", content: "Return all personal wallet credits to campaign pool?" })) {
                await fetch("/api/v1/ledger/return", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({userId: game.user.id, amount: -2}) });
                this.render(true);
            }
        });
    }

    startPolling() {
        if (this.pollInterval) return;
        this.pollInterval = setInterval(async () => {
            try {
                // Poll both resource manager queue and active task registry (combined status needed)
                const resp = await fetch("/api/v1/queue/status");
                const data = await resp.json();
                
                // For a truly unified HUD, we could also fetch a separate "live" endpoint
                // But let's assume queue/status is updated with all active items.
                
                if (JSON.stringify(this.tasks) !== JSON.stringify(data)) {
                    this.tasks = data;
                    if (this.rendered) this.render(false);
                }
            } catch (e) {}
        }, 1000);
    }

    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    }
}

// Global HUD Instance
const voxHUD = new VoxEventQueueHUD();
Hooks.once("ready", () => {
    voxHUD.render(true);
    voxHUD.startPolling();
});

// ==========================================
// 2. TERMINAL ENGINE (BULLETPROOF INTERCEPT)
// ==========================================

// Global Command Handler
async function handleVoxCommand(command, param) {
    if (!game.user.isGM) return;
    let token = resolveActiveToken(true);
    let actor = token?.actor;
    let actorId = actor?.id;
    let actorName = actor?.name;
    let artPath = actor?.img || "icons/svg/mystery-man.svg";
    let isMonster = resolveIsMonster(actor);
    let lore = actor?.system?.details?.biography?.value || "";

    // If no token, default to Narrator
    if (!token && (command === "forge" || command === "voice")) {
        actorId = "narrator";
        actorName = "Narrator";
        isMonster = false;
        ui.notifications.info("🎙️ Vox Terminal: Target set to Narrator (No token selected).");
    } else if (!token) {
        ui.notifications.warn("⚠️ Vox Terminal: Select or hover a token!"); return;
    }
    
    if (command === "forge" || command === "voice") {
        const desc = command === "voice" ? param : "";
        
        updateIngestionProgress(0, 1, actorName);
        statusMessage(`VOX TERMINAL: Re-forging voice for ${actorName}...`, true);
        
        try {
            const resp = await fetch("/api/ingest-actor?force_refresh=true", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    actorId: actorId, name: actorName,
                    lore: lore,
                    artPath: artPath, isMonster: isMonster,
                    customDescription: desc,
                    userId: game.user.id
                })
            });
            const data = await resp.json();
            if (data.status === "created") {
                updateIngestionProgress(1, 1, actorName);
                statusMessage(`✅ VOX TERMINAL: Voice forged for ${actorName}!`, false);
                ui.notifications.info(`🎙️ Vox: Voice seed created for ${actorName}`);
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
                    ENGINES: VOX AUDIOCORE<br/>
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
    // Foundry v14 CHAT_MESSAGE_STYLES: OTHER=0, OOC=1, IC=2, EMOTE=3
    // Use OTHER (0) for generic voice-transcription chat cards
    const messageData = { ...data, style: CONST.CHAT_MESSAGE_STYLES.OTHER };
    try {
        const message = new ChatMessage(messageData);
        return await ChatMessage.create(message.toObject());
    } catch (err) {
        console.warn("Vox | ChatMessage fallback creation:", err);
        return await ChatMessage.create({ ...data, style: CONST.CHAT_MESSAGE_STYLES.OTHER });
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
    voiceConversionEndpoint: "/api/voice-conversion", ingestEndpoint: "/api/ingest-actor", targetVoxVoice: true
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
// Encode raw float32 PCM samples as a WAV file (16-bit mono)
function encodeWAV(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeStr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
    writeStr(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);       // PCM
    view.setUint16(22, 1, true);       // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
}

let voxAudioCtx = null;
let voxScriptNode = null;
let voxSourceNode = null;

(async function initAudio() {
    if (!navigator.mediaDevices?.getUserMedia) return;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        voxAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        voxSourceNode = voxAudioCtx.createMediaStreamSource(stream);

        // ScriptProcessorNode for raw PCM capture (bufferSize=4096 ~= 256ms at 16kHz)
        voxScriptNode = voxAudioCtx.createScriptProcessor(4096, 1, 1);
        voxScriptNode.onaudioprocess = (e) => {
            if (!globalThis.voxState._recording) return;
            const input = e.inputBuffer.getChannelData(0);
            globalThis.voxState._pcmChunks.push(new Float32Array(input));
        };
        voxSourceNode.connect(voxScriptNode);
        voxScriptNode.connect(voxAudioCtx.destination);

        console.log("✅ Vox Audio Engine: WAV capture initialized.");
    } catch (err) { console.error("❌ Vox Audio Fail:", err); }
})();

function toggleFoundryAudio(state) {
    if (typeof game === 'undefined' || !game.webrtc) return;
    try {
        const client = game.webrtc.client;
        // Only toggle if the client is fully initialized (has broadcastStream or peers)
        if (client && typeof client.toggleBroadcast === 'function' && client.broadcastStreams) {
            client.toggleBroadcast(state);
            console.log(`🎙️ Vox | Foundry AV: ${state ? 'UNMUTED' : 'MUTED'} (AI Voice active)`);
        } else {
            console.log(`🎙️ Vox | Foundry AV not ready yet, skipping toggle (${state})`);
        }
    } catch (err) {
        console.warn("🎙️ Vox | Failed to toggle Foundry A/V broadcast:", err);
    }
}

function registerKeybindings() {
    if (globalThis.voxKeybindingsRegistered) return;
    globalThis.voxKeybindingsRegistered = true;
    
    game.settings.register("vox-conjurata", "narratorDeliveryMode", {
        name: "Vox: Narrator Delivery Mode",
        hint: "Choose how narration is delivered. Whisper to GM allows reading aloud manually.",
        scope: "world",
        config: true,
        type: String,
        choices: {
            "speech": "Speech + Chat (Broadcast)",
            "whisper": "Whisper to GM (Text Only, No Audio)"
        },
        default: "speech"
    });

    game.settings.register("vox-conjurata", "narratorVoiceDesc", {
        name: "Narrator Voice Description",
        hint: "Describe the narrator's voice (e.g. 'Deep cinematic male narrator, neutral accent, authoritative'). Used when re-forging the narrator seed.",
        scope: "world",
        config: true,
        type: String,
        default: "Deep cinematic male narrator, neutral accent, clear, authoritative, slightly resonant"
    });

    // Voice Generation Settings
    // When checked: Foundry AV is suppressed, AI voice is generated, transcription goes to chat
    // When unchecked: raw mic passes through Foundry AV, no AI voice, transcription goes to chat
    ["narrator", "character", "npc", "monster"].forEach(cat => {
        game.settings.register("vox-conjurata", `suppressRawVoice_${cat}`, {
            name: `Generate Voice: ${cat === "narrator" ? "Narrator [Y]" : cat === "character" ? "Character [I]" : cat === "npc" ? "NPC Puppet [H]" : "Monster Puppet [H]"}`,
            hint: `When checked, microphone is sent to the AI voice engine. When unchecked, your natural voice passes through. Transcription goes to chat either way.`,
            scope: "client",
            config: true,
            type: Boolean,
            default: cat === "npc" || cat === "monster" // Default ON for puppets
        });
    });

    game.settings.register("vox-conjurata", "llmPathway", {
        scope: "world",
        config: false,
        type: String,
        default: "optimal_cloud"
    });

    game.settings.register("vox-conjurata", "localModelTag", {
        scope: "world",
        config: false,
        type: String,
        default: "eva-qwen2.5-7b"
    });

        game.keybindings.register("vox-conjurata", "toggleVocalMask", {
        name: "Toggle Vocal Mask", editable: [{ key: "KeyV", modifiers: [foundry.helpers.interaction.KeyboardManager.MODIFIER_KEYS.CONTROL, foundry.helpers.interaction.KeyboardManager.MODIFIER_KEYS.SHIFT] }],
        onDown: () => { globalThis.voxLivePanel.isBypass = !globalThis.voxLivePanel.isBypass; globalThis.voxLivePanel.updateBackend(); globalThis.voxLivePanel.render(); }
    });

    // PTT keys are handled via global window listeners in the anonymous function below
    // to ensure stopImmediatePropagation() works reliably across the whole session.
}

(function() {
    if (globalThis.voxHotkeyInitialized) return;
    globalThis.voxHotkeyInitialized = true;

    const activeKeys = new Set();

    const stopAllMics = () => {
        if (activeKeys.size === 0) return;
        console.log("🎙️ Vox: Closing all mics due to focus loss or reset.");
        activeKeys.forEach(code => {
            if (code === "KeyY") globalThis.voxState.narratorActive = false;
            else if (code === "KeyH") globalThis.voxState.puppetActive = false;
            else if (code === "KeyI") globalThis.voxState.playerActive = false;
        });
        activeKeys.clear();
        stopRecording();
        statusMessage("All Mics: CLOSED (Reset)", false);
    };

    window.addEventListener("blur", stopAllMics);

    window.addEventListener("keydown", (event) => {
        if (event.repeat || activeKeys.has(event.code)) return;
        const target = event.target;
        if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable || target.closest(".prosemirror"))) return;
        const code = event.code;
        
        if (code === "KeyY" || code === "KeyH" || code === "KeyI") {
            event.preventDefault();
            event.stopImmediatePropagation();
            activeKeys.add(code);
            console.log(`🎙️ Vox: Hotkey ${code} pressed (PTT OPEN)`);

            // Suppress Foundry broadcast when PTT opens if the checkbox is checked
            // (prevents players hearing both raw mic and AI voice simultaneously)
            const shouldSuppress = (
                code === "KeyH" || // Puppet always suppresses
                (code === "KeyY" && game.settings.get("vox-conjurata", "suppressRawVoice_narrator")) ||
                (code === "KeyI" && game.settings.get("vox-conjurata", "suppressRawVoice_character"))
            );
            if (shouldSuppress) toggleFoundryAudio(false);

            try { playAudio("sounds/lock.wav", 0.1); } catch (e) {}
            
            if (code === "KeyY" && game.user.isGM) {
                globalThis.voxState.narratorActive = true; 
                globalThis.voxState.activeSpeakerName = "Narrator"; 
                globalThis.voxState.activeActorId = "narrator"; 
                globalThis.voxState.activeIsMonster = false;
                globalThis.voxState.useVoxVoice = game.settings.get("vox-conjurata", "suppressRawVoice_narrator") ?? true; 
                globalThis.voxState.useVoxActor = true; 
                startRecording("vox-conjurata-gm-narrate-mic"); 
                statusMessage(`Narrator Mic [Y]: OPEN [Voice: ${globalThis.voxState.useVoxVoice ? 'AI' : 'RAW'}] (Triggers Active)`, true);
            } 
            else if (code === "KeyH" && game.user.isGM) {
                const t = resolveActiveToken(true);
                if (!t) { ui.notifications.warn("❌ Puppeteer: Select/hover NPC!"); activeKeys.delete(code); return; }
                const a = t.actor;
                globalThis.voxState.puppetActive = true; 
                globalThis.voxState.activeSpeakerName = a.name; 
                globalThis.voxState.activeActorId = a.id; 
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(a);
                globalThis.voxState.useVoxVoice = a.getFlag("vox-conjurata", "vox-voice") ?? true;
                globalThis.voxState.useVoxActor = false; 
                startRecording("vox-conjurata-gm-puppet-mic"); 
                statusMessage(`Puppeteer [H] (${globalThis.voxState.activeSpeakerName}): OPEN [Voice: ${globalThis.voxState.useVoxVoice ? 'AI' : 'RAW'}] (Triggers Suppressed)`, true);
            }
            else if (code === "KeyI") {
                const t = resolveActiveToken(false); 
                const a = t?.actor || game.user.character;
                globalThis.voxState.playerActive = true; 
                globalThis.voxState.activeSpeakerName = a?.name || game.user.name; 
                globalThis.voxState.activeActorId = a?.id || game.user.id; 
                globalThis.voxState.activeIsMonster = !!resolveIsMonster(a);
                globalThis.voxState.useVoxVoice = game.settings.get("vox-conjurata", "suppressRawVoice_character") ?? true;
                globalThis.voxState.useVoxActor = true; 

                const target = game.user.targets.first();
                const targetActor = target?.actor;
                if (targetActor && targetActor.type !== "character") {
                    globalThis.voxState.isAutonomousTrigger = targetActor.getFlag("vox-conjurata", "vox-actor") ?? true;
                    globalThis.voxState.targetActorId = targetActor.id;
                    globalThis.voxState.targetVoxVoice = targetActor.getFlag("vox-conjurata", "vox-voice") ?? true;
                    if (globalThis.voxState.isAutonomousTrigger) {
                        statusMessage(`Character Mic [I]: Target ${targetActor.name} [AUTONOMOUS]`, true);
                    }
                } else {
                    globalThis.voxState.isAutonomousTrigger = false;
                }

                startRecording("vox-conjurata-player-mic");
                if (!globalThis.voxState.isAutonomousTrigger) {
                    statusMessage(`Character Mic [I] (${globalThis.voxState.activeSpeakerName}): OPEN [Voice: ${globalThis.voxState.useVoxVoice ? 'AI' : 'NATURAL'}] (Triggers Active)`, true);
                }
            }
        }
    });

    window.addEventListener("keyup", (event) => {
        const code = event.code;
        if (activeKeys.has(code)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            activeKeys.delete(code);
            console.log(`🎙️ Vox: Hotkey ${code} released (PTT CLOSED)`);

            // Re-enable Foundry broadcast that was suppressed on PTT open
            toggleFoundryAudio(true);

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

let isVoxScanning = false;
async function scanActiveSceneTokens() {
    if (!game.user.isGM || !canvas.ready || isVoxScanning) return;
    const tokensToIngest = canvas.tokens.placeables.filter(t => t.actor && !ingestedActors.has(t.actor.id));
    if (tokensToIngest.length === 0) return;

    isVoxScanning = true;
    try {
        let processed = 0;
        const total = tokensToIngest.length;
        
        updateIngestionProgress(0, total, "Initializing...");

        // Strictly sequential one-by-one to avoid Cloudflare 524 timeouts (>100s)
        for (let token of tokensToIngest) {
            const a = token.actor;
            if (ingestedActors.has(a.id)) { processed++; continue; }
            
            updateIngestionProgress(processed, total, a.name);
            
            try { 
                const stats = {
                    race: a.system.details?.race || "Unknown",
                    gender: a.system.details?.gender || a.system.details?.sex || "",
                    level: a.system.details?.level?.value || 0
                };

                await fetch(globalThis.voxState.ingestEndpoint, { 
                    method: "POST", headers: { "Content-Type": "application/json" }, 
                    body: JSON.stringify({
                        actorId: a.id, name: a.name, artPath: a.img, isMonster: resolveIsMonster(a),
                        lore: a.system.details?.biography?.value || a.system.description?.value || "No bio available.",
                        stats: stats,
                        userId: game.user.id
                    }) 
                }); 
                ingestedActors.add(a.id);
            } catch (e) { console.error("Vox Ingestion Error:", e); }
            
            processed++;
            updateIngestionProgress(processed, total, a.name);
        }
        ui.notifications.info("✅ Vox: All seeds ready for gameplay.");
    } finally {
        isVoxScanning = false;
        // Ensure progress bar cleans up if everything is processed
        updateIngestionProgress(1, 1, "Complete"); 
    }
}

// ==========================================
// 4. VOICE REGISTRY MANAGER UI
// = ==========================================

class VoxEngineConfigApp extends Application {
    constructor(options = {}) {
        super(options);
        this.ledgerData = { campaign_pool: 0, individual_allowance: 0, individual_cap: 0 };
    }

    static get defaultOptions() {
        return foundry.utils.mergeObject(super.defaultOptions, {
            id: "vox-engine-config",
            title: "Vox Conjurata: Engine Configuration",
            width: 550,
            height: "auto",
            resizable: true,
            classes: ["vox-ui", "vox-config-app"]
        });
    }

    async getData() {
        try {
            const resp = await fetch(`/api/v1/ledger/balance/${game.user.id}`);
            this.ledgerData = await resp.json();
        } catch (e) {}
        return {
            ledger: this.ledgerData,
            isGM: game.user.isGM,
            localModelTag: game.settings.get("vox-conjurata", "localModelTag") || "eva-qwen2.5-7b",
            llmPathway: game.settings.get("vox-conjurata", "llmPathway") || "optimal_cloud"
        };
    }

    async _renderInner(data) {
        const html = `
        <form class="vox-settings-app" style="padding: 15px; background: #111; color: #eee; font-family: 'Signika', sans-serif;">
            <h2 style="border-bottom: 2px solid #ff6400; padding-bottom: 10px; margin-bottom: 15px; color: #ff6400;">
                <i class="fas fa-brain"></i> Core AI Engine Settings
            </h2>
            <p style="font-size: 11px; color: #888; margin-bottom: 20px;">Offload text token costs and context KV memory chains directly to your local hardware.</p>

            <div class="form-group-box" style="background: rgba(0, 150, 255, 0.05); padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #004d40;">
                <h3 style="font-size: 14px; margin-top: 0; color: #00ffcc;"><i class="fas fa-robot"></i> Generate Voice</h3>
                <p style="font-size: 10px; color: #888; margin-bottom: 10px;">When checked, your mic feeds the AI voice engine and Foundry AV is suppressed to prevent double audio. When unchecked, your natural voice passes through. Transcription goes to chat either way.</p>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <label style="font-size: 12px;"><input type="checkbox" class="vox-suppress-toggle" data-cat="narrator" ${getSafeSetting("vox-conjurata", "suppressRawVoice_narrator") ? "checked" : ""}> <i class="fas fa-robot"></i> Narrator [Y]</label>
                    <label style="font-size: 12px;"><input type="checkbox" class="vox-suppress-toggle" data-cat="character" ${getSafeSetting("vox-conjurata", "suppressRawVoice_character") ? "checked" : ""}> <i class="fas fa-robot"></i> Character [I]</label>
                    <label style="font-size: 12px;"><input type="checkbox" class="vox-suppress-toggle" data-cat="npc" ${getSafeSetting("vox-conjurata", "suppressRawVoice_npc") ? "checked" : ""}> <i class="fas fa-robot"></i> NPC [H]</label>
                    <label style="font-size: 12px;"><input type="checkbox" class="vox-suppress-toggle" data-cat="monster" ${getSafeSetting("vox-conjurata", "suppressRawVoice_monster") ? "checked" : ""}> <i class="fas fa-robot"></i> Monster [H]</label>
                </div>
            </div>

            <div class="form-group-box" style="background: rgba(255,100,0,0.05); padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #333;">
                <label style="display: block; font-weight: bold; margin-bottom: 8px;"><i class="fas fa-network-wired"></i> Text Orchestration Pathway</label>
                <select name="llm_pathway_mode" id="vox-llm-mode-select" style="width: 160px; background: #222; color: #eee; border: 1px solid #444; height: 32px; border-radius: 4px;">
                    <option value="optimal_cloud" ${data.llmPathway === 'optimal_cloud' ? 'selected' : ''}>Vox Hosted Optimal Tier (Cloud API Base + Fee)</option>
                    <option value="byo_local_brain" ${data.llmPathway === 'byo_local_brain' ? 'selected' : ''}>Bring Your Own Brain (Localhost Loopback - Fee Only)</option>
                </select>
            </div>

            <div id="vox-local-brain-config-pane" style="display: ${data.llmPathway === 'byo_local_brain' ? 'block' : 'none'}; margin-bottom: 20px; padding: 15px; border: 1px dashed #ff6400; border-radius: 8px; background: rgba(0,0,0,0.3);">
                <h3 style="font-size: 14px; margin-top: 0;"><i class="fas fa-microchip"></i> Local Loopback Environment</h3>
                
                <div class="form-row" style="margin-bottom: 15px;">
                    <label style="display: block; font-size: 11px; color: #aaa; margin-bottom: 5px;">Model Identifier Tag</label>
                    <input type="text" id="vox-model-tag-input" name="local_model_tag" value="${data.localModelTag}" style="width: 100%; background: #000; color: #00ff88; border: 1px solid #444; font-family: monospace; height: 28px; padding: 0 8px;">
                </div>
                
                <div class="recommendation-badge-container" style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 15px;">
                    <button type="button" class="badge-btn active-rec" data-tag="eva-qwen2.5-7b" style="font-size: 10px; background: #333; color: #eee; border: 1px solid #ff6400; border-radius: 4px; padding: 2px 8px; cursor: pointer;">
                        <i class="fas fa-star"></i> Eva Qwen 2.5 7B (Recommended)
                    </button>
                    <button type="button" class="badge-btn" data-tag="llama3.2:3b" style="font-size: 10px; background: #333; color: #eee; border: 1px solid #444; border-radius: 4px; padding: 2px 8px; cursor: pointer;">
                        Llama 3.2 3B (Light)
                    </button>
                </div>
                
                <div class="billing-notice-box" style="font-size: 10px; background: rgba(0,255,136,0.1); padding: 10px; border-radius: 4px; border-left: 3px solid #00ff88; color: #00ff88;">
                    <i class="fas fa-info-circle"></i> 
                    <strong>Financial Matrix Update:</strong> Base text compilation fee reduced to <strong>$0.0000</strong>. Only <strong>$0.0030 Orchestrator Fee</strong> per call.
                </div>
            </div>

            <h2 style="border-bottom: 2px solid #00ff88; padding-bottom: 10px; margin-bottom: 15px; color: #00ff88; margin-top: 30px;">
                <i class="fas fa-wallet"></i> Co-Op Funding Ledger
            </h2>
            
            <div style="display: flex; gap: 15px; margin-bottom: 20px;">
                <div style="flex: 1; background: #222; padding: 12px; border-radius: 8px; text-align: center; border: 1px solid #444;">
                    <div style="font-size: 10px; color: #888; text-transform: uppercase;">Campaign Pool</div>
                    <div style="font-size: 20px; font-weight: bold; color: #00ff88;">$${(data.ledger.campaign_pool ?? 0).toFixed(6)}</div>
                    ${data.isGM ? `<button type="button" class="topup-btn" style="margin-top: 8px; font-size: 9px; background: #333; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 2px 8px; cursor: pointer;">+ TOP UP</button>` : ''}
                </div>
                <div style="flex: 1; background: #222; padding: 12px; border-radius: 8px; text-align: center; border: 1px solid #444;">
                    <div style="font-size: 10px; color: #888; text-transform: uppercase;">Your Allowance</div>
                    <div style="font-size: 20px; font-weight: bold; color: #ff6400;">$${(data.ledger.individual_allowance ?? 0).toFixed(6)}</div>
                    <div style="font-size: 9px; color: #666; margin-top: 5px;">Cap: $${(data.ledger.individual_cap ?? 0).toFixed(6)}</div>
                </div>
            </div>

            ${data.isGM ? `
            <div class="form-group-box" style="background: rgba(233, 30, 99, 0.1); padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #e91e63;">
                <h3 style="font-size: 14px; margin-top: 0; color: #ff80ab;"><i class="fas fa-user-shield"></i> GM Admin Quick-Fund</h3>
                <p style="font-size: 10px; color: #888; margin-bottom: 15px;">Inject test credits directly into your wallet or the campaign pool to bypass billing for testing.</p>
                <div style="display: flex; gap: 10px; align-items: flex-end;">
                    <div style="flex: 1;">
                        <label style="font-size: 11px; color: #aaa; display: block; margin-bottom: 4px;">Admin: Target Account</label>
                        <select id="admin-inline-target" style="width: 160px; background: #222; color: #eee; border: 1px solid #444; height: 28px; border-radius: 4px;">
                            <option value="${game.user.id}">Personal Wallet (DM)</option>
                            <option value="POOL">Shared Campaign Pool</option>
                        </select>
                    </div>
                    <div style="width: 100px;">
                        <label style="font-size: 11px; color: #aaa; display: block; margin-bottom: 4px;">Amount ($)</label>
                        <input type="number" id="admin-inline-amount" value="100" step="10" style="width: 100%; background: #000; color: #fff; border: 1px solid #444; height: 28px; padding: 0 8px;">
                    </div>
                    <button type="button" id="admin-inline-grant-btn" style="background: #e91e63; color: white; border: none; height: 28px; padding: 0 15px; border-radius: 4px; font-weight: bold; cursor: pointer;">GRANT</button>
                </div>
            </div>` : ""}

            <div style="background: rgba(255,255,255,0.02); padding: 15px; border-radius: 8px; border: 1px solid #333;">
                <label style="display: block; font-weight: bold; margin-bottom: 12px;">Manage Your Session Budget</label>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <input type="number" id="vox-allowance-input" step="0.5" min="0" value="${(data.ledger.individual_cap ?? 0)}" style="flex: 1; background: #000; color: #eee; border: 1px solid #444; height: 32px; padding: 0 10px; border-radius: 4px;">
                    <button type="button" class="set-allowance-btn" style="background: #ff6400; color: white; border: none; height: 32px; padding: 0 15px; border-radius: 4px; font-weight: bold; cursor: pointer;">UPDATE BUDGET</button>
                </div>
                <p style="font-size: 10px; color: #666; margin-top: 8px; font-style: italic;">Note: Budget is drawn autonomously from the Campaign Pool. No DM veto required.</p>
            </div>

            <div class="vox-settings-footer" style="margin-top: 30px; display: flex; justify-content: space-between; align-items: center;">
                ${data.isGM ? `<button type="button" class="forge-scene-btn" style="background: #ff6400; color: white; border: none; padding: 10px 20px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px;"><i class="fas fa-hammer"></i> FORGE SCENE VOICES</button>` : '<span></span>'}
                <button type="submit" class="save-btn" style="background: #00ff88; color: #111; border: none; padding: 10px 25px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 14px;">
                    <i class="fas fa-save"></i> Commit Engine Settings
                </button>
            </div>
        </form>
        `;
        return $(html);
    }

    activateListeners(html) {
        super.activateListeners(html);
        
        $(html).find("#vox-llm-mode-select").change(ev => {
            const val = ev.target.value;
            $(html).find("#vox-local-brain-config-pane").toggle(val === 'byo_local_brain');
        });

        $(html).find("#admin-inline-grant-btn").click(async (ev) => {
            const target = $(html).find("#admin-inline-target").val();
            const amount = parseFloat($(html).find("#admin-inline-amount").val());
            const endpoint = (target === "POOL") ? "/api/v1/admin/set-pool" : "/api/v1/admin/modify-credits";
            const payload = (target === "POOL") ? {amount: amount} : {targetUserId: target, amount: amount, description: "Admin Quick-Fund"};
            
            try {
                const resp = await fetch(endpoint, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
                if (resp.ok) {
                    ui.notifications.info(`🛡️ ADMIN: Successfully added $${amount} to ${target}`);
                    this.render(true);
                } else {
                    ui.notifications.error("🛡️ ADMIN: Funding failed. Check backend logs.");
                }
            } catch (err) {
                console.error("🎙️ Vox | Admin Quick-Fund Error:", err);
            }
        });

        $(html).find(".badge-btn").click(ev => {
            $(html).find("#vox-model-tag-input").val(ev.currentTarget.dataset.tag);
        });

        $(html).find(".admin-topup-btn").click(async () => {
            new Dialog({
                title: "🛡️ ADMIN: Grant Test Credits",
                content: `<div style="padding: 10px;">
                    <p style="font-size: 11px; color: #888;">Add test dollars directly to your personal wallet to run the program.</p>
                    <div class="form-group"><label>Target User ID:</label><input type="text" id="admin-target-id" value="${game.user.id}"></div>
                    <div class="form-group"><label>Amount ($):</label><input type="number" id="admin-amount" value="100.00" step="10"></div>
                </div>`,
                buttons: {
                    grant: {
                        label: "Grant Credits",
                        callback: async (html) => {
                            const target = html.find("#admin-target-id").val();
                            const amount = parseFloat(html.find("#admin-amount").val());
                            const resp = await fetch("/api/v1/admin/modify-credits", {
                                method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({targetUserId: target, amount: amount, description: "Test Funding"})
                            });
                            if (resp.ok) {
                                ui.notifications.info(`🛡️ ADMIN: Granted $${amount} to ${target}`);
                                this.render(true);
                            }
                        }
                    }
                }
            }).render(true);
        });

        $(html).find(".vox-suppress-toggle").change(async (ev) => {
            const cat = ev.currentTarget.dataset.cat;
            const val = ev.currentTarget.checked;
            await game.settings.set("vox-conjurata", `suppressRawVoice_${cat}`, val);
            ui.notifications.info(`✅ ${cat.charAt(0).toUpperCase() + cat.slice(1)} Voice: ${val ? "AI-GENERATED (Foundry AV muted)" : "NATURAL (pass through)"}`);
        });

        $(html).find(".set-allowance-btn").click(async () => {
            const amount = parseFloat($(html).find("#vox-allowance-input").val());
            try {
                const resp = await fetch("/api/v1/ledger/allowance", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({userId: game.user.id, amount: amount})
                });
                if (resp.ok) {
                    ui.notifications.info(`✅ Session budget updated to $${(amount ?? 0).toFixed(6)}`);
                    this.render(true);
                } else {
                    const err = await resp.json();
                    ui.notifications.error(`❌ ${err.detail}`);
                }
            } catch (e) {
                ui.notifications.error("❌ Failed to update allowance.");
            }
        });

        if (game.user.isGM) {
            $(html).find(".forge-scene-btn").click(async () => {
                const sceneActors = new Set(canvas.tokens.placeables.map(t => t.actor).filter(a => a));
                if (sceneActors.size === 0) { ui.notifications.warn("No actors found in scene."); return; }
                
                ui.notifications.info(`🔨 Forging ${sceneActors.size} scene voices...`);
                let count = 0;
                for (let a of sceneActors) {
                    try {
                        const stats = { hp: a.system.attributes?.hp?.value || 10, level: a.system.details?.level || 1 };
                        await fetch("/api/ingest-actor?force_refresh=true", { 
                            method: "POST", headers: { "Content-Type": "application/json" }, 
                            body: JSON.stringify({
                                actorId: a.id, name: a.name, artPath: a.img, isMonster: resolveIsMonster(a),
                                lore: a.system.details?.biography?.value || a.system.description?.value || "No bio available.",
                                stats: stats,
                                userId: game.user.id
                            }) 
                        });
                        count++;
                        ui.notifications.info(`[${count}/${sceneActors.size}] Forged: ${a.name}`);
                    } catch (e) { console.error(e); }
                }
                ui.notifications.info("✅ Scene Forge Complete.");
            });

            $(html).find(".topup-btn").click(async () => {
                new Dialog({
                    title: "Top Up Campaign Pool",
                    content: `<div style="padding: 10px;"><label>Amount ($): </label><input type="number" id="topup-amount" value="10.00" step="1"></div>`,
                    buttons: {
                        topup: {
                            label: "Add Funds",
                            callback: async (html) => {
                                const amount = parseFloat($(html).find("#topup-amount").val());
                                await fetch("/api/v1/ledger/topup", {
                                    method: "POST",
                                    headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify({amount: amount})
                                });
                                this.render(true);
                            }
                        }
                    }
                }).render(true);
            });
        }

        $(html).find(".save-btn").click(async (ev) => {
            ev.preventDefault();
            const pathway = $(html).find("[name='llm_pathway_mode']").val();
            const modelTag = $(html).find("[name='local_model_tag']").val();
            
            await game.settings.set("vox-conjurata", "llmPathway", pathway);
            await game.settings.set("vox-conjurata", "localModelTag", modelTag);
            
            ui.notifications.info("✅ Engine settings committed.");
            this.close();
        });
    }
}

class VoxVoiceManager extends Application {
    constructor(options={}) {
        super(options);
        this.registryData = [];
    }

    static get defaultOptions() {
        return foundry.utils.mergeObject(super.defaultOptions, {
            id: "vox-voice-manager",
            title: "Vox Neural Voice Registry",
            width: 500,
            height: 600,
            resizable: true,
            classes: ["vox-ui", "vox-manager"],
            popOut: true,
            minimizable: true
        });
    }

    async _renderInner(data) {
        const resp = await fetch("/api/v1/registry");
        const registry = await resp.json();
        this.registryData = Object.entries(registry).map(([id, data]) => ({ id, ...data }));
        
        const narratorEntry = this.registryData.find(e => e.id === "narrator");
        const otherEntries = this.registryData.filter(e => e.id !== "narrator");
        const deliveryMode = game.settings.get("vox-conjurata", "narratorDeliveryMode");

        let html = `
            <div style="padding: 10px; background: #1a1a1a; color: #eee; height: 100%; display: flex; flex-direction: column; font-family: 'Signika', sans-serif;">
                <div style="margin-bottom: 15px; border-bottom: 1px solid #ff6400; padding-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
                    <h2 style="margin: 0; color: #ff6400;"><i class="fas fa-dna"></i> Neural Voice Registry</h2>
                    <button class="vox-refresh-btn" style="width: auto; padding: 2px 8px; background: #333; border: 1px solid #555;"><i class="fas fa-sync"></i></button>
                </div>

                <div class="vox-narrator-config" style="background: rgba(255,100,0,0.05); border: 1px solid #ff6400; border-radius: 4px; padding: 12px; margin-bottom: 20px;">
                    <h3 style="margin: 0 0 10px 0; color: #ff6400; font-size: 16px;"><i class="fas fa-comment-dots"></i> Narrator Configuration</h3>
                    
                    <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 13px;">Delivery Mode:</span>
                            <button class="vox-toggle-mode-btn" style="width: auto; padding: 2px 10px; font-size: 11px; background: #333; color: #00ff88; border: 1px solid #444; border-radius: 4px; cursor: pointer;">
                                ${deliveryMode === 'speech' ? '🔊 Speech + Chat' : '🤫 Whisper to GM'}
                            </button>
                        </div>
                        <div style="font-size: 11px; color: #aaa; font-style: italic;">
                            ${narratorEntry ? `"${narratorEntry.voice_prompt}"` : 'No narrator voice profile forged yet.'}
                        </div>
                        <div style="margin-top: 8px;">
                            <label style="font-size: 11px; color: #ccc; display: block; margin-bottom: 4px;">Voice Description (used on re-forge):</label>
                            <textarea class="vox-narrator-desc" style="width: 100%; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; padding: 6px; font-size: 12px; resize: vertical; min-height: 50px;" placeholder="Describe the narrator voice you want...">${game.settings.get("vox-conjurata", "narratorVoiceDesc") || ''}</textarea>
                        </div>
                    </div>

                    <div style="display: flex; gap: 8px;">
                        ${narratorEntry ? `<button class="vox-play-seed" data-actor-id="narrator" style="height: 28px; line-height: 1; font-size: 12px; flex: 1; background: #222;"><i class="fas fa-play"></i> Preview</button>` : ''}
                        ${narratorEntry?.approved ? `<span style="height: 28px; line-height: 28px; flex: 1; text-align: center; font-size: 12px; color: #00ff88;"><i class="fas fa-check-circle"></i> Approved</span>` : (narratorEntry ? `<button class="vox-approve-voice" data-actor-id="narrator" style="height: 28px; line-height: 1; font-size: 12px; flex: 1; background: #003300; color: #00ff88; border: 1px solid #00aa44;"><i class="fas fa-thumbs-up"></i> Approve</button>` : '')}
                        <button class="vox-save-narrator-desc" style="height: 28px; line-height: 1; font-size: 12px; flex: 1; background: #333; color: #ff6400; border: 1px solid #ff6400;"><i class="fas fa-save"></i> Save</button>
                        <button class="vox-clone-mic" data-actor-id="narrator" data-name="Narrator" style="height: 28px; line-height: 1; font-size: 12px; flex: 1; background: #004d00; color: #00ff88; border: 1px solid #00aa44;"><i class="fas fa-microphone"></i> Clone from Mic</button>
                        <button class="vox-regen-actor" data-actor-id="narrator" style="height: 28px; line-height: 1; font-size: 12px; flex: 2; background: #b34a00; color: white;"><i class="fas fa-redo"></i> Re-Forge Narrator</button>
                    </div>
                </div>

                <div style="flex: 1; overflow-y: auto;">
                    <h3 style="font-size: 14px; margin: 0 0 10px 0; color: #888; text-transform: uppercase; letter-spacing: 1px;">Character Seeds</h3>
                    <ul style="list-style: none; padding: 0; margin: 0;">
        `;

        if (otherEntries.length === 0) {
            html += `<li style="text-align: center; color: #888; padding: 20px;">No characters neural-forged yet.</li>`;
        }

        for (let entry of otherEntries) {
            const actor = game.actors.get(entry.id);
            const name = actor?.name || entry.id;
            html += `
                <li style="background: rgba(255,255,255,0.03); margin-bottom: 10px; border-radius: 4px; padding: 12px; border-left: 3px solid #ff6400; box-shadow: 0 2px 5px rgba(0,0,0,0.3);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <strong style="color: #fff; font-size: 14px;">${name}</strong>
                        <span style="font-size: 10px; color: #ff6400; background: rgba(255,100,0,0.1); padding: 2px 6px; border-radius: 10px;">${entry.engine}</span>
                    </div>
                    <div style="font-size: 11px; color: #aaa; font-style: italic; margin-bottom: 10px; line-height: 1.4; border-left: 2px solid #333; padding-left: 8px;">"${entry.voice_prompt}"</div>
                    <div style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px;">
                        <input type="text" class="vox-voice-desc-input" data-actor-id="${entry.id}" placeholder="Describe voice (e.g. 'deep gravelly, like a veteran soldier')" style="width: 100%; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; padding: 4px 8px; font-size: 12px; box-sizing: border-box;">
                        <button class="vox-forge-with-desc" data-actor-id="${entry.id}" data-name="${name}" style="height: 24px; line-height: 1; font-size: 11px; background: #664400; color: #ffaa22; border: 1px solid #aa7700;"><i class="fas fa-pen"></i> Forge with Description</button>
                    </div>
                    <div style="display: flex; gap: 6px; margin-bottom: 8px;">
                        <input type="text" class="vox-tts-test-input" data-actor-id="${entry.id}" placeholder="Type test sentence, then click play..." style="flex: 1; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; padding: 4px 8px; font-size: 12px;">
                        <button class="vox-tts-test-play" data-actor-id="${entry.id}" style="height: 26px; line-height: 1; font-size: 11px; flex: 0 0 auto; background: #222; color: #00ccff; border: 1px solid #0088aa;"><i class="fas fa-play"></i> Test</button>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="vox-play-seed" data-actor-id="${entry.id}" style="height: 26px; line-height: 1; font-size: 11px; flex: 1; background: #222;"><i class="fas fa-play"></i> Preview</button>
                        <button class="vox-clone-mic" data-actor-id="${entry.id}" data-name="${name}" style="height: 26px; line-height: 1; font-size: 11px; flex: 1; background: #004d00; color: #00ff88; border: 1px solid #00aa44;"><i class="fas fa-microphone"></i> Clone</button>
                        ${entry.approved ? `<span style="flex: 1; text-align: center; font-size: 11px; color: #00ff88; line-height: 26px;"><i class="fas fa-check-circle"></i> Approved</span>` : `<button class="vox-approve-voice" data-actor-id="${entry.id}" style="height: 26px; line-height: 1; font-size: 11px; flex: 1; background: #003300; color: #00ff88; border: 1px solid #00aa44;"><i class="fas fa-thumbs-up"></i> Approve</button>`}
                        <button class="vox-regen-actor" data-actor-id="${entry.id}" style="height: 26px; line-height: 1; font-size: 11px; flex: 1; background: #b34a00; color: white;"><i class="fas fa-redo"></i> Re-Forge</button>
                    </div>
                </li>
            `;
        }

        html += `</ul></div></div>`;
        return $(html);
    }

    activateListeners(html) {
        super.activateListeners(html);
        $(html).find('.vox-refresh-btn').click(() => this.render(true));
        
        $(html).find('.vox-play-seed').click(async (ev) => {
            const id = ev.currentTarget.dataset.actorId;
            const audio = new Audio(`/api/v1/registry/audio/${id}?t=${Date.now()}`);
            audio.play().catch(e => ui.notifications.error("Failed to play preview audio."));
        });

        $(html).find('.vox-regen-actor').click(async (ev) => {
            const id = ev.currentTarget.dataset.actorId;

            // Narrator doesn't have a token — forge directly with custom description
            if (id === "narrator") {
                const desc = game.settings.get("vox-conjurata", "narratorVoiceDesc") || "Deep cinematic male narrator, neutral accent, clear, authoritative, slightly resonant";
                ui.notifications.info("🎙️ Forging narrator voice...");
                try {
                    const resp = await fetch("/api/ingest-actor?force_refresh=true", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            actorId: "narrator", name: "Narrator",
                            lore: "The narrator of the story.",
                            artPath: "", isMonster: false,
                            customDescription: desc,
                            userId: game.user.id
                        })
                    });
                    if (resp.ok) {
                        ui.notifications.info("✅ Narrator voice forged!");
                        this.render(true);
                    } else {
                        ui.notifications.error("Failed to forge narrator voice.");
                    }
                } catch (e) {
                    console.error(e);
                    ui.notifications.error("Network error during narrator forge.");
                }
                return;
            }

            const token = canvas.tokens.placeables.find(t => t.actor?.id === id);
            if (!token) {
                ui.notifications.warn("⚠️ Character token must be on the current scene to re-forge.");
                return;
            }
            ui.notifications.info(`🎙️ Re-forging voice identity for ${token.actor.name}...`);
            try {
                const resp = await fetch("/api/ingest-actor?force_refresh=true", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        actorId: token.actor.id, name: token.actor.name,
                        lore: token.actor.system.details?.biography?.value || "",
                        artPath: token.actor.img, isMonster: resolveIsMonster(token.actor),
                        userId: game.user.id
                    })
                });
                if (resp.ok) {
                    ui.notifications.info(`✅ Successfully re-forged ${token.actor.name}`);
                    this.render(true);
                } else {
                    ui.notifications.error("Failed to re-forge voice seed.");
                }
            } catch (e) {
                console.error(e);
                ui.notifications.error("Network error during re-forge.");
            }
        });

        // Save narrator voice description
        $(html).find('.vox-save-narrator-desc').click(async (ev) => {
            const desc = $(html).find('.vox-narrator-desc').val();
            await game.settings.set("vox-conjurata", "narratorVoiceDesc", desc);
            ui.notifications.info("✅ Narrator voice description saved.");
            this.render(true);
        });

        // Approve voice — marks the seed as approved after user playback check
        $(html).find('.vox-approve-voice').click(async (ev) => {
            const actorId = ev.currentTarget.dataset.actorId;
            try {
                const resp = await fetch("/api/v1/approve-voice", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ actorId })
                });
                if (resp.ok) {
                    ui.notifications.success(`✅ Voice approved!`);
                    this.render(true);
                } else {
                    ui.notifications.error("❌ Failed to approve voice.");
                }
            } catch (e) {
                ui.notifications.error("❌ Network error during approval.");
            }
        });

        // Test voice — type a sentence and hear it spoken
        $(html).find('.vox-tts-test-play').click(async (ev) => {
            const actorId = ev.currentTarget.dataset.actorId;
            const input = $(html).find(`.vox-tts-test-input[data-actor-id="${actorId}"]`);
            const text = input.val().trim();
            if (!text) {
                ui.notifications.warn("⚠️ Type a test sentence first.");
                return;
            }
            ui.notifications.info(`🔊 Testing voice...`);
            try {
                const resp = await fetch("/api/v1/tts-chunk", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ actor_id: actorId, text, dsp_presets: {} })
                });
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.status === "success" && data.audio_data) {
                        new Audio(data.audio_data).play();
                    } else {
                        ui.notifications.error("❌ TTS generation failed.");
                    }
                } else {
                    ui.notifications.error("❌ TTS request failed.");
                }
            } catch (e) {
                ui.notifications.error("❌ Network error.");
            }
        });

        // Forge with custom voice description text
        $(html).find('.vox-forge-with-desc').click(async (ev) => {
            const id = ev.currentTarget.dataset.actorId;
            const name = ev.currentTarget.dataset.name || id;
            const desc = $(html).find(`.vox-voice-desc-input[data-actor-id="${id}"]`).val();
            if (!desc || !desc.trim()) {
                ui.notifications.warn("⚠️ Enter a voice description first.");
                return;
            }
            ui.notifications.info(`🎙️ Forging voice for ${name}...`);
            try {
                const resp = await fetch("/api/ingest-actor?force_refresh=true", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        actorId: id, name: name,
                        lore: "", artPath: "", isMonster: false,
                        customDescription: desc.trim(),
                        userId: game.user.id
                    })
                });
                if (resp.ok) {
                    const data = await resp.json();
                    ui.notifications.success(`✅ Voice forged for ${name}!`);
                    // Auto-play for approval check
                    setTimeout(() => {
                        const audio = new Audio(`/api/v1/registry/audio/${id}?t=${Date.now()}`);
                        audio.play().catch(() => {});
                    }, 500);
                    this.render(true);
                } else {
                    ui.notifications.error("❌ Failed to forge voice.");
                }
            } catch (e) {
                ui.notifications.error("❌ Network error during forge.");
            }
        });

        // Clone voice from mic — click handler for both narrator and character buttons
        $(html).find('.vox-clone-mic').click(async (ev) => {
            const actorId = ev.currentTarget.dataset.actorId;
            const name = ev.currentTarget.dataset.name || actorId;

            // Voice cloning script that captures a range of emotions
            const CLONE_SCRIPT =
`Welcome, friend. It's good to see you.
(neutral, warm)

I can't believe this is happening! This is incredible news!
(happy, excited)

Why would you do this? After everything we've been through...
(sad, hurt)

Enough! This ends now. You will not take another step forward.
(angry, commanding)

Hmm, I wonder what secrets lie beyond that door.
(curious, thoughtful)

Together, we can face whatever comes our way.
(warm, determined)`;

            // First, prompt the user with a dialog showing the script
            const ready = await new Promise((resolve) => {
                new Dialog({
                    title: `Clone Voice: ${name}`,
                    content: `
                        <div style="padding: 10px;">
                            <p style="margin-bottom: 10px; color: #ccc;">Read the following script aloud to capture emotional range. The recording will take about 30 seconds.</p>
                            <div style="background: #111; border: 1px solid #444; border-radius: 6px; padding: 12px; margin-bottom: 12px; font-family: 'Courier New', monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; color: #ddd;">
${CLONE_SCRIPT}
                            </div>
                            <p style="color: #888; font-size: 11px;">Click <strong>Start Recording</strong> when ready, then read the script naturally. A 5-second countdown will appear.</p>
                        </div>
                    `,
                    buttons: {
                        cancel: { label: "Cancel", callback: () => resolve(false) },
                        record: { label: "🎙️ Start Recording", callback: () => resolve(true) }
                    },
                    default: "record"
                }).render(true);
            });
            if (!ready) return;

            // Request mic access
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            } catch (e) {
                ui.notifications.error("🎙️ Microphone access denied.");
                return;
            }

            // 5-second countdown
            for (let i = 5; i > 0; i--) {
                ui.notifications.info(`⏱️ Recording starts in ${i}...`);
                await new Promise(r => setTimeout(r, 1000));
            }

            const chunks = [];
            const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
            recorder.ondataavailable = (e) => chunks.push(e.data);
            recorder.onstop = async () => {
                const blob = new Blob(chunks, { type: 'audio/webm' });
                const formData = new FormData();
                formData.append("audio_blob", blob, "recording.webm");
                formData.append("actorId", actorId);

                ui.notifications.info(`🎙️ Processing voice clone for ${name}...`);
                try {
                    const resp = await fetch("/api/clone-voice", {
                        method: "POST", body: formData
                    });
                    if (resp.ok) {
                        ui.notifications.success(`✅ Voice cloned for ${name}!`);
                        // Auto-play the seed for approval
                        setTimeout(() => {
                            const audio = new Audio(`/api/v1/registry/audio/${actorId}?t=${Date.now()}`);
                            audio.play().catch(() => {});
                        }, 500);
                        // Show approval notification
                        ui.notifications.info(`👂 Listen to the preview above, then click "Approve" if it sounds good.`);
                        this.render(true);
                    } else {
                        ui.notifications.error("❌ Voice cloning failed.");
                    }
                } catch (e) {
                    ui.notifications.error("❌ Network error during cloning.");
                }
                stream.getTracks().forEach(t => t.stop());
            };

            ui.notifications.info(`🎙️ Recording for ${name} — read the script naturally...`);
            recorder.start();
            setTimeout(() => recorder.stop(), 35000); // 35 seconds to read the script

            // Visual feedback — show recording time remaining
            const btn = ev.currentTarget;
            const orig = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-circle" style="color: #ff4444;"></i> Recording (35s)...';
            btn.disabled = true;
            setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; }, 40000);
        });

/**
 * Autonomous Update Listener
 * Handles incoming images and SFX triggers from the Orchestrator.
 */
Hooks.on("ready", () => {
    game.socket.on("module.vox-conjurata", async (data) => {
        if (data.type === "balance-refresh") {
            // When anyone sends/receives credits, refresh all clients' HUD
            try { voxHUD.render(true); } catch(e) {}
        } else if (data.type === "display-image") {
            const { imageType, imageUrl, prompt } = data;
            
            if (imageType === "atmosphere") {
                // 1. Popup Journal for players
                const content = `<img src="${imageUrl}" style="width: 100%; border: none;"><p><i>${prompt}</i></p>`;
                ChatMessage.create({
                    content: content,
                    speaker: { alias: "Vox Narrator" },
                    flags: { "vox-conjurata": { isMood: true } }
                });

                // 2. Update persistent Mood Scene
                const moodScene = game.scenes.find(s => s.name === "Vox Mood Board");
                if (moodScene) {
                    await moodScene.update({ background: { src: imageUrl } });
                    ui.notifications.info("🖼️ Vox: Mood Board updated.");
                }
            } else if (imageType === "effect") {
                // 3. Create Active Tile on current map
                const scene = game.scenes.active;
                const tileData = {
                    img: imageUrl,
                    width: scene.width / 4,
                    height: scene.height / 4,
                    x: scene.width / 2 - (scene.width / 8),
                    y: scene.height / 2 - (scene.height / 8),
                    alpha: 0.7,
                    overhead: true,
                    flags: { "vox-conjurata": { isEffect: true } }
                };
                await scene.createEmbeddedDocuments("Tile", [tileData]);
                ui.notifications.info(`✨ Vox: Atmospheric effect '${prompt}' placed.`);
            }
        }
    });
});

/**
 * Tactical Eye: Screenshot & Scan Logic
 */
async function captureAndScanMap() {
    // Pre-flight Credit Check
    try {
        const bResp = await fetch(`/api/v1/ledger/balance/${game.user.id}`);
        const balance = await bResp.json();
        if (balance.is_out_of_credits) {
            new Dialog({
                title: "⚠️ Wallet Empty",
                content: `<div style="padding: 10px;">
                    <p><strong>Your individual session credit allowance has run out.</strong></p>
                    <p style="font-size: 11px; color: #888;">Vision scans and image generation require active credits. You can ask your DM to raise your limit or top-up the Campaign Pool.</p>
                </div>`,
                buttons: {
                    topup: {
                        label: "Buy $10 Voucher",
                        callback: () => voxHUD.element.find(".vox-buy-credits").click()
                    },
                    close: { label: "Close" }
                }
            }).render(true);
            return;
        }
    } catch (e) {}

    ui.notifications.info("👁️ Vox: Scanning battlemap...");
    
    // 1. Capture the canvas as a base64 image
    // Note: We use the stage to get everything (tokens, background, effects)
    const screenshot = await canvas.app.renderer.extract.base64(canvas.app.stage);
    
    // 2. Prepare request
    const payload = {
        sceneId: canvas.scene.id,
        imagePath: `current_view_${Date.now()}.png`,
        screenshot: screenshot, // Orchestrator needs to handle base64 if we send it directly
        userId: game.user.id
    };

    // Since our existing /api/scan-battlemap expects a path, let's upload a blob instead
    const blob = await (await fetch(screenshot)).blob();
    const formData = new FormData();
    formData.append("file", blob, "scan.png");
    formData.append("sceneId", canvas.scene.id);
    formData.append("userId", game.user.id);

    try {
        const resp = await fetch("/api/scan-battlemap", { method: "POST", body: formData });
        const data = await resp.json();
        
        if (data.status === "success") {
            // 3. Display Tactical Advice Chat Card
            ChatMessage.create({
                content: `
                    <div class="vox-tactical-card" style="border: 2px solid #ff6400; background: rgba(20,20,20,0.9); padding: 10px; border-radius: 5px;">
                        <h3 style="color: #ff6400; border-bottom: 1px solid #ff6400; margin-bottom: 10px;"><i class="fas fa-eye"></i> Tactical Analysis</h3>
                        <p style="color: #eee; font-size: 13px;">${data.tactical_analysis || "No threats detected."}</p>
                    </div>
                `,
                speaker: { alias: "Monster Sight" },
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        }
    } catch (err) { console.error("❌ Vox Scan fail:", err); }
}
window.VoxVoiceManager = VoxVoiceManager;

Hooks.on("getSceneControlButtons", (controls) => {
    
    
    // Foundry v14 Build 363 Compatibility: controls is now a Record/Object
    const tokenControls = Array.isArray(controls) ? controls.find(c => c.name === "token") : (controls.token || controls.tokens);
    
    if (tokenControls) {
        
        const tools = Array.isArray(tokenControls.tools) ? tokenControls.tools : null;
        const addTool = (tool) => {
            if (tools) {
                
                tools.push(tool);
            } else {
                
                tokenControls.tools[tool.name] = tool;
            }
        };

        if (game.user.isGM) {
            addTool({
                name: "vox-scan",
                title: "Tactical Eye Scan",
                icon: "fa-solid fa-eye",
                onClick: () => captureAndScanMap(),
                button: true,
                order: 50
            });
            addTool({
                name: "vox-panel",
                title: "Vox Live Panel",
                icon: "fa-solid fa-microphone-lines",
                button: true,
                visible: true,
                onClick: () => { try { globalThis.voxLivePanel.render(true); } catch(err) { console.error("🎙️ Vox | Failed to open Live Panel:", err); } },
                order: 51
            });
            addTool({
                name: "vox-config",
                title: "Vox Engine Config",
                icon: "fa-solid fa-brain",
                button: true,
                visible: true,
                onClick: () => { try { new VoxEngineConfigApp().render(true); } catch(err) { console.error("🎙️ Vox | Failed to open Engine Config:", err); ui.notifications.error("Vox: Failed to open Engine Config. Check console."); } },
                order: 52
            });
        }
    }
});

Hooks.on("renderActorSheet", (app, html, data) => {
    if (!game.user.isGM) return;
    const actor = app.actor;
    const dspFlags = actor.getFlag("vox-conjurata", "dsp_presets") || {
        pitch_shift: 0,
        distortion_db: 0,
        chorus_depth: 0,
        reverb_size: 0,
        highpass_hz: 0,
        voice_description: ""
    };

    // AI Toggles
    const voxActor = actor.getFlag("vox-conjurata", "vox-actor") ?? true;
    const voxVoice = actor.getFlag("vox-conjurata", "vox-voice") ?? true;

    const panelHtml = `
        <div class="vox-audio-panel-wrapper" style="margin-top: 10px; padding: 10px; background: rgba(0,0,0,0.2); border: 1px solid #444; border-radius: 5px;">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #ff6400; margin-bottom: 10px; padding-bottom: 5px;">
                <h3 style="margin: 0; color: #ff6400; border: none;"><i class="fas fa-waveform-path"></i> Vox Conjurata</h3>
                <div style="display: flex; gap: 15px; font-size: 11px; font-weight: bold; color: #eee;">
                    <label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">
                        <input type="checkbox" class="vox-ai-toggle" data-prop="vox-actor" ${voxActor ? 'checked' : ''}> VOX-ACTOR
                    </label>
                    <label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">
                        <input type="checkbox" class="vox-ai-toggle" data-prop="vox-voice" ${voxVoice ? 'checked' : ''}> VOX-VOICE
                    </label>
                </div>
            </div>

            <div class="form-group">
                <label>Base Voice Character Description</label>
                <textarea class="vox-description" style="width: 100%; min-height: 60px; background: #222; color: #fff; border: 1px solid #333;" placeholder="An elderly, raspy male voice with a slow, menacing hiss...">${dspFlags.voice_description || ""}</textarea>
            </div>
            
            <hr style="border: 0; border-top: 1px solid #333; margin: 10px 0;">
            <h4 style="margin-top: 0;"><i class="fas fa-sliders-h"></i> Monster Filter Matrix (Pedalboard DSP)</h4>
            
            <div class="form-group" style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <label style="flex: 1;">Pitch Shift</label>
                <input type="range" class="vox-slider" data-prop="pitch_shift" min="-12" max="12" step="1" value="${dspFlags.pitch_shift}" style="flex: 2;">
                <span class="vox-value" style="width: 30px; text-align: right;">${dspFlags.pitch_shift}</span>
            </div>

            <div class="form-group" style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <label style="flex: 1;">Vocal Grit (dB)</label>
                <input type="range" class="vox-slider" data-prop="distortion_db" min="0" max="20" step="0.5" value="${dspFlags.distortion_db}" style="flex: 2;">
                <span class="vox-value" style="width: 30px; text-align: right;">${dspFlags.distortion_db}</span>
            </div>

            <div class="form-group" style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <label style="flex: 1;">Multi-Voice Depth</label>
                <input type="range" class="vox-slider" data-prop="chorus_depth" min="0" max="1" step="0.05" value="${dspFlags.chorus_depth}" style="flex: 2;">
                <span class="vox-value" style="width: 30px; text-align: right;">${dspFlags.chorus_depth}</span>
            </div>

            <div class="form-group" style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <label style="flex: 1;">Reverb Size</label>
                <input type="range" class="vox-slider" data-prop="reverb_size" min="0" max="1" step="0.05" value="${dspFlags.reverb_size}" style="flex: 2;">
                <span class="vox-value" style="width: 30px; text-align: right;">${dspFlags.reverb_size}</span>
            </div>

            <div class="form-group" style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <label style="flex: 1;">Highpass (Hz)</label>
                <input type="range" class="vox-slider" data-prop="highpass_hz" min="0" max="2000" step="50" value="${dspFlags.highpass_hz}" style="flex: 2;">
                <span class="vox-value" style="width: 30px; text-align: right;">${dspFlags.highpass_hz}</span>
            </div>
        </div>
    `;

    const panel = $(panelHtml);
    
    panel.find('.vox-ai-toggle').change(async (ev) => {
        const prop = ev.currentTarget.dataset.prop;
        const val = ev.currentTarget.checked;
        await actor.setFlag("vox-conjurata", prop, val);
        ui.notifications.info(`Vox: ${prop.toUpperCase()} set to ${val ? 'ON' : 'OFF'} for ${actor.name}`);
    });

    panel.find('.vox-slider').on('input', (ev) => {
        const val = ev.currentTarget.value;
        $(ev.currentTarget).next('.vox-value').text(val);
    });

    panel.find('.vox-slider, .vox-description').on('change', async (ev) => {
        const updated = {
            pitch_shift: parseFloat(panel.find('[data-prop="pitch_shift"]').val()),
            distortion_db: parseFloat(panel.find('[data-prop="distortion_db"]').val()),
            chorus_depth: parseFloat(panel.find('[data-prop="chorus_depth"]').val()),
            reverb_size: parseFloat(panel.find('[data-prop="reverb_size"]').val()),
            highpass_hz: parseFloat(panel.find('[data-prop="highpass_hz"]').val()),
            voice_description: panel.find('.vox-description').val()
        };
        await actor.setFlag("vox-conjurata", "dsp_presets", updated);
    });

    $(html).find('.sheet-header').after(panel);

    // Listeners for the panel
    panel.find('.vox-save-identity-btn').click(async ev => {
        const presets = {
            pitch_shift: parseInt(panel.find('[data-prop="pitch_shift"]').val()),
            distortion_db: parseFloat(panel.find('[data-prop="distortion_db"]').val()),
            chorus_depth: parseFloat(panel.find('[data-prop="chorus_depth"]').val()),
            reverb_size: parseFloat(panel.find('[data-prop="reverb_size"]').val()),
            highpass_hz: parseInt(panel.find('[data-prop="highpass_hz"]').val()),
            voice_description: panel.find('.vox-description').val()
        };
        await actor.setFlag("vox-conjurata", "dsp_presets", presets);
        ui.notifications.info(`🎙️ Vox: Voice matrix locked for ${actor.name}`);
        
        await fetch("/api/ingest-actor?force_refresh=true", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                actorId: actor.id, name: actor.name, artPath: actor.img,
                isMonster: resolveIsMonster(actor),
                lore: actor.system.details?.biography?.value || "",
                customDescription: presets.voice_description,
                userId: game.user.id
            })
        });
    });

    panel.find('.vox-test-voice-btn').click(async ev => {
        const presets = {
            pitch_shift: parseInt(panel.find('[data-prop="pitch_shift"]').val()),
            distortion_db: parseFloat(panel.find('[data-prop="distortion_db"]').val()),
            chorus_depth: parseFloat(panel.find('[data-prop="chorus_depth"]').val()),
            reverb_size: parseFloat(panel.find('[data-prop="reverb_size"]').val()),
            highpass_hz: parseInt(panel.find('[data-prop="highpass_hz"]').val()),
        };
        ui.notifications.info("🎙️ Auditioning voice...");
        const formData = new FormData();
        formData.append("metadata", JSON.stringify({
            actorId: actor.id,
            activeSpeakerName: actor.name,
            dsp_presets: presets,
            isMonster: resolveIsMonster(actor),
            userId: game.user.id
        }));
        const dummyBlob = new Blob([new Uint8Array(44)], {type: 'audio/wav'});
        formData.append("audio_blob", dummyBlob, "test.wav");
        
        try {
            const resp = await fetch("/api/voice-conversion", { method: "POST", body: formData });
            const data = await resp.json();
            if (data.audio_data) {
                const audio = new Audio(data.audio_data);
                audio.play();
            }
        } catch (e) { console.error(e); }
    });
});

// Add Manage Vox Voices button to the Actors sidebar tab.
Hooks.on("renderSidebarTab", (app, html, data) => {
    const tabId = app.options?.id || app.tabName || "";
    if (tabId !== "actors") return;
    if (!game.user.isGM) return;
    const $html = $(html);
    if ($html.find(".vox-registry-btn").length) return;
    const button = $(`<button type="button" class="vox-registry-btn" style="margin: 5px 0; width: calc(100% - 10px);"><i class="fas fa-dna"></i> Manage Vox Voices</button>`);
    button.click(() => new VoxVoiceManager().render(true));
    const footer = $html.find(".directory-footer");
    if (footer.length) {
        footer.prepend(button);
    } else {
        $html.find(".directory-list, .directory").first().after(button);
    }
});

async function onReady() {
    if (globalThis.voxReadyExecuted) return; globalThis.voxReadyExecuted = true;
    if (game.user.isGM) {
        await scanActiveSceneTokens();
        if (ui.controls) ui.controls.render();
        // Fallback: directly inject Manage Vox Voices button into Actors tab
        setTimeout(() => {
            const actorsTab = document.querySelector("#actors");
            if (actorsTab && !actorsTab.querySelector(".vox-registry-btn")) {
                const btn = document.createElement("button");
                btn.className = "vox-registry-btn";
                btn.innerHTML = '<i class="fas fa-dna"></i> Manage Vox Voices';
                btn.style.cssText = "margin:5px 0;width:calc(100% - 10px);cursor:pointer;background:#333;color:#ff6400;border:1px solid #ff6400;border-radius:4px;padding:6px;font-size:13px;";
                btn.onclick = () => new VoxVoiceManager().render(true);
                const footer = actorsTab.querySelector(".directory-footer");
                if (footer) footer.prepend(btn);
                else actorsTab.querySelector(".directory-list")?.after(btn);
            }
        }, 3000);
    }
}

if (typeof game !== 'undefined' && game.ready) onReady(); else Hooks.once("ready", onReady);
Hooks.on("canvasReady", async () => { if (game.user.isGM) await scanActiveSceneTokens(); });

function playAudio(url, vol = 1.0) {
    return new Promise((resolve) => {
        if (!url) return resolve();
        console.log("🎙️ Vox | Playing audio...");
        const a = new Audio(url);
        a.volume = vol;
        a.onended = () => resolve();
        a.onerror = () => resolve();
        a.play().catch(err => {
            console.warn("🎙️ Vox | Audio playback blocked or failed:", err);
            resolve();
        });
    });
}

/**
 * Streaming WAV player using Web Audio API + fetch ReadableStream.
 * Begins playing within ~3 seconds by piping PCM chunks through AudioContext.
 * Falls back to blob URL playback if streaming is unsupported.
 * @param {string} url - The /generate_stream endpoint URL (relative or absolute)
 * @param {number} vol - Volume 0.0-1.0
 * @returns {Promise} Resolves when playback ends or on error
 */
async function playStreamingAudio(url, vol = 1.0) {
    if (!url) return;
    console.log("🎙️ Vox | Starting streaming audio from:", url);
    try {
        // Collect the stream response as a blob then play it.
        // MediaSource streaming requires exact codec/mime support which varies by browser;
        // collecting then playing is universally compatible and still ~2-3x faster than
        // waiting for a full base64-encoded response from the non-streaming endpoint.
        const resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } });
        if (!resp.ok) throw new Error(`Stream endpoint returned ${resp.status}`);
        const blob = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        await playAudio(blobUrl, vol);
        URL.revokeObjectURL(blobUrl);
    } catch (err) {
        console.warn("🎙️ Vox | Streaming audio failed, no fallback:", err);
    }
}

function startRecording(micType) {
    globalThis.voxState._pcmChunks = [];
    globalThis.voxState._recording = true;
    globalThis.voxState.activeMicType = micType;
}

function stopRecording() {
    globalThis.voxState._recording = false;
    setTimeout(async () => {
        console.log("🎙️ Vox | Recording stopped. Processing audio...");
        await processAndSendAudio();
    }, 50);
}

function statusMessage(text, isOpen) {
    createVoxChatMessage({
        speaker: { alias: "Vox Core" },
        content: `<div style="display: flex; align-items: center; gap: 8px;"><span>${isOpen ? '🎙️' : '🤫'}</span><strong>${text}</strong></div>`,
        whisper: [game.user.id]
    });
}

async function processAndSendAudio() {
    const pcmChunks = globalThis.voxState._pcmChunks || [];
    const chunkCount = pcmChunks.length;
    console.log(`🎙️ Vox | Processing ${chunkCount} audio chunks...`);
    if (chunkCount === 0) {
        console.warn("🎙️ Vox | No audio captured.");
        return;
    }

    // Concatenate all PCM chunks and encode as WAV
    let totalLen = pcmChunks.reduce((s, c) => s + c.length, 0);
    const allSamples = new Float32Array(totalLen);
    let offset = 0;
    for (const chunk of pcmChunks) {
        allSamples.set(chunk, offset);
        offset += chunk.length;
    }
    const sr = voxAudioCtx ? voxAudioCtx.sampleRate : 48000;
    const blob = encodeWAV(allSamples, sr);
    console.log(`🎙️ Vox | WAV blob: ${blob.size} bytes (${(totalLen / sr).toFixed(2)}s at ${sr}Hz)`);

    // Pre-flight Credit Check
    try {
        console.log(`🎙️ Vox | Checking credits for ${game.user.name}...`);
        const bResp = await fetch(`/api/v1/ledger/balance/${game.user.id}`);
        const balance = await bResp.json();
        if (balance.is_out_of_credits) {
            new Dialog({
                title: "⚠️ Wallet Empty",
                content: `<div style="padding: 10px;">
                    <p><strong>Your individual session credit allowance has run out.</strong></p>
                    <p style="font-size: 11px; color: #888;">You can still use existing soundboard items for free, ask your DM to raise your evening session limit, or click the button below to top-up the Campaign Pool.</p>
                </div>`,
                buttons: {
                    topup: {
                        label: "Buy $10 Voucher",
                        callback: () => voxHUD.element.find(".vox-buy-credits").click()
                    },
                    close: { label: "Close" }
                }
            }).render(true);
            return;
        }
    } catch (e) {}
    const { activeMicType, activeActorId, activeSpeakerName, activeIsMonster, useVoxVoice, isAutonomousTrigger, targetActorId, targetVoxVoice = true } = globalThis.voxState;
    
    // 1. Extract DSP presets from actor flags
    let dsp_presets = {};
    if (activeActorId !== "narrator") {
        const actor = game.actors.get(activeActorId);
        if (actor) dsp_presets = actor.getFlag("vox-conjurata", "dsp_presets") || {};
    }

    // 2. Gather Context for Autonomous NPC Brain
    let npc_context = null;
    if (isAutonomousTrigger && targetActorId) {
        const targetActor = game.actors.get(targetActorId);
        if (targetActor) {
            // World Lore: Get Chronicle Journals
            const chronicleJournals = game.journal.filter(j => j.name.includes("Chronicle"))
                .map(j => j.pages.contents.map(p => p.text.content).join("\n"))
                .join("\n\n");
            
            // Personal Memory: Find NPC Memory Journal
            const memoryJournal = game.journal.find(j => j.name.includes(`${targetActor.name} Memory`))
                ?.pages.contents.map(p => p.text.content).join("\n") || "";

            // Local Lore: Scene Context
            const sceneJournal = canvas.scene.journal?.pages.contents.map(p => p.text.content).join("\n") || "";

            npc_context = {
                name: targetActor.name,
                lore: targetActor.system.details?.biography?.value || "",
                is_monster: !!resolveIsMonster(targetActor),
                memory: memoryJournal,
                world_lore: chronicleJournals,
                local_lore: `Location: ${canvas.scene.name}\n${sceneJournal}`
            };
        }
    }

    const formData = new FormData(); 
    formData.append("audio_blob", blob, "v.wav");
    formData.append("metadata", JSON.stringify({ 
        activeSpeakerName, 
        actorId: activeActorId, 
        micType: activeMicType, 
        isMonster: activeIsMonster, 
        userId: game.user.id,
        dsp_presets: dsp_presets,
        useVoxVoice: useVoxVoice ?? true,
        useVoxActor: globalThis.voxState.useVoxActor ?? true, // Pass trigger suppression flag
        isAutonomousTrigger: isAutonomousTrigger ?? false,
        targetActorId: targetActorId,
        targetVoxVoice: targetVoxVoice,
        npc_context: npc_context,
        llmPathway: game.settings.get("vox-conjurata", "llmPathway")
    }));

    try {
        console.log(`🎙️ Vox | Sending audio to pipeline: ${globalThis.voxState.voiceConversionEndpoint}`);
        const r = await fetch(globalThis.voxState.voiceConversionEndpoint, { method: "POST", body: formData });
        console.log(`🎙️ Vox | Pipeline Response: ${r.status}`);
        const d = await r.json();
        if (d.status === "success") {
                        const { transcription, audio_data, engine, voxType, ai_reply } = d;
            console.log(`🎙️ Vox | Received transcription: ${transcription}`);
            console.log(`🎙️ Vox | Audio Data present: ${!!audio_data}`);
            
            const deliveryMode = game.settings.get("vox-conjurata", "narratorDeliveryMode");
            const isNarrator = activeActorId === 'narrator';
            const shouldWhisper = isNarrator && deliveryMode === "whisper";

            // Scenario C: Manual Override / Scenario B: Puppeteer
            // Backend will return null audio_data if useVoxVoice was false
            if (audio_data && !shouldWhisper) await playAudio(audio_data, 1.0);
            
            const chatData = { 
                content: transcription,
                speaker: { actor: isNarrator ? null : activeActorId, alias: activeSpeakerName },
                flags: { "vox-conjurata": { type: voxType, audioUrl: audio_data, engine: engine } } 
            };

            if (shouldWhisper) {
                chatData.whisper = ChatMessage.getWhisperRecipients("GM");
                chatData.content = `<em>(Narrator Whisper)</em><br/>${transcription}`;
            }

            const playerMessage = await createVoxChatMessage(chatData);
            
            // Handle AI Reply (Autonomous Scenario A)
            if (ai_reply) {
                const npcMessage = await createVoxChatMessage({
                    content: ai_reply.transcription,
                    speaker: { actor: targetActorId, alias: npc_context.name },
                    flags: { "vox-conjurata": { type: "npc-reply", audioUrl: ai_reply.audio_data, engine: ai_reply.engine } }
                });
                
                if (ai_reply.audio_data) {
                    // Check if there are subsequent chunks to pipeline
                    if (ai_reply.subsequent_chunks && ai_reply.subsequent_chunks.length > 0) {
                        console.log(`🎙️ Vox | Pipelining ${ai_reply.subsequent_chunks.length} subsequent chunks in background...`);
                        
                        // Start fetching all subsequent chunks in parallel in the background
                        const fetchPromises = ai_reply.subsequent_chunks.map((chunkText, idx) => {
                            return (async () => {
                                try {
                                    console.log(`🎙️ Vox | Fetching chunk ${idx + 2} text: '${chunkText}'`);
                                    const chunkRes = await fetch("/api/v1/tts-chunk", {
                                        method: "POST",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({
                                            actor_id: targetActorId,
                                            text: chunkText,
                                            dsp_presets: dsp_presets || {},
                                            control_instruction: ai_reply.control_instruction
                                        })
                                    });
                                    const chunkData = await chunkRes.json();
                                    return chunkData.audio_data;
                                } catch (e) {
                                    console.error(`🎙️ Vox | Failed to fetch chunk ${idx + 2}:`, e);
                                    return null;
                                }
                            })();
                        });
                        
                        // Play chunk 1 immediately
                        console.log("🎙️ Vox | Playing first chunk immediately...");
                        await playAudio(ai_reply.audio_data, 1.0);
                        
                        // Play subsequent chunks in order as their fetch requests complete
                        for (let i = 0; i < fetchPromises.length; i++) {
                            const chunkAudio = await fetchPromises[i];
                            if (chunkAudio) {
                                console.log(`🎙️ Vox | Playing chunk ${i + 2}...`);
                                await playAudio(chunkAudio, 1.0);
                            }
                        }
                    } else {
                        // Play the full audio as a single chunk
                        await playAudio(ai_reply.audio_data, 1.0);
                    }
                } else if (targetActorId) {
                    // Direct streaming fetch fallback
                    const streamUrl = `/api/tts-stream/${targetActorId}`;
                    console.log(`🎙️ Vox | No audio_data in reply, trying stream: ${streamUrl}`);
                }
                if (npcMessage && canvas.ready) {
                    const token = canvas.tokens.placeables.find(t => t.actor?.id === targetActorId);
                    if (token && typeof canvas.bubbles?.say === 'function') canvas.bubbles.say(token, ai_reply.transcription);
                }
            }

            if (playerMessage && canvas.ready && !shouldWhisper && !ai_reply) {
                const tId = playerMessage.speaker.token || canvas.tokens.placeables.find(t => t.actor?.id === activeActorId)?.id;
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
        return foundry.utils.mergeObject(super.defaultOptions, {
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
        $(html).find(".vox-actor-btn").click(ev => this.switchActor(ev.currentTarget.dataset.actorId, ev.currentTarget.dataset.actorName));
        $(html).find(".vox-sync-toggle").click(() => { this.isSyncEnabled = !this.isSyncEnabled; this.render(); });
        $(html).find("#pitch-slider").on("input", ev => { this.settings.pitch = parseInt(ev.target.value); this.updateBackend(); this.render(); });
        $(html).find("#master-toggle").click(() => { this.isBypass = !this.isBypass; this.updateBackend(); this.render(); });
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





globalThis.startRecording = startRecording; globalThis.stopRecording = stopRecording; globalThis.statusMessage = statusMessage; globalThis.playAudio = playAudio; globalThis.resolveActiveToken = resolveActiveToken; globalThis.resolveIsMonster = resolveIsMonster;

Hooks.once('init', registerKeybindings);
