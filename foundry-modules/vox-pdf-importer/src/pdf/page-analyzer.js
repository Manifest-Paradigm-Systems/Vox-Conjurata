/**
 * PageAnalyzer — Categorizes PDF pages by content type to route them
 * to the appropriate processing pipeline.
 */
export class PageAnalyzer {

  static likelyHasStatBlocks(textContent) {
    const text = (textContent?.items || []).map(i => i.str).join(" ");
    if (!text.trim()) return false;

    const statIndicators = [
      /\b(?:N|L[NG]|C[NG]|N[EG])\b/,
      /\b(?:HP|Hit\s*Points)\s*\d+/i,
      /\bAC\s*\d+/i,
      /\bFort\b.*\bRef\b.*\bWill\b/i,
      /\bPerception\s*\+?\d+/i,
      /\bSpeed\s+\d+/i,
      /\b(?:STR|DEX|CON|INT|WIS|CHA)\b/i,
      /\bLevel\s+\d+/i,
    ];
    return statIndicators.filter(r => r.test(text)).length >= 2;
  }

  static likelyIsMap(textContent) {
    return (textContent?.items?.length || 0) < 3;
  }

  static likelyHasNarrative(textContent) {
    const text = (textContent?.items || []).map(i => i.str).join(" ");
    return text.split(/\s+/).length > 40 && !this.likelyHasStatBlocks(textContent);
  }

  /**
   * Categorize a page. Returns: "statblock", "narrative", "art", or "map"
   */
  static categorize(textContent) {
    if (this.likelyHasStatBlocks(textContent)) return "statblock";
    if (this.likelyIsMap(textContent)) return "art";
    if (this.likelyHasNarrative(textContent)) return "narrative";
    return "art";
  }
}
