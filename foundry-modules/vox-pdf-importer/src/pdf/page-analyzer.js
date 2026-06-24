/**
 * PageAnalyzer — Heuristic pre-filter to skip art-only pages before the vision API call.
 * Saves time and resources by only sending pages that likely have stat blocks or text.
 */
export class PageAnalyzer {

  /**
   * Check if a page likely contains stat block content.
   * Looks for PF2e stat block indicators in the raw text items.
   * @param {object} textContent - pdf.js getTextContent() result
   * @returns {boolean}
   */
  static likelyHasStatBlocks(textContent) {
    const text = (textContent?.items || []).map(i => i.str).join(" ");
    if (!text.trim()) return false;

    const statIndicators = [
      /\b(?:N|L[NG]|C[NG]|N[EG])\b/,          // Alignment
      /\b(?:HP|Hit\s*Points)\s*\d+/i,          // Hit Points
      /\bAC\s*\d+/i,                            // Armor Class
      /\bFort\b.*\bRef\b.*\bWill\b/i,           // Saving throws
      /\bPerception\s*\+?\d+/i,                 // Perception
      /\bSpeed\s+\d+/i,                         // Speed
      /\b(?:STR|DEX|CON|INT|WIS|CHA)\b/i,       // Ability scores header
      /\bLevel\s+\d+/i,                         // Creature level
      /\bCREATURE\s+\d+\b/i,                    // Creature number
      /\bSaving\s+Throws?\b/i,                  // Save header
      /\bSkills?\s+(?:Str|Dex|Con|Int|Wis|Cha)\b/i,  // Skill header
    ];

    const matches = statIndicators.filter(r => r.test(text)).length;
    const textItems = textContent.items.length;

    // Art-only pages have very few text items and few stat indicators
    if (textItems < 5 && matches < 2) return false;
    return matches >= 2;
  }

  /**
   * Check if a page is a full-page map (very little text, has images).
   * @param {object} textContent
   * @param {number} imageCount - Number of embedded images detected on the page
   * @returns {boolean}
   */
  static likelyIsMap(textContent, imageCount) {
    const textItems = textContent?.items?.length || 0;
    return textItems < 3 && imageCount > 0;
  }

  /**
   * Check if a page is mostly narrative text (journal entry content).
   */
  static likelyHasNarrative(textContent) {
    const text = (textContent?.items || []).map(i => i.str).join(" ");
    const wordCount = text.split(/\s+/).length;
    return wordCount > 40 && !this.likelyHasStatBlocks(textContent);
  }

  /**
   * Decide whether to send this page to the vision API.
   * Returns a category string: "statblock", "narrative", "map", or "skip".
   */
  static categorize(textContent, imageCount, pageNum) {
    if (this.likelyHasStatBlocks(textContent)) return "statblock";
    if (this.likelyIsMap(textContent, imageCount)) return "map";
    if (this.likelyHasNarrative(textContent)) return "narrative";
    return "skip";
  }
}
