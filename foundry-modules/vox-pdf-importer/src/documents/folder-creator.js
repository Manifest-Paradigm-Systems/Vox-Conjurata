/**
 * FolderCreator — Creates the import folder hierarchy under a root "Vox AI Imports" folder.
 */
export class FolderCreator {

  /**
   * Ensure the import folder tree exists for a given PDF.
   * @param {string} pdfFilename - PDF filename (used for adventure subfolder name)
   * @returns {Promise<object>} Map of document type to folder ID
   */
  static async ensureFolders(pdfFilename) {
    const pdfName = pdfFilename.replace(/\.pdf$/i, "").replace(/[^a-zA-Z0-9 ]/g, " ").trim();

    // Root folder
    let rootFolder = game.folders.find(f => f.name === "Vox AI Imports" && f.type === "Root");
    if (!rootFolder) {
      [rootFolder] = await Folder.createDocuments([{
        name: "Vox AI Imports",
        type: "Root",
        sorting: "m",
      }]);
    }

    // Adventure-specific folder
    let advFolder = game.folders.find(
      f => f.name === pdfName && f.type === "Root" && f.folder?.id === rootFolder.id
    );
    if (!advFolder) {
      [advFolder] = await Folder.createDocuments([{
        name: pdfName,
        type: "Root",
        folder: rootFolder.id,
        sorting: "m",
      }]);
    }

    // Sub-folders per document type
    const subFolders = {};
    const types = [
      ["Actors", "Actor"],
      ["Journals", "JournalEntry"],
      ["Scenes", "Scene"],
    ];
    for (const [label, type] of types) {
      let f = game.folders.find(
        f => f.name === label && f.type === type && f.folder?.id === advFolder.id
      );
      if (!f) {
        [f] = await Folder.createDocuments([{
          name: label,
          type,
          folder: advFolder.id,
          sorting: "m",
        }]);
      }
      subFolders[type] = f.id;
    }

    return subFolders;
  }
}
