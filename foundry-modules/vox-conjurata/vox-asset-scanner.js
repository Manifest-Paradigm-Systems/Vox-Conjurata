/**
 * Vox Conjurata Library-Agnostic Asset Scanner
 * Scans installed modules for WebM/Opus animations and maps them to spell intents.
 */

Hooks.once("ready", async () => {
    // 1. Check for recommendation popup
    const recommended = ["jb2a_patreon", "jb2a_free", "blfx", "tokenmagic"];
    const missing = recommended.filter(id => !game.modules.get(id)?.active);

    if (missing.length > 0 && !game.user.getFlag("vox-conjurata", "hide-animation-warning")) {
        new Dialog({
            title: "Vox Conjurata | Animation Recommendations",
            content: `<p>To enable cinematic combat visuals, it is highly recommended to install one or more of the following animation packs:</p>
                      <ul>${missing.map(m => `<li><b>${m}</b></li>`).join("")}</ul>`,
            buttons: {
                ok: { label: "Got it" },
                never: { 
                    label: "Never show again", 
                    callback: () => game.user.setFlag("vox-conjurata", "hide-animation-warning", true) 
                }
            }
        }).render(true);
    }

    // 2. Initial Asset Scan
    console.log("Vox Conjurata | Scanning for compatible visual assets...");
    const library = await scanForAnimations();
    
    // Send the library to the Orchestrator to update its mapping dictionary
    fetch("http://localhost:8080/api/v1/library/sync-animations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library: library })
    }).catch(err => console.warn("Vox Conjurata | Could not sync animation library with backend."));
});

async function scanForAnimations() {
    const animationMap = {};
    const modules = game.modules.filter(m => m.active);
    
    for (let mod of modules) {
        // This is a conceptual placeholder for the recursive scanning logic
        // In a real implementation, we use Sequencer's database if available
        if (typeof Sequencer !== 'undefined' && Sequencer.Database) {
            const db = Sequencer.Database.entries;
            for (let entry of db) {
                // Map database entries back to common spell names
                // e.g., jb2a.fireball -> "Fireball"
            }
        }
    }
    return animationMap;
}
