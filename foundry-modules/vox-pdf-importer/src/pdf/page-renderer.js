/**
 * PdfPageRenderer — Loads a PDF via pdf.js and renders pages to canvas.
 * Uses Foundry V14's built-in @foundryvtt/pdfjs library.
 */
export class PdfPageRenderer {

  constructor() {
    this.pdfjs = null;
    this.pdfDoc = null;
    this.numPages = 0;
  }

  /**
   * Initialize pdf.js — tries Foundry's built-in copy, then falls back.
   */
  static async create() {
    const renderer = new PdfPageRenderer();
    renderer.pdfjs = globalThis.pdfjsLib;
    if (!renderer.pdfjs) {
      try {
        renderer.pdfjs = await import("/modules/jacks-pf2e-pdf-import/src/pdf.mjs");
      } catch {
        try {
          renderer.pdfjs = await import("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.mjs");
        } catch (e) {
          throw new Error("Vox PDF | Could not load pdf.js library");
        }
      }
    }
    return renderer;
  }

  /**
   * Load a PDF from an ArrayBuffer.
   * @param {ArrayBuffer} arrayBuffer - Raw PDF data
   * @returns {number} Number of pages
   */
  async loadPdf(arrayBuffer) {
    const loadingTask = this.pdfjs.getDocument({ data: arrayBuffer });
    this.pdfDoc = await loadingTask.promise;
    this.numPages = this.pdfDoc.numPages;
    return this.numPages;
  }

  /**
   * Render a single page to an HTMLCanvasElement.
   * @param {number} pageNum - 1-indexed page number
   * @param {number} scale - Render scale (1.5 recommended for vision AI)
   * @returns {HTMLCanvasElement}
   */
  async renderPageToCanvas(pageNum, scale = 1.5) {
    const page = await this.pdfDoc.getPage(pageNum);
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");

    await page.render({ canvasContext: ctx, viewport }).promise;
    return canvas;
  }

  /**
   * Render a page directly to a base64 PNG data URI.
   */
  async renderPageToBase64(pageNum, scale = 1.5) {
    const canvas = await this.renderPageToCanvas(pageNum, scale);
    return canvas.toDataURL("image/png");
  }

  /**
   * Get text content from a page for pre-filter heuristics.
   * @param {number} pageNum
   * @returns {Promise<object>} pdf.js text content items
   */
  async getTextContent(pageNum) {
    const page = await this.pdfDoc.getPage(pageNum);
    return await page.getTextContent();
  }

  destroy() {
    if (this.pdfDoc) {
      this.pdfDoc.destroy();
      this.pdfDoc = null;
    }
  }
}
