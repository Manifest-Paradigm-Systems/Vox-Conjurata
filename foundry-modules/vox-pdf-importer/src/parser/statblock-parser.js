/**
 * StatBlockParser — Transforms vision AI JSON output into PF2e actor createDocuments format.
 * Maps the structured NPC data from the vision model to Foundry's PF2e system schema.
 */
export class StatBlockParser {

  /**
   * Convert structured NPC data into a PF2e Actor.createDocuments-compatible model.
   * @param {object} structuredData - Parsed JSON from vision API
   * @returns {object|null} Actor creation data, or null if not an NPC
   */
  static toActorCreateData(structuredData) {
    if (!structuredData || structuredData.type === "empty") return null;
    if (structuredData.type === "narrative") return null;
    if (structuredData.type === "map") return null;

    const sd = structuredData;
    const name = sd.name || "Unnamed NPC";

    const model = {
      name,
      type: "npc",
      system: {
        details: {
          level: { value: sd.level ?? 1 },
          alignment: { value: sd.alignment || "N" },
          blurb: "",
          publicBiography: "",
          biography: { value: "", public: "" },
        },
        traits: {
          size: { value: this._mapSize(sd.size) },
          rarity: "common",
          value: sd.traits || [],
        },
        attributes: {
          hp: { value: sd.hp ?? 10, max: sd.hp ?? 10 },
          ac: { value: sd.ac ?? 10, details: "" },
        },
        saves: {
          fortitude: { value: sd.saves?.fort ?? 0 },
          reflex: { value: sd.saves?.ref ?? 0 },
          will: { value: sd.saves?.will ?? 0 },
        },
        perception: { value: sd.perception ?? 0 },
        abilities: {
          str: { value: sd.abilities?.str ?? 10 },
          dex: { value: sd.abilities?.dex ?? 10 },
          con: { value: sd.abilities?.con ?? 10 },
          int: { value: sd.abilities?.int ?? 10 },
          wis: { value: sd.abilities?.wis ?? 10 },
          cha: { value: sd.abilities?.cha ?? 10 },
        },
        skills: this._buildSkills(sd.skills),
        speed: {
          value: this._parseSpeed(sd.speed),
          otherSpeeds: [],
        },
        languages: {
          value: sd.languages || [],
          details: "",
        },
        source: {
          value: sd.source || "Vox AI Import",
        },
      },
      items: [],
      prototypeToken: {
        name,
        texture: { src: "icons/svg/mystery-man.svg" },
        width: 1,
        height: 1,
        disposition: -1,
        displayName: 50,
      },
      img: "icons/svg/mystery-man.svg",
    };

    // Build embedded items for attacks
    if (sd.attacks && Array.isArray(sd.attacks)) {
      for (const attack of sd.attacks) {
        model.items.push({
          name: attack.name || "Strike",
          type: "melee",
          system: {
            damage: {
              base: {
                damage: attack.damage || "1d6",
                type: this._mapDamageType(attack.damage_type),
              },
            },
            bonus: { value: attack.bonus ?? 0 },
            traits: { value: attack.traits || [] },
          },
        });
      }
    }

    // Build embedded items for special abilities
    if (sd.abilities_list && Array.isArray(sd.abilities_list)) {
      for (const ability of sd.abilities_list) {
        model.items.push({
          name: ability.name || "Special Ability",
          type: "action",
          system: {
            description: { value: ability.description || "" },
          },
        });
      }
    }

    // Build embedded items for spells
    if (sd.spells && Array.isArray(sd.spells)) {
      for (const spellGroup of sd.spells) {
        if (spellGroup.spells && Array.isArray(spellGroup.spells)) {
          for (const spellName of spellGroup.spells) {
            model.items.push({
              name: spellName,
              type: "spell",
              system: {
                level: { value: spellGroup.level ?? 1 },
              },
            });
          }
        }
      }
    }

    return model;
  }

  /** Create a journal entry page data from a narrative block. */
  static toJournalPageData(structuredData) {
    if (!structuredData || structuredData.type !== "narrative") return null;

    return {
      name: structuredData.title || "Imported Text",
      type: "text",
      text: {
        content: `<p>${structuredData.text || ""}</p>`,
        format: 1,
      },
    };
  }

  /** Create scene data from a map block. */
  static toSceneData(structuredData) {
    if (!structuredData || structuredData.type !== "map") return null;

    return {
      name: structuredData.name || "Imported Map",
      width: 30,
      height: 20,
      padding: 0.2,
      backgroundColor: "#999999",
      grid: {
        type: 1,
        size: 100,
        color: "#000000",
        alpha: 0.2,
        distance: 5,
        units: "ft",
      },
    };
  }

  static _mapSize(size) {
    const map = {
      tiny: "tiny", sm: "sm", small: "sm",
      med: "med", medium: "med",
      lg: "lg", large: "lg",
      huge: "huge", grg: "grg", gargantuan: "grg",
    };
    return map[size?.toLowerCase()] || "med";
  }

  static _parseSpeed(speed) {
    if (!speed) return 25;
    const match = String(speed).match(/(\d+)/);
    return match ? parseInt(match[1]) : 25;
  }

  static _buildSkills(skills) {
    if (!skills) return {};
    const result = {};
    for (const [name, value] of Object.entries(skills)) {
      result[name.toLowerCase()] = { value: typeof value === "number" ? value : 0 };
    }
    return result;
  }

  static _mapDamageType(type) {
    const map = {
      b: "bludgeoning", bludgeoning: "bludgeoning",
      p: "piercing", piercing: "piercing",
      s: "slashing", slashing: "slashing",
      fire: "fire", cold: "cold", acid: "acid",
      electricity: "electricity", sonic: "sonic",
      positive: "positive", negative: "negative",
      mental: "mental", poison: "poison",
    };
    return map[type?.toLowerCase()] || "bludgeoning";
  }
}
