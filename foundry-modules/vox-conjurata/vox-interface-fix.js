/**
 * Vox-Conjurata: Pure Hardware Push-to-Talk Engine
 * Upgraded with state-memory caching to retain targeted NPC identity
 * context across both keydown and keyup execution frames.
 */

globalThis.voxState = globalThis.voxState || { 
    narratorActive: false, 
    puppetActive: false,
    playerActive: false,
    activePuppetName: "", // Short-term memory cache for the active speaker context
    activePlayerName: ""
};

Hooks.once("init", () => {
    
    // 1. Narrator Push-to-Talk (GM Only - Mapped to Y)
    game.keybindings.register("vox-conjurata", "narratorPTT", {
        name: "Vox: Narrator Push-to-Talk",
        hint: "Hold down 'Y' to capture ambient DM narration.",
        editable: [{ key: "KeyY" }],
        onDown: () => {
            if (!game.user.isGM || globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = true;
            
            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🎙️ <strong>Narrator Mic [Y]:</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span> (Recording ambient description...)`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (!game.user.isGM || !globalThis.voxState.narratorActive) return;
            globalThis.voxState.narratorActive = false;
            
            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🤫 <strong>Narrator Mic [Y]:</strong> <span style='color: #cc3333; font-weight: bold;'>CLOSED</span> (Processing...)`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        }
    });

    // 2. Puppeteer Push-to-Talk (GM Only - Mapped to H with Context Caching)
    game.keybindings.register("vox-conjurata", "puppeteerPTT", {
        name: "Vox: Puppeteer Push-to-Talk",
        hint: "Select an NPC token. Hold down 'H' to roleplay as them.",
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
            
            // Lock the targeted identity into memory cache
            globalThis.voxState.activePuppetName = actorName;
            
            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🎭 <strong>Puppeteer [H] (${actorName}):</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (!game.user.isGM || !globalThis.voxState.puppetActive) return;
            globalThis.voxState.puppetActive = false;
            
            // Retrieve the identity straight from memory cache
            const actorName = globalThis.voxState.activePuppetName || "Unknown Entity";
            
            ChatMessage.create({
                speaker: { alias: "Vox Core" },
                content: `🎭 <strong>Puppeteer Mic [H] (${actorName}):</strong> <span style='color: #cc3333; font-weight: bold;'>CLOSED</span>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
            
            // Clear the memory track slot until the next activation loop
            globalThis.voxState.activePuppetName = "";
        }
    });

    // 3. Player Character Push-to-Talk (Players Only - Mapped to I)
    game.keybindings.register("vox-conjurata", "playerPTT", {
        name: "Vox: Character Push-to-Talk",
        hint: "Hold down 'I' to speak as your assigned character.",
        editable: [{ key: "KeyI" }],
        onDown: () => {
            if (game.user.isGM || globalThis.voxState.playerActive) return;
            
            const speakerActor = canvas.tokens.controlled[0]?.actor || game.user.character;
            const speakerName = speakerActor?.name || game.user.name;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activePlayerName = speakerName; // Cache for symmetry

            ChatMessage.create({
                speaker: { alias: "Vox Proxy" },
                content: `👤 <strong>Character Mic [I]:</strong> <span style='color: #26b347; font-weight: bold;'>OPEN</span> (Speaking as: <strong>${speakerName}</strong>)`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
        },
        onUp: () => {
            if (game.user.isGM || !globalThis.voxState.playerActive) return;
            globalThis.voxState.playerActive = false;
            
            const speakerName = globalThis.voxState.activePlayerName || game.user.name;
            
            ChatMessage.create({
                speaker: { alias: "Vox Proxy" },
                content: `🤫 <strong>Character Mic [I] (${speakerName}):</strong> <span style='color: #cc3333; font-weight: bold;'>CLOSED</span>`,
                whisper: ChatMessage.getWhisperRecipients("GM")
            });
            
            globalThis.voxState.activePlayerName = "";
        }
    });
});

// 4. Clean Sweep Garbage Collection Layer
Hooks.once("ready", async () => {
    if (!game.user.isGM) return;
    const targetGarbageCollection = ["Vox: Toggle Narrator", "Vox: Toggle Puppeteer"];
    for (const name of targetGarbageCollection) {
        const existing = game.macros.filter(m => m.name === name);
        for (const oldMacro of existing) {
            console.log(`🧹 Clearing deprecated macro asset: ${oldMacro.name}`);
            await oldMacro.delete();
        }
    }
});
