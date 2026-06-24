/**
 * vox-pdf-importer — Main entry point.
 * Provides an AI-powered PDF import fallback for unsupported Pathfinder 2e PDFs
 * using Vox's vision AI infrastructure.
 */
import { PdfPageRenderer } from "./pdf/page-renderer.js";
import { PageAnalyzer } from "./pdf/page-analyzer.js";
import { VisionClient } from "./api/vision-client.js";
import { ActorCreator } from "./documents/actor-creator.js";
import { JournalCreator } from "./documents/journal-creator.js";
import { SceneCreator } from "./documents/scene-creator.js";
import { FolderCreator } from "./documents/folder-creator.js";
import { VoxPdfProgressDialog } from "./ui/progress-dialog.js";

const MODULE_ID = "vox-pdf-importer";

// ─── Hooks ────────────────────────────────────────────────────────────

Hooks.once("init", () => {
  // Register settings
  game.settings.register(MODULE_ID, "visionApiEndpoint", {
    name: "VOXPDF.Settings.VisionEndpoint",
    hint: "VOXPDF.Settings.VisionEndpointHint",
    scope: "world",
    config: true,
    type: String,
    default: "/api/v1/pdf-import-vision",
  });

  game.settings.register(MODULE_ID, "useLlmRefinement", {
    name: "VOXPDF.Settings.UseLLMRefinement",
    hint: "VOXPDF.Settings.UseLLMRefinementHint",
    scope: "world",
    config: true,
    type: Boolean,
    default: true,
  });

  game.settings.register(MODULE_ID, "maxRetries", {
    name: "VOXPDF.Settings.MaxRetries",
    hint: "VOXPDF.Settings.MaxRetriesHint",
    scope: "user",
    config: true,
    type: Number,
    default: 3,
    choices: { 1: "1", 2: "2", 3: "3", 5: "5" },
  });

  game.settings.register(MODULE_ID, "createJournals", {
    name: "VOXPDF.Settings.CreateJournals",
    hint: "VOXPDF.Settings.CreateJournalsHint",
    scope: "world",
    config: true,
    type: Boolean,
    default: true,
  });

  game.settings.register(MODULE_ID, "createScenes", {
    name: "VOXPDF.Settings.CreateScenes",
    hint: "VOXPDF.Settings.CreateScenesHint",
    scope: "world",
    config: true,
    type: Boolean,
    default: true,
  });
});

Hooks.once("ready", () => {
  // Register sidebar button
  game.voxPdfImporter = {
    start: (pdfFilename) => new VoxPdfImport().start(pdfFilename),
  };

  // Inject button into Jack's unknown PDF dialog
  Hooks.on("renderDialog", (dialog, html) => {
    const title = dialog?.title;
    if (!title || !title.includes("Helyx")) return;
    if (!game.modules.get("jacks-pf2e-pdf-import")?.active) return;

    const buttonsDiv = html.find(".dialog-buttons");
    if (!buttonsDiv.length) return;
    if (buttonsDiv.find(".vox-pdf-import-btn").length) return; // Already injected

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

// ─── Main Import Flow ──────────────────────────────────────────────────

class VoxPdfImport {
  constructor() {
    this.renderer = null;
    this.visionClient = new VisionClient(
      game.settings.get(MODULE_ID, "visionApiEndpoint")
    );
    this.progressDialog = null;
    this.results = { actors: 0, journals: 0, scenes: 0, items: 0 };
    this.pdfData = null;
    this.pdfFilename = "";
  }

  /**
   * Start import from the standalone dialog (user picks a file).
   */
  async start() {
    const file = await this._promptForPdf();
    if (!file) return;
    this.pdfData = file.buffer;
    this.pdfFilename = file.name;
    await this._run();
  }

  /**
   * Start import from Jack's unknown PDF dialog.
   * Tries to resolve the PDF path from Jack's module context.
   */
  async startFromJack(pdfFilename) {
    this.pdfFilename = pdfFilename;
    this.pdfData = await this._resolvePdfFromJack(pdfFilename);
    if (!this.pdfData) {
      // Fall back to manual file picker
      const file = await this._promptForPdf();
      if (!file) return;
      this.pdfData = file.buffer;
      this.pdfFilename = file.name;
    }
    await this._run();
  }

  // ─── Core Import Pipeline ────────────────────────────────────────────

  async _run() {
    try {
      // 1. Initialize pdf.js
      this.renderer = await PdfPageRenderer.create();
      const numPages = await this.renderer.loadPdf(this.pdfData);

      // 2. Show progress dialog
      this.progressDialog = new VoxPdfProgressDialog(numPages);
      this.progressDialog.render(true);

      // 3. Create folder structure
      const folders = await FolderCreator.ensureFolders(this.pdfFilename);

      // 4. Process each page
      let previousContext = "";
      const maxRetries = game.settings.get(MODULE_ID, "maxRetries");

      for (let pageNum = 1; pageNum <= numPages; pageNum++) {
        if (this.progressDialog.isCancelled()) {
          ui.notifications.info("Vox PDF | Import cancelled.");
          break;
        }

        this.progressDialog.updateProgress(pageNum, `Analyzing page ${pageNum}...`);

        try {
          // 4a. Get text content for pre-filter
          const textContent = await this.renderer.getTextContent(pageNum);

          // 4b. Categorize page
          const category = PageAnalyzer.categorize(textContent);

          // 4c. Render page to image
          this.progressDialog.updateProgress(pageNum, `Rendering page ${pageNum}...`);
          const canvas = await this.renderer.renderPageToCanvas(pageNum);

          // 4d. Handle by category
          if (category === "art") {
            // Full-page illustration — embed in a journal entry
            const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/webp", 0.85));
            const artPath = `vox-pdf-imports/${this._safeName()}/art`;
            const result = await FilePicker.upload("data", artPath, new File([blob], `page_${pageNum}.webp`), {}, { notify: false });
            const imgSrc = result.path;

            const journal = await JournalEntry.createDocuments([{
              name: `Art — Page ${pageNum}`,
              folder: folders.JournalEntry,
              pages: [{
                name: `Page ${pageNum}`,
                type: "text",
                text: { content: `<p><img src="${imgSrc}" style="width:100%;max-width:100%;"></p>`, format: 1 },
              }],
            }]);
            this.results.journals++;
            this.progressDialog.recordResult("success");
            previousContext = `Previous page: art/illustration`;
          } else {
            // statblock, narrative, or map — send to vision API
            const base64 = canvas.toDataURL("image/png");
            this.progressDialog.updateProgress(pageNum, `AI analyzing page ${pageNum}...`);
            const result = await this._callVisionWithRetry(base64, pageNum, previousContext, maxRetries);

            if (result.has_content) {
              const sd = result.structured_data;
              const type = sd?.type;

              if (type === "npc") {
                const actors = await ActorCreator.createFromParsedData([sd], folders.Actor);
                this.results.actors += actors.length;
                this.progressDialog.recordResult("success");
              } else if (type === "narrative" && game.settings.get(MODULE_ID, "createJournals")) {
                const journal = await JournalCreator.createFromParsedData(sd, folders.JournalEntry);
                if (journal) {
                  this.results.journals++;
                  this.progressDialog.recordResult("success");
                }
              } else {
                this.progressDialog.recordResult("skipped");
              }
              previousContext = this._summarizeContext(sd);
            } else {
              this.progressDialog.recordResult("skipped");
            }
          }
        } catch (err) {
          console.error(`Vox PDF | Page ${pageNum} failed:`, err);
          this.progressDialog.recordResult("error");
          this.progressDialog.updateProgress(pageNum, `Error: ${err.message.slice(0, 80)}`);
        }
      }

      // 5. Show completion
      this.progressDialog?.close();
      await this._showSummary();
    } catch (err) {
      console.error("Vox PDF | Import failed:", err);
      ui.notifications.error(`Vox PDF Import failed: ${err.message}`);
      this.progressDialog?.close();
    } finally {
      this.renderer?.destroy();
    }
  }

  // ─── Vision API ──────────────────────────────────────────────────────

  async _callVisionWithRetry(base64, pageNum, previousContext, maxRetries) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        return await this.visionClient.analyzePage(base64, pageNum, previousContext);
      } catch (err) {
        if (attempt === maxRetries) throw err;
        const delay = Math.pow(2, attempt) * 1000;
        this.progressDialog.updateProgress(
          pageNum,
          `Retrying page ${pageNum} (${attempt + 1}/${maxRetries})...`
        );
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }

  _summarizeContext(structured) {
    if (!structured) return "";
    if (structured.type === "npc") {
      return `Previous page: NPC "${structured.name}" (Level ${structured.level})`;
    }
    if (structured.type === "narrative") {
      return `Previous page: ${(structured.text || "").substring(0, 100)}`;
    }
    return "";
  }

  _safeName() {
    return this.pdfFilename.replace(/\.pdf$/i, "").replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_|_$/g, "").toLowerCase();
  }

  // ─── UI Helpers ─────────────────────────────────────────────────────

  async _promptForPdf() {
    return new Promise(resolve => {
      const fp = new foundry.applications.apps.FilePicker({
        type: "data",
        callback: async (path) => {
          try {
            const resp = await fetch(path);
            if (!resp.ok) { resolve(null); return; }
            const buffer = await resp.arrayBuffer();
            const name = path.split("/").pop();
            resolve({ buffer, name });
          } catch { resolve(null); }
        },
        extensions: [".pdf"],
      });
      fp.render(true);
    });
  }

  async _resolvePdfFromJack(filename) {
    try {
      // Try to find the PDF in Jack's import context
      const helyx = globalThis.game?.helyx;
      if (helyx?.context?.pdfFilename) {
        const resp = await fetch(helyx.context.pdfFilename);
        if (resp.ok) return await resp.arrayBuffer();
      }
      // Try direct path resolution
      const resp = await fetch(filename);
      if (resp.ok) return await resp.arrayBuffer();

      // Try common Foundry data paths
      for (const dir of ["", "Data/", "../"]) {
        const resp = await fetch(`${dir}${filename}`);
        if (resp.ok) return await resp.arrayBuffer();
      }
    } catch { /* fall through */ }
    return null;
  }

  async _showSummary() {
    const summary = {
      actors: this.results.actors,
      journals: this.results.journals,
      scenes: this.results.scenes,
      items: this.results.items,
      processed: this.progressDialog?.totalPages || 0,
    };

    const content = await renderTemplate(
      "modules/vox-pdf-importer/templates/vox-pdf-result.hbs",
      summary
    );

    new Dialog({
      title: "Vox AI PDF Import — Complete",
      content,
      buttons: {
        ok: { icon: '<i class="fas fa-check"></i>', label: "Done" },
      },
    }).render(true);
  }
}

// Expose for debugging
globalThis.VoxPdfImport = VoxPdfImport;
