/**
 * ActorCreator — Creates PF2e NPC actors from parsed stat block data.
 */
import { StatBlockParser } from "../parser/statblock-parser.js";

export class ActorCreator {

  /**
   * Create actors from an array of structured NPC data.
   * @param {Array} npcDataList
   * @param {string} folderId - Target folder ID
   * @returns {Promise<Array>} Created actor documents
   */
  static async createFromParsedData(npcDataList, folderId) {
    const created = [];
    for (const npcData of npcDataList) {
      const model = StatBlockParser.toActorCreateData(npcData);
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
