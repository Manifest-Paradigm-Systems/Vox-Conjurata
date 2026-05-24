/**
 * vox-conjurata: Main Foundry Module Entry Point
 * Handles programmatic, zero-click macro installation and framework initialization.
 */

const MODULE_ID = 'vox-conjurata';
const MACRO_NAME = 'LogNPCSession';

const MACRO_PAYLOAD = `/**
 * vox-conjurata: NPC Conversation Log Ingestion
 * Expected input format: args[0] = { npcName: "NPC Name", summary: "HTML/Text Summary" }
 * Auto-generated and managed by the vox-conjurata module.
 */

const { npcName, summary } = args[0] || { 
    npcName: "Default Test NPC", 
    summary: "<p>The orchestrator pipeline successfully handshook with the Foundry VTT API layer.</p>" 
};

// 1. Locate or create the target parent folder for clean organization
const folderName = "NPC Memories";
let folder = game.folders.find(f => f.name === folderName && f.type === "JournalEntry");
if (!folder) {
    folder = await Folder.create({ name: folderName, type: "JournalEntry" });
}

// 2. Find the NPC's specific logbook journal entry within that folder
let journal = game.journal.find(j => j.name === npcName && j.folder?.id === folder.id);
if (!journal) {
    journal = await JournalEntry.create({
        name: npcName,
        folder: folder.id
    });
}

// 3. Generate a clean, standardized timestamp for the session title
const timestamp = new Date().toLocaleString('en-US', { 
    year: 'numeric', month: 'short', day: 'numeric', 
    hour: '2-digit', minute: '2-digit', hour12: true 
});

// 4. Embed a brand new, isolated history page inside that NPC's book
await journal.createEmbeddedDocuments("JournalEntryPage", [{
    name: \`Session Log: \${timestamp}\`,
    type: "text",
    text: {
        content: \`
            <div class="vox-conjurata-log" style="border-left: 3px solid #7a52cc; padding-left: 12px; font-family: sans-serif;">
                <span style="color: #a380f5; font-size: 0.85em; font-weight: bold; letter-spacing: 1px;">VOX-CONJURATA SYSTEM RECORD</span>
                <p>\${summary}</p>
            </div>
\`,
        format: 1
    }
}]);

ui.notifications.info(\`vox-conjurata: Updated memory profile for \${npcName}.\`);
`;

Hooks.once("ready", async () => {
    if (!game.user.isGM) return;

    console.log("vox-conjurata | Initiating automated macro deployment check...");

    let existingMacro = game.macros.find(m => m.name === MACRO_NAME);

    if (!existingMacro) {
        await Macro.create({
            name: MACRO_NAME,
            type: "script",
            img: "icons/svg/book.svg",
            command: MACRO_PAYLOAD,
            ownership: {
                default: CONST.DOCUMENT_OWNERSHIP_LEVELS.NONE
            }
        });
        console.log(`vox-conjurata | Successfully executed zero-click installation for the ${MACRO_NAME} macro.`);
        ui.notifications.info("vox-conjurata: Automatically configured NPC memory pipelines.");
    } else {
        if (existingMacro.command !== MACRO_PAYLOAD) {
            await existingMacro.update({ command: MACRO_PAYLOAD });
            console.log(`vox-conjurata | Successfully synchronized and updated ${MACRO_NAME} to the latest runtime version.`);
        }
    }
});