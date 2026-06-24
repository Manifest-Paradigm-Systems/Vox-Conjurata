/**
 * ParserRegistry — Maps game system IDs to their stat block parsers.
 * Auto-detects the active system and provides the correct parser.
 */
import { Pf2eParser } from "./pf2e-parser.js";
import { Dnd5eParser } from "./dnd5e-parser.js";

const PARSERS = {
  pf2e: Pf2eParser,
  dnd5e: Dnd5eParser,
};

export class ParserRegistry {

  /**
   * Get the parser for a given system ID.
   * @param {string} systemId - `game.system.id` value ("pf2e", "dnd5e")
   * @returns {object} Parser with `toActorCreateData()`, `systemId`, `actorType`
   */
  static get(systemId) {
    const parser = PARSERS[systemId];
    if (!parser) {
      throw new Error(`Vox PDF | Unsupported game system: "${systemId}". Supported: ${Object.keys(PARSERS).join(", ")}`);
    }
    return parser;
  }

  /**
   * Auto-detect the active game system.
   * @returns {string} System ID ("pf2e", "dnd5e", or "unknown")
   */
  static detect() {
    const id = game?.system?.id;
    if (id && PARSERS[id]) return id;
    return "unknown";
  }

  /** List of supported system IDs */
  static get supported() {
    return Object.keys(PARSERS);
  }

  /** Display labels for supported systems */
  static get labels() {
    return {
      pf2e: "Pathfinder 2e",
      dnd5e: "D&D 5e",
    };
  }
}
