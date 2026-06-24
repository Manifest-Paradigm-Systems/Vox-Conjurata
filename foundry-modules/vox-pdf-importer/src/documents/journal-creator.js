/**
 * JournalCreator — Creates journal entries from parsed narrative text.
 */
export class JournalCreator {

  /**
   * Create a journal entry from structured narrative data.
   * @param {object} narrativeData - { type: "narrative", title, text }
   * @param {string} folderId - Target folder ID
   * @returns {Promise<object|null>} Created journal entry
   */
  static async createFromParsedData(narrativeData, folderId) {
    if (!narrativeData || narrativeData.type !== "narrative") return null;

    const title = narrativeData.title || "Imported Page";
    const content = narrativeData.text || "";

    try {
      const [journal] = await JournalEntry.createDocuments([{
        name: title,
        folder: folderId,
        pages: [{
          name: title,
          type: "text",
          text: { content: `<p>${content}</p>`, format: 1 },
        }],
      }]);
      return journal;
    } catch (err) {
      console.error(`Vox PDF | Failed to create journal ${title}:`, err);
      return null;
    }
  }
}
