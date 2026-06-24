/**
 * VoxPdfProgressDialog — Page-by-page progress tracker for PDF import.
 */
export class VoxPdfProgressDialog extends Application {
  constructor(totalPages, options = {}) {
    super(options);
    this.totalPages = totalPages;
    this.currentPage = 0;
    this.successCount = 0;
    this.errorCount = 0;
    this.skippedCount = 0;
    this.status = "Starting...";
    this.cancelled = false;
  }

  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions, {
      id: "vox-pdf-progress",
      title: "Vox AI PDF Import — Progress",
      template: "modules/vox-pdf-importer/templates/vox-pdf-progress.hbs",
      popOut: true,
      width: 420,
      height: "auto",
      minimizable: false,
      closable: false,
    });
  }

  async getData() {
    return {
      currentPage: this.currentPage,
      totalPages: this.totalPages,
      percent: this.totalPages > 0 ? Math.round((this.currentPage / this.totalPages) * 100) : 0,
      status: this.status,
      successCount: this.successCount,
      errorCount: this.errorCount,
      skippedCount: this.skippedCount,
    };
  }

  activateListeners(html) {
    super.activateListeners(html);
    html.find(".vox-pdf-cancel-btn").click(() => {
      this.cancelled = true;
      this.status = "Cancelling...";
      this.render();
    });
  }

  updateProgress(page, status) {
    this.currentPage = page;
    this.status = status;
    this.render(false);
  }

  recordResult(type) {
    if (type === "success") this.successCount++;
    else if (type === "error") this.errorCount++;
    else if (type === "skipped") this.skippedCount++;
  }

  isCancelled() {
    return this.cancelled;
  }

  close(options = {}) {
    this.cancelled = true;
    return super.close(options);
  }
}
