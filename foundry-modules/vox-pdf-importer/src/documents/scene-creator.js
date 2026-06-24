/**
 * SceneCreator — Creates scenes from parsed map data with the Foundry v14 Level fix.
 */
export class SceneCreator {

  /**
   * Create scenes from parsed map data.
   * @param {Array} sceneDataList
   * @param {string} folderId - Target folder ID
   * @returns {Promise<Array>} Created scene documents
   */
  static async createFromParsedData(sceneDataList, folderId) {
    const created = [];
    for (const sceneData of sceneDataList) {
      if (!sceneData || sceneData.type !== "map") continue;

      const model = {
        name: sceneData.name || "Imported Map",
        folder: folderId,
        width: sceneData.gridSize?.width || 30,
        height: sceneData.gridSize?.height || 20,
        padding: 0.2,
        backgroundColor: sceneData.backgroundColor || "#999999",
        grid: {
          type: 1,
          size: sceneData.gridSize?.pixels || 100,
          color: "#000000",
          alpha: 0.2,
          distance: sceneData.gridSize?.distance || 5,
          units: "ft",
        },
        background: {
          src: sceneData.background?.src || "",
          offsetX: sceneData.background?.offsetX ?? 0,
          offsetY: sceneData.background?.offsetY ?? 0,
          scaleX: sceneData.background?.scaleX ?? 1,
          scaleY: sceneData.background?.scaleY ?? 1,
          rotation: sceneData.background?.rotation ?? 0,
        },
      };

      // Foundry v14 Level Background Fix
      if (model.background?.src) {
        model.levels = [{
          name: "Ground",
          elevation: { bottom: 0, top: 20 },
          background: {
            src: model.background.src,
            color: model.backgroundColor ?? "#999999",
          },
          textures: {
            offsetX: model.background.offsetX ?? 0,
            offsetY: model.background.offsetY ?? 0,
            scaleX: model.background.scaleX ?? 1,
            scaleY: model.background.scaleY ?? 1,
            rotation: model.background.rotation ?? 0,
          },
        }];
      }
      delete model.background;

      if (!game.scenes.active) {
        model.active = true;
      }

      try {
        const [scene] = await Scene.createDocuments([model]);
        scene.createSceneThumbnail().then(data => {
          scene.update({ thumb: data.thumb });
        });
        created.push(scene);
      } catch (err) {
        console.error(`Vox PDF | Failed to create scene ${model.name}:`, err);
      }
    }
    return created;
  }
}
