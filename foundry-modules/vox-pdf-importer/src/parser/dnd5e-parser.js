/**
 * Dnd5eParser — Transforms vision AI JSON output into D&D 5e actor createDocuments format.
 * Maps structured NPC data to the dnd5e system's NPC schema.
 */
export class Dnd5eParser {
  static systemId = "dnd5e";
  static actorType = "npc";

  /**
   * Convert structured NPC data into a D&D 5e Actor.createDocuments-compatible model.
   * @param {object} structuredData - Parsed JSON from vision API
   * @returns {object|null} Actor creation data, or null if not an NPC
   */
  static toActorCreateData(structuredData) {
    if (!structuredData || structuredData.type === "empty") return null;
    if (structuredData.type === "narrative") return null;

    const sd = structuredData;
    const name = sd.name || "Unnamed NPC";

    // Build skills mapping from full names to 3-letter codes
    const skills = {};
    if (sd.skills) {
      const skillMap = {
        acrobatics: "acr", "animal handling": "ani", arcana: "arc",
        athletics: "ath", deception: "dec", history: "his",
        insight: "ins", intimidation: "itm", investigation: "inv",
        medicine: "med", nature: "nat", perception: "prc",
        performance: "prf", persuasion: "per", religion: "rel",
        "sleight of hand": "slt", stealth: "ste", survival: "sur",
      };
      for (const [fullName, code] of Object.entries(skillMap)) {
        if (sd.skills[fullName] !== undefined) {
          skills[code] = { value: Number(sd.skills[fullName]) || 0, ability: this._skillAbility(code), bonuses: { check: "", passive: "" } };
        }
      }
    }

    const model = {
      name,
      type: "npc",
      system: {
        abilities: {
          str: { value: sd.abilities?.str ?? 10 },
          dex: { value: sd.abilities?.dex ?? 10 },
          con: { value: sd.abilities?.con ?? 10 },
          int: { value: sd.abilities?.int ?? 10 },
          wis: { value: sd.abilities?.wis ?? 10 },
          cha: { value: sd.abilities?.cha ?? 10 },
        },
        attributes: {
          ac: { calc: "natural", flat: sd.ac ?? 10 },
          hp: { value: sd.hp ?? 10, max: sd.hp ?? 10, temp: 0, tempmax: 0, formula: this._inferHpFormula(sd) },
          movement: { walk: this._parseSpeed(sd.speed), units: "ft", hover: false },
          senses: { ranges: {}, units: "ft", special: "" },
          spellcasting: "",
          exhaustion: 0,
          concentration: { bonuses: { save: "" }, limit: 1 },
          attunement: { max: 3 },
        },
        details: {
          alignment: sd.alignment || "",
          type: { value: this._inferType(sd), subtype: "", swarm: "", custom: "" },
          cr: sd.challenge ?? sd.cr ?? 1,
          biography: { value: "", public: "" },
          treasure: { value: [] },
        },
        traits: {
          size: this._mapSize(sd.size),
          di: { value: [], custom: "", bypasses: [] },
          dr: { value: [], custom: "", bypasses: [] },
          dv: { value: [], custom: "", bypasses: [] },
          ci: { value: [], custom: "" },
          languages: { value: sd.languages || [], custom: "", communication: {} },
        },
        skills,
        bonuses: {
          mwak: { attack: "", damage: "" },
          rwak: { attack: "", damage: "" },
          msak: { attack: "", damage: "" },
          rsak: { attack: "", damage: "" },
          abilities: { check: "", save: "", skill: "" },
          spell: { dc: "" },
        },
        source: { book: "Vox AI Import", page: "", custom: "", license: "", revision: 1, rules: "2024" },
      },
      items: [],
      prototypeToken: {
        name,
        texture: { src: "icons/svg/mystery-man.svg" },
        width: 1, height: 1, disposition: -1, displayName: 50,
      },
      img: "icons/svg/mystery-man.svg",
    };

    // Build embedded items for attacks
    if (sd.attacks && Array.isArray(sd.attacks)) {
      for (const attack of sd.attacks) {
        model.items.push({
          name: attack.name || "Strike",
          type: "weapon",
          system: {
            damage: { base: { damage: attack.damage || "1d6", type: this._mapDamageType(attack.damage_type) } },
            bonus: { value: attack.bonus ?? 0 },
            proficient: true,
          },
        });
      }
    }

    // Build embedded items for special abilities
    if (sd.abilities_list && Array.isArray(sd.abilities_list)) {
      for (const ability of sd.abilities_list) {
        model.items.push({
          name: ability.name || "Special Ability",
          type: "feat",
          system: { description: { value: ability.description || "" } },
        });
      }
    }

    return model;
  }

  /** Create a journal entry page from narrative data. */
  static toJournalPageData(structuredData) {
    if (!structuredData || structuredData.type !== "narrative") return null;
    return {
      name: structuredData.title || "Imported Text",
      type: "text",
      text: { content: `<p>${structuredData.text || ""}</p>`, format: 1 },
    };
  }

  /** Create scene data from a map block. */
  static toSceneData(structuredData) {
    if (!structuredData || structuredData.type !== "map") return null;
    return {
      name: structuredData.name || "Imported Map",
      width: 30, height: 20, padding: 0.2,
      backgroundColor: "#999999",
      grid: { type: 1, size: 100, color: "#000000", alpha: 0.2, distance: 5, units: "ft" },
    };
  }

  static _mapSize(size) {
    const map = {
      tiny: "tiny", t: "tiny",
      sm: "sm", small: "sm",
      med: "med", medium: "med",
      lg: "lg", large: "lg",
      huge: "huge", hg: "hg",
      grg: "grg", gargantuan: "grg",
    };
    return map[size?.toLowerCase()] || "med";
  }

  static _parseSpeed(speed) {
    if (!speed) return 30;
    const match = String(speed).match(/(\d+)/);
    return match ? parseInt(match[1]) : 30;
  }

  static _inferType(sd) {
    const traits = sd.traits || [];
    const typeMap = {
      humanoid: "humanoid", beast: "beast", dragon: "dragon",
      monstrosity: "monstrosity", plant: "plant", undead: "undead",
      fey: "fey", fiend: "fiend", celestial: "celestial",
      elemental: "elemental", ooze: "ooze", construct: "construct",
      aberration: "aberration", giant: "giant", goblinoid: "goblinoid",
    };
    for (const t of traits) {
      const lower = t.toLowerCase();
      for (const [key, val] of Object.entries(typeMap)) {
        if (lower.includes(key)) return val;
      }
    }
    return "";
  }

  static _inferHpFormula(sd) {
    if (sd.hp_formula) return sd.hp_formula;
    const hp = sd.hp || 10;
    const con = (sd.abilities?.con ?? 10);
    const mod = Math.floor((con - 10) / 2);
    const dieCount = Math.max(1, Math.round(hp / 8));
    return `${dieCount}d8${mod >= 0 ? "+" : ""}${mod * dieCount}`;
  }

  static _skillAbility(code) {
    const map = {
      acr: "dex", ani: "wis", arc: "int", ath: "str",
      dec: "cha", his: "int", ins: "wis", itm: "cha",
      inv: "int", med: "wis", nat: "int", prc: "wis",
      prf: "cha", per: "cha", rel: "int", slt: "dex",
      ste: "dex", sur: "wis",
    };
    return map[code] || "dex";
  }

  static _mapDamageType(type) {
    const map = {
      b: "bludgeoning", bludgeoning: "bludgeoning",
      p: "piercing", piercing: "piercing",
      s: "slashing", slashing: "slashing",
      fire: "fire", cold: "cold", acid: "acid",
      lightning: "lightning", thunder: "thunder",
      poison: "poison", psychic: "psychic",
      radiant: "radiant", necrotic: "necrotic",
      force: "force",
    };
    return map[type?.toLowerCase()] || "bludgeoning";
  }
}
