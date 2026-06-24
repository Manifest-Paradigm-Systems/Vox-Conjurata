/**
 * ActorCreator — Creates actors from parsed stat block data using the system-appropriate parser.
 */
import { ParserRegistry } from "../parser/registry.js";

export class ActorCreator {

  /**
   * Create actors from an array of structured NPC data.
   * @param {Array} npcDataList
   * @param {string} folderId - Target folder ID
   * @param {string} systemId - Game system ID ("pf2e", "dnd5e")
   * @returns {Promise<Array>} Created actor documents
   */
  static async createFromParsedData(npcDataList, folderId, systemId) {
    const parser = ParserRegistry.get(systemId);
    const created = [];
    for (const npcData of npcDataList) {
      const model = parser.toActorCreateData(npcData);
      if (!model) continue;

      model.folder = folderId;

      try {
        const [actor] = await Actor.implementation.createDocuments([model]);
        created.push(actor);
      } catch (err) {
        console.error(`Vox PDF | Failed to create actor ${model.name}:`, err);
      }
    }
    return created;
  }
}
