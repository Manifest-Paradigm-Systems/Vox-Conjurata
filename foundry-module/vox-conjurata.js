/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Intercepts dialogue completion and offloads processing to the local orchestrator.
 */

Hooks.once("ready", () => {
    console.log("vox-conjurata | System initialized and listening for session events.");
});

/**
 * Global function called by your game world when a scene or conversation ends.
 * Other DMs can trigger this via standard gameplay triggers or chat hooks.
 */
async function processNPCDialogueLog(npcName, rawTranscript) {
    if (!game.user.isGM) return;

    ui.notifications.info(`vox-conjurata: Summarizing conversation with ${npcName}...`);

    try {
        // Send the dialogue out to our local Python orchestrator container
        const response = await fetch("http://localhost:8080/api/v1/dialogue/summary", {
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