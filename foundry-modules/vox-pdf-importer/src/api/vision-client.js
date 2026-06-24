/**
 * VisionClient — Sends rendered PDF page images to the Vox vision API
 * and receives structured Pathfinder 2e data back.
 */
export class VisionClient {

  /**
   * @param {string} endpoint - URL for the vision API (default: orchestrator endpoint)
   */
  constructor(endpoint = "/api/v1/pdf-import-vision") {
    this.endpoint = endpoint;
  }

  /**
   * Send a PDF page image to the vision API for stat block / text extraction.
   * @param {string} pageBase64 - Base64-encoded PNG data URI
   * @param {number} pageNumber - 1-indexed page number
   * @param {string} previousContext - Summary of previous page for continuity
   * @param {object} options - Additional options
   * @returns {Promise<object>} { page, raw_extraction, structured_data, has_content }
   */
  async analyzePage(pageBase64, pageNumber, previousContext = "", options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 120000);

    try {
      const response = await fetch(this.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          page_image: pageBase64,
          page_number: pageNumber,
          previous_context: previousContext,
          max_tokens: options.maxTokens || 2048,
          temperature: options.temperature ?? 0.1,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => "Unknown error");
        throw new Error(`Vision API error ${response.status}: ${errorText.slice(0, 200)}`);
      }

      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }
}
