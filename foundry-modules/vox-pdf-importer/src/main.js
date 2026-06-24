/**
 * vox-pdf-importer — Main entry point.
 * System-agnostic AI PDF import with queue support, WebP-native, and mini-terminal display.
 */
import { PdfPageRenderer } from "./pdf/page-renderer.js";
import { PageAnalyzer } from "./pdf/page-analyzer.js";
import { VisionClient } from "./api/vision-client.js";
import { ActorCreator } from "./documents/actor-creator.js";
import { JournalCreator } from "./documents/journal-creator.js";
import { SceneCreator } from "./documents/scene-creator.js";
import { FolderCreator } from "./documents/folder-creator.js";
import { VoxPdfProgressDialog } from "./ui/progress-dialog.js";
import { ParserRegistry } from "./parser/registry.js";

const MODULE_ID = "vox-pdf-importer";

// ─── Hooks ──────────────────────────────────────────────────────────

Hooks.once("init", () => {
  // Handlebars helper for step status comparison
  Handlebars.registerHelper("eq", (a, b) => a === b);

  game.settings.register(MODULE_ID, "visionApiEndpoint", {
    name: "VOXPDF.Settings.VisionEndpoint",
    hint: "VOXPDF.Settings.VisionEndpointHint",
    scope: "world", config: true, type: String,
    default: "/api/v1/pdf-import-vision",
  });
  game.settings.register(MODULE_ID, "useLlmRefinement", {
    name: "VOXPDF.Settings.UseLLMRefinement",
    hint: "VOXPDF.Settings.UseLLMRefinementHint",
    scope: "world", config: true, type: Boolean, default: true,
  });
  game.settings.register(MODULE_ID, "maxRetries", {
    name: "VOXPDF.Settings.MaxRetries",
    hint: "VOXPDF.Settings.MaxRetriesHint",
    scope: "user", config: true, type: Number, default: 3,
    choices: { 1: "1", 2: "2", 3: "3", 5: "5" },
  });
  game.settings.register(MODULE_ID, "createJournals", {
    name: "VOXPDF.Settings.CreateJournals",
    hint: "VOXPDF.Settings.CreateJournalsHint",
    scope: "world", config: true, type: Boolean, default: true,
  });
  game.settings.register(MODULE_ID, "createScenes", {
    name: "VOXPDF.Settings.CreateScenes",
    hint: "VOXPDF.Settings.CreateScenesHint",
    scope: "world", config: true, type: Boolean, default: true,
  });
  // Game system: auto-detected but user-overridable
  const detected = ParserRegistry.detect();
  game.settings.register(MODULE_ID, "gameSystem", {
    name: "VOXPDF.Settings.GameSystem",
    hint: "VOXPDF.Settings.GameSystemHint",
    scope: "world", config: true, type: String,
    default: detected,
    choices: (() => {
      const c = { unknown: "Auto-detect" };
      for (const [k, v] of Object.entries(ParserRegistry.labels)) c[k] = v;
      return c;
    })(),
  });
});

Hooks.once("ready", () => {
  // When world loads, re-set the default to auto-detect
  const detected = ParserRegistry.detect();
  const current = game.settings.get(MODULE_ID, "gameSystem");
  if (current === "unknown" && detected !== "unknown") {
    game.settings.set(MODULE_ID, "gameSystem", detected);
  }

  // Expose entry points
  game.voxPdfImporter = {
    start: () => new VoxPdfImport().start(),
    startQueue: () => new VoxPdfImport().startQueue(),
  };

  // Inject into Jack's unknown PDF dialog
  Hooks.on("renderDialog", (dialog, html) => {
    const title = dialog?.title;
    if (!title || !title.includes("Helyx")) return;
    if (!game.modules.get("jacks-pf2e-pdf-import")?.active) return;
    const buttonsDiv = html.find(".dialog-buttons");
    if (!buttonsDiv.length || buttonsDiv.find(".vox-pdf-import-btn").length) return;
    buttonsDiv.prepend(`
      <button class="vox-pdf-import-btn" style="background:#6a1b9a;color:white;border:none;margin-bottom:4px;">
        <i class="fas fa-brain"></i> Import with Vox AI
      </button>
    `);
    html.find(".vox-pdf-import-btn").click(async () => {
      dialog.close();
      const pdfFileName = html.find("b").first().text().trim() || "unknown.pdf";
      const importer = new VoxPdfImport();
      await importer.startFromJack(pdfFileName);
    });
  });
});

// ─── VoxPdfImport ─────────────────────────────────────────────────────

class VoxPdfImport {
  constructor() {
    this.visionClient = new VisionClient(game.settings.get(MODULE_ID, "visionApiEndpoint"));
    this.renderer = null;
    this.progressDialog = null;
    this.results = { actors: 0, journals: 0, scenes: 0 };
    this.pdfData = null;
    this.pdfFilename = "";
    this.gameSystem = game.settings.get(MODULE_ID, "gameSystem");
    if (this.gameSystem === "unknown" || !ParserRegistry.supported.includes(this.gameSystem)) {
      this.gameSystem = ParserRegistry.detect();
    }
    // Mini-terminal: array of step objects
    this.steps = [];
    this.logs = [];
    this._stepId = 0;
  }

  _activeSystem() { return this.gameSystem; }
  _parser() { return ParserRegistry.get(this._activeSystem()); }

  _addStep(id, label) {
    const step = { id, label, status: "pending", duration: null, started: null };
    this.steps.push(step);
    return step;
  }
  _setStepStatus(id, status) {
    const s = this.steps.find(x => x.id === id);
    if (s) { s.status = status; if (status === "running") s.started = Date.now(); }
  }
  _log(level, message) {
    this.logs.push({ time: Date.now(), level, message });
    if (this.logs.length > 200) this.logs.shift();
  }

  // ─── Queue Entry Point ─────────────────────────────────────────────

  async start() {
    const file = await this._promptForPdf();
    if (!file) return;
    this.pdfData = file.buffer;
    this.pdfFilename = file.name;
    await this._run();
  }

  async startFromJack(pdfFilename) {
    this.pdfFilename = pdfFilename;
    this.pdfData = await this._resolvePdfFromJack(pdfFilename);
    if (!this.pdfData) {
      const file = await this._promptForPdf();
      if (!file) return;
      this.pdfData = file.buffer;
      this.pdfFilename = file.name;
    }
    await this._run();
  }

  async startQueue() {
    await this._openQueueDialog();
  }

  // ─── Queue Dialog ──────────────────────────────────────────────────

  async _openQueueDialog() {
    const content = await renderTemplate("modules/vox-pdf-importer/templates/vox-pdf-import-dialog.hbs", {
      systemDetected: ParserRegistry.labels[this._activeSystem()] || "Unknown",
      systems: Object.entries(ParserRegistry.labels).map(([k, v]) => ({ id: k, label: v, selected: k === this._activeSystem() })),
    });
    new Dialog({
      title: "Vox AI PDF Import",
      content,
      buttons: {},
      render: html => {
        html.find(".vox-pdf-add-btn").click(async () => {
          const files = await this._promptForMultiplePdfs();
          if (files.length) {
            const queue = this._loadQueue();
            for (const f of files) queue.push({ id: foundry.utils.randomID(), filename: f.name, status: "pending" });
            this._saveQueue(queue);
            this._renderQueueList(html);
          }
        });
        html.find(".vox-pdf-start-queue").click(async () => {
          html.find(".vox-pdf-start-queue").prop("disabled", true);
          await this._processQueue();
          html.find(".vox-pdf-start-queue").prop("disabled", false);
        });
        html.find(".vox-pdf-system-select").change(e => {
          this.gameSystem = e.target.value;
        });
        this._renderQueueList(html);
      },
    }, { width: 600 }).render(true);
  }

  _loadQueue() {
    return JSON.parse(game.user.getFlag(MODULE_ID, "queue") || "[]");
  }
  _saveQueue(queue) {
    game.user.setFlag(MODULE_ID, "queue", queue);
  }

  _renderQueueList(html) {
    const queue = this._loadQueue();
    const container = html.find(".vox-pdf-queue-list");
    container.empty();
    if (!queue.length) { container.append('<p class="notes">No PDFs in queue. Click "Add PDFs" to begin.</p>'); return; }
    queue.forEach((item, i) => {
      container.append(`
        <div class="vox-pdf-queue-item" data-idx="${i}">
          <span class="vox-pdf-queue-name">${item.filename}</span>
          <span class="vox-pdf-queue-status">${item.status}</span>
          <button type="button" class="vox-pdf-queue-remove" data-idx="${i}"><i class="fas fa-times"></i></button>
        </div>
      `);
    });
    html.find(".vox-pdf-queue-remove").click(e => {
      const idx = parseInt(e.currentTarget.dataset.idx);
      const queue = this._loadQueue();
      queue.splice(idx, 1);
      this._saveQueue(queue);
      this._renderQueueList(html);
    });
  }

  async _processQueue() {
    let queue = this._loadQueue();
    if (!queue.length) return;
    let totalItems = queue.length;
    for (let i = 0; i < queue.length; i++) {
      const item = queue[i];
      item.status = "processing";
      this._saveQueue(queue);
      try {
        const resp = await fetch(item.filename || item.path);
        if (!resp.ok) { item.status = "failed"; continue; }
        this.pdfData = await resp.arrayBuffer();
        this.pdfFilename = item.filename;
        this.steps = [];
        this.logs = [];
        await this._run(item, i + 1, totalItems);
        item.status = "done";
        item.result = { ...this.results };
      } catch (e) {
        item.status = "failed";
        this._log("error", `Queue item failed: ${e.message}`);
      }
      this._saveQueue(queue);
    }
    ui.notifications.info(`Vox PDF | Queue complete: ${queue.filter(i => i.status === "done").length}/${queue.length} succeeded.`);
    this._saveQueue([]);
  }

  async _promptForMultiplePdfs() {
    return new Promise(resolve => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".pdf";
      input.multiple = true;
      input.onchange = async () => {
        const files = [];
        for (const f of input.files) {
          const buffer = await f.arrayBuffer();
          files.push({ buffer, name: f.name });
        }
        resolve(files);
      };
      input.click();
    });
  }

  _promptForPdf() {
    return new Promise(resolve => {
      const input = document.createElement("input");
      input.type = "file"; input.accept = ".pdf";
      input.onchange = async () => {
        const f = input.files[0];
        if (!f) { resolve(null); return; }
        resolve({ buffer: await f.arrayBuffer(), name: f.name });
      };
      input.click();
    });
  }

  // ─── Core Pipeline ─────────────────────────────────────────────────

  async _run(queueItem, queuePos, queueTotal) {
    const parser = this._parser();
    const systemLabel = ParserRegistry.labels[this._activeSystem()] || this._activeSystem();
    this._log("info", `Starting import: ${this.pdfFilename} [${systemLabel}]`);
    const stepLoad = this._addStep("load", "Load PDF");
    const stepAnalyze = this._addStep("analyze", "Analyze page structure");
    const stepVision = this._addStep("vision", "AI vision analysis");
    const stepDocuments = this._addStep("documents", "Create documents");
    const stepArt = this._addStep("art", "Upload art images");

    try {
      this._setStepStatus("load", "running");
      this.renderer = await PdfPageRenderer.create();
      const numPages = await this.renderer.loadPdf(this.pdfData);
      this._setStepStatus("load", "done");

      this.progressDialog = new VoxPdfProgressDialog(numPages, {
        queuePosition: queuePos,
        queueTotal,
        filename: this.pdfFilename,
      });
      this.progressDialog.render(true);

      const folders = await FolderCreator.ensureFolders(this.pdfFilename);

      let previousContext = "";
      const maxRetries = game.settings.get(MODULE_ID, "maxRetries");
      const createJournals = game.settings.get(MODULE_ID, "createJournals");
      const createScenes = game.settings.get(MODULE_ID, "createScenes");

      this._setStepStatus("analyze", "running");

      for (let pageNum = 1; pageNum <= numPages; pageNum++) {
        if (this.progressDialog.isCancelled()) break;

        this.progressDialog.updateProgress(pageNum, `Analyzing page ${pageNum}...`);
        this._log("info", `Page ${pageNum}/${numPages}: analyzing`);

        try {
          const textContent = await this.renderer.getTextContent(pageNum);
          const category = PageAnalyzer.categorize(textContent);

          const canvas = await this.renderer.renderPageToCanvas(pageNum);

          if (category === "art") {
            this._setStepStatus("art", "running");
            this._log("info", `Page ${pageNum}: art detected, uploading as WebP`);
            const blob = await new Promise(r => canvas.toBlob(r, "image/webp", 0.85));
            const artPath = `vox-pdf-imports/${this._safeName()}/art`;
            const result = await FilePicker.upload("data", artPath, new File([blob], `page_${pageNum}.webp`), {}, { notify: false });
            const journal = await JournalEntry.createDocuments([{
              name: `Art — Page ${pageNum}`,
              folder: folders.JournalEntry,
              pages: [{ name: `Page ${pageNum}`, type: "text", text: { content: `<p><img src="${result.path}" style="width:100%;max-width:100%;"></p>`, format: 1 } }],
            }]);
            this.results.journals++;
            this.progressDialog.recordResult("success");
          } else {
            this._setStepStatus("vision", "running");
            this._log("info", `Page ${pageNum}: sending to vision API (${systemLabel})`);
            // WebP for vision API
            const webpBlob = await new Promise(r => canvas.toBlob(r, "image/webp", 0.9));
            const reader = new FileReader();
            const base64 = await new Promise(r => { reader.onload = () => r(reader.result); reader.readAsDataURL(webpBlob); });

            const result = await this._callVisionWithRetry(base64, pageNum, previousContext, maxRetries);
            this._setStepStatus("vision", "done");

            if (result.has_content) {
              const sd = result.structured_data;
              const type = sd?.type;

              if (type === "npc") {
                this._setStepStatus("documents", "running");
                this._log("info", `Page ${pageNum}: creating NPC "${sd.name || 'unnamed'}"`);
                const actors = await ActorCreator.createFromParsedData([sd], folders.Actor, this._activeSystem());
                this.results.actors += actors.length;
                this.progressDialog.recordResult("success");
              } else if (type === "narrative" && createJournals) {
                this._setStepStatus("documents", "running");
                const journal = await JournalCreator.createFromParsedData(sd, folders.JournalEntry);
                if (journal) this.results.journals++;
                this.progressDialog.recordResult("success");
              } else {
                this.progressDialog.recordResult("skipped");
              }
              previousContext = this._summarizeContext(sd);
            } else {
              this.progressDialog.recordResult("skipped");
            }
          }
        } catch (err) {
          this._log("error", `Page ${pageNum}: ${err.message}`);
          this.progressDialog.recordResult("error");
        }

        // Push state to progress dialog
        this._pushState();
      }

      this._setStepStatus("analyze", "done");
      this._setStepStatus("documents", "done");
      this._setStepStatus("art", "done");

      this.progressDialog?.close();
      await this._showSummary();
    } catch (err) {
      this._log("error", `Fatal: ${err.message}`);
      ui.notifications.error(`Vox PDF Import failed: ${err.message}`);
      this.progressDialog?.close();
    } finally {
      this.renderer?.destroy();
    }
  }

  _pushState() {
    if (this.progressDialog && !this.progressDialog._closed) {
      this.progressDialog.updateSteps([...this.steps], [...this.logs]);
    }
  }

  // ─── Vision API ────────────────────────────────────────────────────

  async _callVisionWithRetry(base64, pageNum, previousContext, maxRetries) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        return await this.visionClient.analyzePage(base64, pageNum, this._activeSystem(), previousContext);
      } catch (err) {
        if (attempt === maxRetries) throw err;
        const delay = Math.pow(2, attempt) * 1000;
        this._log("warning", `Retry ${attempt}/${maxRetries} for page ${pageNum}: ${err.message}`);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }

  _summarizeContext(structured) {
    if (!structured) return "";
    if (structured.type === "npc") return `Previous page: NPC "${structured.name}"`;
    if (structured.type === "narrative") return `Previous page: ${(structured.text || "").substring(0, 100)}`;
    return "";
  }

  _safeName() {
    return this.pdfFilename.replace(/\.pdf$/i, "").replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_|_$/g, "").toLowerCase();
  }

  async _resolvePdfFromJack(filename) {
    try {
      const helyx = globalThis.game?.helyx;
      if (helyx?.context?.pdfFilename) {
        const resp = await fetch(helyx.context.pdfFilename);
        if (resp.ok) return await resp.arrayBuffer();
      }
      const resp = await fetch(filename);
      if (resp.ok) return await resp.arrayBuffer();
      for (const dir of ["", "Data/", "../"]) {
        const r = await fetch(`${dir}${filename}`);
        if (r.ok) return await r.arrayBuffer();
      }
    } catch { /* ignore */ }
    return null;
  }

  async _showSummary() {
    const content = await renderTemplate("modules/vox-pdf-importer/templates/vox-pdf-result.hbs", {
      actors: this.results.actors,
      journals: this.results.journals,
      scenes: this.results.scenes,
      processed: this.progressDialog?.totalPages || 0,
    });
    new Dialog({
      title: "Vox AI PDF Import — Complete",
      content,
      buttons: { ok: { icon: '<i class="fas fa-check"></i>', label: "Done" } },
    }).render(true);
  }
}

globalThis.VoxPdfImport = VoxPdfImport;
