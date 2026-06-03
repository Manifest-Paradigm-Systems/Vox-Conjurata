/**
 * Vox Conjurata Live Panel
 * Draggable control interface for real-time voice changing.
 */
console.log("🚀 Vox-Live-Panel: Script evaluation started.");

class VoxLivePanel extends Application {
    constructor(options = {}) {
        super(options);
        this.activeActorId = null;
        this.isBypass = true;
        this.isSyncEnabled = true;
        this.settings = {
            pitch: 0,
            formant: 0,
            mix: 1.0,
            f0Detector: "rmvpe_onnx",
            chunkSize: 112,
            extraFrame: 4096
        };
        
        // Actor Mapping from Specification
        this.profiles = {
            "elminster": { modelId: 1, tran: -3 },
            "goblin":    { modelId: 2, tran: 7 },
            "strahd":    { modelId: 3, tran: 0 }
        };
    }

    static get defaultOptions() {
        return mergeObject(super.defaultOptions, {
            id: "vox-live-panel",
            title: "🎙️ Vox Conjurata Live Panel",
            template: "modules/vox-conjurata/templates/live-panel.html", // We'll generate this string
            width: 320,
            height: "auto",
            resizable: false,
            dragDrop: [{ dragSelector: ".window-header" }]
        });
    }

    /**
     * Override render to use a string template if file doesn't exist
     */
    async _render(force = false, options = {}) {
        await super._render(force, options);
        this.element.find('.window-content').html(this._getHtml());
        this.activateListeners(this.element);
    }

    _getHtml() {
        const actors = this._getAvailableActors();
        let actorGrid = actors.map(a => `
            <div class="vox-actor-btn ${this.activeActorId === a.id ? 'active' : ''}" data-actor-id="${a.id}" data-actor-name="${a.name.toLowerCase()}">
                <img src="${a.img}" title="${a.name}"/>
                <div class="actor-name">${a.name}</div>
            </div>
        `).join("");

        return `
            <div class="vox-panel-section">
                <div class="vox-section-title">
                    <span>Quick-Swap Grid</span>
                    <div class="vox-sync-toggle ${this.isSyncEnabled ? 'active' : ''}" title="Toggle Token Sync">
                        <i class="fas fa-sync"></i> SYNC
                    </div>
                </div>
                <div class="vox-actor-grid">
                    ${actorGrid}
                </div>
            </div>

            <div class="vox-panel-section">
                <div class="vox-section-title">On-The-Fly Tweaks</div>
                
                <div class="vox-slider-group">
                    <div class="vox-slider-label">
                        <span>Pitch Shift</span>
                        <span class="vox-slider-value" id="pitch-val">${this.settings.pitch > 0 ? '+' : ''}${this.settings.pitch}</span>
                    </div>
                    <input type="range" class="vox-slider" id="pitch-slider" min="-12" max="12" step="1" value="${this.settings.pitch}">
                </div>

                <div class="vox-slider-group">
                    <div class="vox-slider-label">
                        <span>Formant / Size</span>
                        <span class="vox-slider-value" id="formant-val">${this.settings.formant}</span>
                    </div>
                    <input type="range" class="vox-slider" id="formant-slider" min="-100" max="100" step="5" value="${this.settings.formant}">
                </div>

                <div class="vox-slider-group">
                    <div class="vox-slider-label">
                        <span>AI Mix Blend</span>
                        <span class="vox-slider-value" id="mix-val">${Math.round(this.settings.mix * 100)}%</span>
                    </div>
                    <input type="range" class="vox-slider" id="mix-slider" min="0" max="1" step="0.05" value="${this.settings.mix}">
                </div>
            </div>

            <div class="vox-panel-section">
                <div class="vox-master-toggle ${this.isBypass ? '' : 'active'}" id="master-toggle">
                    <i class="fas ${this.isBypass ? 'fa-microphone-slash' : 'fa-microphone'}"></i>
                    <span>${this.isBypass ? 'BYPASS (OOC)' : 'VOX ACTIVE (NPC)'}</span>
                </div>
            </div>
        `;
    }

    _getAvailableActors() {
        // Fetch NPCs from the current scene or world
        return game.actors.filter(a => a.type === "npc" || a.hasPlayerOwner).map(a => ({
            id: a.id,
            name: a.name,
            img: a.img
        })).slice(0, 9); // Limit to 9 for the grid
    }

    activateListeners(html) {
        super.activateListeners(html);

        // Grid clicks
        html.find(".vox-actor-btn").click(ev => {
            const actorId = ev.currentTarget.dataset.actorId;
            const actorName = ev.currentTarget.dataset.actorName;
            this.switchActor(actorId, actorName);
        });

        // Sync toggle
        html.find(".vox-sync-toggle").click(() => {
            this.isSyncEnabled = !this.isSyncEnabled;
            this.render();
        });

        // Sliders
        html.find("#pitch-slider").on("input", ev => {
            this.settings.pitch = parseInt(ev.target.value);
            html.find("#pitch-val").text((this.settings.pitch > 0 ? '+' : '') + this.settings.pitch);
            this.updateBackend();
        });

        html.find("#formant-slider").on("input", ev => {
            this.settings.formant = parseInt(ev.target.value);
            html.find("#formant-val").text(this.settings.formant);
            this.updateBackend();
        });

        html.find("#mix-slider").on("input", ev => {
            this.settings.mix = parseFloat(ev.target.value);
            html.find("#mix-val").text(Math.round(this.settings.mix * 100) + "%");
            this.updateBackend();
        });

        // Master Toggle
        html.find("#master-toggle").click(() => {
            this.isBypass = !this.isBypass;
            this.updateBackend();
            this.render();
        });
    }

    async switchActor(actorId, actorName = "") {
        this.activeActorId = actorId;
        const profile = this.profiles[actorName.toLowerCase()] || { modelId: 0, tran: 0 };
        
        console.log(`🎙️ Vox: Switching to ${actorName || actorId} (Model ${profile.modelId})`);
        
        // Update local settings from profile
        this.settings.pitch = profile.tran;
        
        await this.updateBackend(profile.modelId);
        this.render();
    }

    async updateBackend(forceModelId = null) {
        if (this.isBypass) {
            console.log("🎙️ Vox: System in Bypass mode.");
            // Send bypass command if backend supports it, or just stop switching
            return;
        }

        const payload = {
            modelId: forceModelId !== null ? forceModelId : (this.profiles[this.activeActorId]?.modelId || 0),
            f0Detector: this.settings.f0Detector,
            tran: this.settings.pitch,
            chunkSize: this.settings.chunkSize,
            extraFrame: this.settings.extraFrame,
            // Additional custom params if W-Okada supports them via update_settings
            // formantShift: this.settings.formant,
            // mix: this.settings.mix
        };

        try {
            const response = await fetch("/api/voice-changer/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            if (response.ok) {
                console.log("🎙️ Vox: Backend settings updated.");
            }
        } catch (err) {
            console.error("🎙️ Vox: Failed to update backend:", err);
        }
    }
}

// Global instance
globalThis.voxLivePanel = new VoxLivePanel();

// --- Target Sync Implementation ---
Hooks.on("controlToken", (token, selected) => {
    if (selected && globalThis.voxLivePanel.isSyncEnabled) {
        const actor = token.actor;
        if (actor) {
            globalThis.voxLivePanel.switchActor(actor.id, actor.name);
        }
    }
});

// --- Hotkey Registration ---
Hooks.once("ready", () => {
    // Register Hotkeys in Foundry
    game.keybindings.register("vox-conjurata", "toggleVocalMask", {
        name: "Toggle Vocal Mask (Bypass/Active)",
        editable: [{ key: "KeyV", modifiers: [KeyboardManager.MODIFIER_KEYS.CONTROL, KeyboardManager.MODIFIER_KEYS.SHIFT] }],
        onDown: () => {
            globalThis.voxLivePanel.isBypass = !globalThis.voxLivePanel.isBypass;
            globalThis.voxLivePanel.updateBackend();
            globalThis.voxLivePanel.render();
            ui.notifications.info(`🎙️ Vox: ${globalThis.voxLivePanel.isBypass ? 'Bypass (OOC)' : 'Active (NPC)'}`);
        }
    });

    game.keybindings.register("vox-conjurata", "pitchUp", {
        name: "Quick Pitch Up",
        editable: [{ key: "ArrowUp", modifiers: [KeyboardManager.MODIFIER_KEYS.SHIFT] }],
        onDown: () => {
            globalThis.voxLivePanel.settings.pitch = Math.min(12, globalThis.voxLivePanel.settings.pitch + 1);
            globalThis.voxLivePanel.updateBackend();
            globalThis.voxLivePanel.render();
        }
    });

    game.keybindings.register("vox-conjurata", "pitchDown", {
        name: "Quick Pitch Down",
        editable: [{ key: "ArrowDown", modifiers: [KeyboardManager.MODIFIER_KEYS.SHIFT] }],
        onDown: () => {
            globalThis.voxLivePanel.settings.pitch = Math.max(-12, globalThis.voxLivePanel.settings.pitch - 1);
            globalThis.voxLivePanel.updateBackend();
            globalThis.voxLivePanel.render();
        }
    });

    game.keybindings.register("vox-conjurata", "cleanPTT", {
        name: "Pristine PTT (Whisper Intercept)",
        editable: [{ key: "KeyV" }],
        onDown: () => {
            if (globalThis.voxState.playerActive) return;
            console.log("🎙️ Vox: Pristine PTT [V] Down");
            const token = resolveActiveToken(false);
            const actor = token?.actor || game.user.character;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activeSpeakerName = actor?.name || game.user.name;
            globalThis.voxState.activeActorId = actor?.id || game.user.id;
            globalThis.voxState.activeIsMonster = !!resolveIsMonster(actor);
            
            // This PTT explicitly uses the character mic type
            if (typeof startRecording === 'function') startRecording("vox-conjurata-player-mic");
        },
        onUp: () => {
            if (!globalThis.voxState.playerActive) return;
            console.log("🎙️ Vox: Pristine PTT [V] Up");
            globalThis.voxState.playerActive = false;
            if (typeof stopRecording === 'function') stopRecording();
        }
    });

    // Create a button in the scene controls to open the panel
    const control = ui.controls.controls.find(c => c.name === "token");
    if (control) {
        control.tools.push({
            name: "vox-panel",
            title: "Vox Live Panel",
            icon: "fas fa-microphone-lines",
            button: true,
            onClick: () => globalThis.voxLivePanel.render(true)
        });
    }
});
