// Initialize a global, volatile transaction memory buffer for in-progress dialogue streams
globalThis.voxDialogueCache = globalThis.voxDialogueCache || {};
const voxHost = window.location.hostname || "127.0.0.1";

console.log("🌲 Vox Chronicle | Initializing Ambient Campaign Tracker Pipeline...");

/**
 * HOOK 1: System-Agnostic Chat Capture Interceptor
 * Listens to active dialogue text whenever a player target locks an entity token.
 */
Hooks.on("createChatMessage", (message, options, userId) => {
    if (userId !== game.user.id) return;

    const targets = game.user.targets;
    if (targets.size === 0) return; 

    targets.forEach(target => {
        const npcName = target.actor?.name || target.name;
        if (target.actor?.type === "character") return; // Bypass PC cross-chatter

        if (!globalThis.voxDialogueCache[npcName]) {
            globalThis.voxDialogueCache[npcName] = [];
        }

        globalThis.voxDialogueCache[npcName].push(`Player: ${message.content}`);
    });
});

/**
 * HOOK 2: Scene/Map Shift Safety Net
 * Catches region navigation transitions and prompts the DM to commit remaining volatile logs.
 */
Hooks.on("canvasReady", () => {
    if (!game.user.isGM) return;

    const pendingNPCs = Object.keys(globalThis.voxDialogueCache || {}).filter(
        npc => globalThis.voxDialogueCache[npc].length > 0
    );

    if (pendingNPCs.length === 0) return;

    new Dialog({
        title: "🗺️ Region Transition: Pending Logs Found",
        content: `
            <p><strong>Warning:</strong> You have uncommitted scene logs in memory for: 
            <code>${pendingNPCs.join(", ")}</code>.</p>
            <p>Would you like to process these travel chronicles before running encounters on the new map?</p>
        `,
        buttons: {
            commit: {
                icon: '<i class="fas fa-book-open"></i>',
                label: "Commit All to Journals",
                callback: async () => {
                    for (const npc of pendingNPCs) {
                        const transcript = globalThis.voxDialogueCache[npc].join("\n");
                        await fetch(`http://${voxHost}:8080/api/v1/dialogue/end`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ npcName: npc, transcript: transcript })
                        });
                        delete globalThis.voxDialogueCache[npc];
                    }
                    ui.notifications.info("All pending regional travel logs updated!");
                }
            },
            hold: {
                icon: '<i class="fas fa-hourglass-half"></i>',
                label: "Keep Active in Memory",
                callback: () => ui.notifications.info("Buffers retained for current scene.")
            }
        },
        default: "commit"
    }).render(true);
});

/**
 * HOOK 3: System-Agnostic Sheet Value Scanner
 * Flattens update records to dynamically look for value increases matching 'xp' or 'experience'.
 */
Hooks.on("updateActor", (actor, updateData, options, userId) => {
    if (!game.user.isGM || actor.type !== "character") return;

    const flatUpdate = foundry.utils.flattenObject(updateData);
    const isXpUpdated = Object.keys(flatUpdate).some(key => 
        key.toLowerCase().includes("xp") || key.toLowerCase().includes("experience")
    );

    if (!isXpUpdated) return;

    new Dialog({
        title: "⚔️ Milestone Cleared: Log Travel Journal?",
        content: `
            <p>Experience points or character progress metrics modified for <strong>${actor.name}</strong>.</p>
            <p>Would you like to document this achievement within the campaign chronicle?</p>
        `,
        buttons: {
            log: {
                icon: '<i class="fas fa-feather-alt"></i>',
                label: "Compile Summary",
                callback: () => {
                    fetch(`http://${voxHost}:8080/api/v1/dialogue/end`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            npcName: "Travel Milestone",
                            transcript: `System Event: Progress milestone achieved or experience modified for ${actor.name}.`
                        })
                    });
                    ui.notifications.info("Milestone summary dispatched to local engine.");
                }
            },
            skip: {
                icon: '<i class="fas fa-times"></i>',
                label: "Skip Logging"
            }
        },
        default: "log"
    }).render(true);
});

/**
 * HOOK 4: Core Tracker Lifecycle Closer
 * Captures combat breakdown vectors right as the tracker context gets flushed.
 */
Hooks.on("deleteCombat", (combat, options, userId) => {
    if (!game.user.isGM) return;

    const participants = combat.combatants
        .map(c => c.name)
        .join(", ");

    new Dialog({
        title: "🛡️ Combat Concluded",
        content: `
            <p>The active encounter tracker has been closed.</p>
            <p>Involved: <code>${participants || "Unknown Entities"}</code></p>
            <p>Would you like your local engine to automatically draft a journal log for this battle?</p>
        `,
        buttons: {
            log: {
                icon: '<i class="fas fa-swords"></i>',
                label: "Log Battle Chronicle",
                callback: () => {
                    fetch(`http://${voxHost}:8080/api/v1/dialogue/end`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            npcName: "Combat Encounter",
                            transcript: `System Event: Combat ended. Participants involved in conflict: ${participants}.`
                        })
                    });
                    ui.notifications.info("Combat summary data sent to local engine queue.");
                }
            },
            skip: {
                icon: '<i class="fas fa-times"></i>',
                label: "Skip"
            }
        },
        default: "log"
    }).render(true);
});

/**
 * HOOK 5: Zero-Click Client Installation Engine
 * Programmatically injects the mandatory 'LogNPCSession' backend pipeline landing pad
 * into the client's world database immediately upon module activation.
 */
Hooks.once("ready", async () => {
    if (!game.user.isGM) return;

    const targetMacroName = "LogNPCSession";
    const macroExists = game.macros.contents.find(m => m.name === targetMacroName);
    if (macroExists) return;

    console.log(`🌲 Vox Chronicle | Core macro missing. Initiating automatic deployment sequence...`);

    const macroCommandScript = [
        "// This script runs automatically when triggered by your local Python orchestrator API.",
        "const { npcName, summary } = args[0] || {};",
        "",
        "if (!npcName || !summary) {",
        "    return console.error('Vox Chronicle | Missing payload parameters in execution block.');",
        "}",
        "",
        "// 1. Locate or create the centralized chronicle journal directory",
        "let folder = game.folders.find(f => f.name === 'Campaign Chronicles' && f.type === 'JournalEntry');",
        "if (!folder) {",
        "    const createdFolders = await Folder.createDocuments([{ name: 'Campaign Chronicles', type: 'JournalEntry' }]);",
        "    folder = createdFolders[0];",
        "}",
        "",
        "// 2. Resolve target journal entry wrapper",
        "let entry = game.journal.find(j => j.name === npcName + ' Chronicle' && j.folder?.id === folder.id);",
        "",
        "if (entry) {",
        "    // Append new content block to existing chronicle timeline",
        "    const page = entry.pages.first();",
        "    const updatedContent = page.text.content + '<br><hr><br>' + summary;",
        "    await page.update({ text: { content: updatedContent } });",
        "    ui.notifications.info('Updated chronicle timeline for ' + npcName + '!');",
        "} else {",
        "    // Build a brand new chronicle record from scratch",
        "    await JournalEntry.create({",
        "        name: npcName + ' Chronicle',",
        "        folder: folder.id,",
        "        pages: [{",
        "            name: 'Session Notes',",
        "            type: 'text',",
        "            text: { content: summary, format: 1 }",
        "        }]",
        "    });",
        "    ui.notifications.info('Created a new chronicle ledger for ' + npcName + '!');",
        "}"
    ].join("\n");

    try {
        await Macro.create({
            name: targetMacroName,
            type: "script",
            scope: "global",
            command: macroCommandScript,
            img: "icons/sundries/books/book-red-leather.webp"
        });
        ui.notifications.info(`🌲 Vox Chronicle | Automated installation successful! '${targetMacroName}' macro added.`);
    } catch (err) {
        console.error(`🌲 Vox Chronicle | Critical failure during macro deployment:`, err);
    }
});
