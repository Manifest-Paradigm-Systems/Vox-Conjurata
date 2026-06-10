/**
 * Permanent Lifecycle Hook for Vox-Voice UI Injection
 * Listens to the core Foundry chat rendering engine to guarantee buttons
 * are consistently painted and anchored inside the ProseMirror editor layout.
 */
Hooks.on("renderChatLog", (app, html, data) => {
    // Navigate the jQuery wrapper element provided by the Foundry hook context
    const chatSidebar = html.jquery ? html[0] : (html[0] || html);
    if (!chatSidebar || typeof chatSidebar.querySelector !== 'function') return;
    const editorContainer = chatSidebar.querySelector('.editor-container');
    
    // Safety exit if the chat box isn't initialized or rendered yet
    if (!editorContainer) return;
    
    // Prevent duplicate injection loops on sub-renders
    if (chatSidebar.querySelector('#vox-grounded-container')) return;

    // Build an unbreakable flex row container layout
    const voxContainer = document.createElement('div');
    voxContainer.id = 'vox-grounded-container';
    voxContainer.style.display = 'flex';
    voxContainer.style.gap = '6px';
    voxContainer.style.justifyContent = 'center';
    voxContainer.style.padding = '6px';
    voxContainer.style.background = 'rgba(20, 20, 20, 0.75)';
    voxContainer.style.borderTop = '1px solid #444';
    voxContainer.style.width = '100%';
    voxContainer.style.boxSizing = 'border-box';

    // Craft the Microphone Engine Input Toggle
    const micBtn = document.createElement('button');
    micBtn.type = 'button'; // Explicitly blocks form submission reflow breaks
    micBtn.innerHTML = '🎙️ Mic Mode';
    micBtn.style.flex = '1';
    micBtn.style.padding = '4px 0';
    micBtn.style.cursor = 'pointer';
    micBtn.style.fontSize = '12px';
    micBtn.style.lineHeight = '18px';

    const startVoxMic = (e) => {
        if (e) e.preventDefault();
        if (micBtn.classList.contains('active')) return;
        micBtn.classList.add('active');
        micBtn.style.background = '#8b0000';
        micBtn.style.color = '#fff';
        console.log("🎙️ Vox-Voice: Transcribe stream OPENED (PTT)");

        let micType = "vox-conjurata-player-mic";
        const resolveToken = globalThis.resolveActiveToken || window.resolveActiveToken;
        
        if (game.user.isGM) {
            const isPuppeteer = pupBtn.classList.contains('active');
            if (isPuppeteer) {
                const selectedToken = typeof resolveToken === 'function' ? resolveToken(true) : (canvas.tokens.controlled[0] || null);
                if (!selectedToken) {
                    ui.notifications.warn("❌ Puppeteer: Hover over or select an NPC token first!");
                    micBtn.classList.remove('active');
                    micBtn.style.background = '';
                    micBtn.style.color = '';
                    return;
                }
                globalThis.voxState.puppetActive = true;
                globalThis.voxState.activeSpeakerName = selectedToken.actor?.name || "Unknown NPC";
                globalThis.voxState.activeActorId = selectedToken.actor?.id || "unknown";
                micType = "vox-conjurata-gm-puppet-mic";
                const statMsg = globalThis.statusMessage || window.statusMessage;
                if (typeof statMsg === 'function') {
                    statMsg(`Puppeteer Mic (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
                }
            } else {
                globalThis.voxState.narratorActive = true;
                globalThis.voxState.activeSpeakerName = "Narrator";
                globalThis.voxState.activeActorId = "narrator";
                micType = "vox-conjurata-gm-narrate-mic";
                const statMsg = globalThis.statusMessage || window.statusMessage;
                if (typeof statMsg === 'function') {
                    statMsg("Narrator Mic: OPEN", true);
                }
            }
        } else {
            const selectedToken = typeof resolveToken === 'function' ? resolveToken(false) : (canvas.tokens.controlled[0] || null);
            const speakerActor = selectedToken?.actor || game.user.character;
            globalThis.voxState.playerActive = true;
            globalThis.voxState.activeSpeakerName = speakerActor?.name || game.user.name;
            globalThis.voxState.activeActorId = speakerActor?.id || game.user.id;
            micType = "vox-conjurata-player-mic";
            const statMsg = globalThis.statusMessage || window.statusMessage;
            if (typeof statMsg === 'function') {
                statMsg(`Character Mic (${globalThis.voxState.activeSpeakerName}): OPEN`, true);
            }
        }

        const startRec = globalThis.startRecording || window.startRecording;
        if (typeof startRec === 'function') {
            startRec(micType);
        } else {
            console.error("❌ Vox-Voice: startRecording function not found in global scope.");
        }
    };

    const stopVoxMic = (e) => {
        if (e) e.preventDefault();
        if (!micBtn.classList.contains('active')) return;
        micBtn.classList.remove('active');
        micBtn.style.background = '';
        micBtn.style.color = '';
        console.log("🎙️ Vox-Voice: Transcribe stream CLOSED (PTT)");

        if (game.user.isGM) {
            globalThis.voxState.narratorActive = false;
            globalThis.voxState.puppetActive = false;
            const statMsg = globalThis.statusMessage || window.statusMessage;
            if (typeof statMsg === 'function') {
                statMsg("Narrator/Puppeteer Mic: CLOSED", false);
            }
        } else {
            globalThis.voxState.playerActive = false;
            const statMsg = globalThis.statusMessage || window.statusMessage;
            if (typeof statMsg === 'function') {
                statMsg(`Character Mic (${globalThis.voxState.activeSpeakerName}): CLOSED`, false);
            }
        }

        const stopRec = globalThis.stopRecording || window.stopRecording;
        if (typeof stopRec === 'function') {
            stopRec();
        } else {
            console.error("❌ Vox-Voice: stopRecording function not found in global scope.");
        }
    };

    // Event Listeners for PTT behavior
    micBtn.onmousedown = startVoxMic;
    micBtn.onmouseup = stopVoxMic;
    micBtn.onmouseleave = stopVoxMic; // Stops if mouse dragged out
    micBtn.ontouchstart = startVoxMic;
    micBtn.ontouchend = stopVoxMic;
    micBtn.ontouchcancel = stopVoxMic;

    // Craft the DM Puppeteer Target Override Hook
    const pupBtn = document.createElement('button');
    pupBtn.type = 'button'; // Explicitly blocks form submission reflow breaks
    pupBtn.innerHTML = '🎭 Puppeteer';
    pupBtn.style.flex = '1';
    pupBtn.style.padding = '4px 0';
    pupBtn.style.cursor = 'pointer';
    pupBtn.style.fontSize = '12px';
    pupBtn.style.lineHeight = '18px';
    pupBtn.onclick = (e) => {
        e.preventDefault();
        pupBtn.classList.toggle('active');
        pupBtn.style.background = pupBtn.classList.contains('active') ? '#004d40' : '';
        pupBtn.style.color = pupBtn.classList.contains('active') ? '#fff' : '';
        console.log("🎭 Vox-Voice: Actor context hook state changed.");
    };

    // Append items together and dock cleanly underneath the ProseMirror input box
    voxContainer.appendChild(micBtn);
    voxContainer.appendChild(pupBtn);
    editorContainer.appendChild(voxContainer);
    
    console.log("🎯 Vox-Voice: UI elements securely locked to chat frame via lifecycle hooks.");
});
