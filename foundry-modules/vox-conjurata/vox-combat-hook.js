/**
 * Vox Conjurata Cinematic Combat Hook
 * System-aware hook for D&D 5e (Midi-QOL) and Pathfinder 2e.
 */

Hooks.once("ready", () => {
    console.log("Vox Conjurata | Cinematic Combat Hook Initialized");

    // --- D&D 5e: Midi-QOL Hook ---
    if (game.system.id === "dnd5e" && game.modules.get("midi-qol")?.active) {
        Hooks.on("midi-qol.RollComplete", async (workflow) => {
            if (!game.user.isGM && workflow.user.id !== game.user.id) return;
            
            const item = workflow.item;
            const result = (workflow.hitTargets.size > 0) ? "hit" : "miss";
            const target = workflow.targets.first();
            
            // Capture Automated Animations flags from the item
            const aaFlags = item.getFlag("autoanimations", "all-settings");
            const visualOverride = aaFlags?.primary?.video?.file || null;

            const payload = {
                userId: game.user.id,
                intentId: "auto_" + Date.now(), // Fallback if no STT intent found
                result: result,
                visualOverride: visualOverride,
                targetX: target?.center?.x,
                targetY: target?.center?.y
            };

            await resolveVoxCombat(payload);
        });
    }

    // --- Pathfinder 2e: Native Hook ---
    if (game.system.id === "pf2e") {
        Hooks.on("createChatMessage", async (message) => {
            if (!message.isRoll) return;
            const context = message.flags.pf2e?.context;
            if (context?.type === "attack-roll") {
                const result = (message.flags.pf2e?.appliedDamage || message.getFlag("pf2e", "context")?.outcome !== "failure") ? "hit" : "miss";
                const target = message.target?.token;
                
                // PF2e uses different flag structure for AA
                const aaFlags = message.item?.getFlag("autoanimations", "all-settings");
                const visualOverride = aaFlags?.primary?.video?.file || null;

                const payload = {
                    userId: game.user.id,
                    intentId: "auto_" + Date.now(),
                    result: result,
                    visualOverride: visualOverride,
                    targetX: target?.center?.x,
                    targetY: target?.center?.y
                };

                await resolveVoxCombat(payload);
            }
        });
    }
});

/**
 * Sends the mechanical result to Orchestrator and executes the cinematic sequence.
 */
async function resolveVoxCombat(payload) {
    try {
        const response = await fetch("http://localhost:8080/api/v1/combat/resolve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.status === "success" && data.sequencer_payload) {
            executeCinematicSequence(data.sequencer_payload);
        }
    } catch (err) {
        console.error("Vox Conjurata | Failed to resolve combat action:", err);
    }
}

/**
 * Executes the master Sequencer script returned by the Orchestrator.
 */
function executeCinematicSequence(p) {
    if (typeof Sequencer === "undefined") {
        console.error("Vox Conjurata | Sequencer module not found!");
        return;
    }

    new Sequence()
        // --- SHOT 1: THE CASTER ---
        .effect()
            .file(p.action_image)
            .screenSpace()
            .fadeIn(500)
            .duration(4000)
            .fadeOut(500)
        .sound()
            .file(p.action_sfx)
        .wait(4000)

        // --- SHOT 2: THE TARGET ---
        .effect()
            .file(p.reaction_image)
            .screenSpace()
            .fadeIn(500)
            .duration(4000)
            .fadeOut(500)
        .sound()
            .file(p.reaction_sfx)
        .wait(4000)

        // --- GRID EXECUTION ---
        .effect()
            .file(p.visual_override)
            .atLocation({ x: p.target_x, y: p.target_y })
        .sound()
            .file(p.narration_audio)
        
        .play();
}
