/** BL-CATFISH-WIKI-MODE P3.3.3 (6/4) — wiki state store.
 *
 * 负责:
 *   - 真**load** wiki/*.md 全 list (wikiListFiles)
 *   - selected file (rel_path) + 缓存 read content
 *   - filter (search / kind / tag)
 */

import { create } from "zustand";
import {
  wikiListFiles,
  wikiReadFile,
  type WikiFileInfo,
  type WikiFileFull,
} from "../lib/tauri";

interface WikiState {
  files: WikiFileInfo[];
  filesLoading: boolean;
  filesError: string | null;

  selectedPath: string | null;
  selectedFile: WikiFileFull | null;
  selectedLoading: boolean;
  selectedError: string | null;

  search: string;
  kindFilter: "all" | "entity" | "concept" | "query";

  loadFiles: () => Promise<void>;
  selectFile: (relPath: string) => Promise<void>;
  setSearch: (s: string) => void;
  setKindFilter: (k: "all" | "entity" | "concept" | "query") => void;
}

export const useWikiStore = create<WikiState>((set) => ({
  files: [],
  filesLoading: false,
  filesError: null,

  selectedPath: null,
  selectedFile: null,
  selectedLoading: false,
  selectedError: null,

  search: "",
  kindFilter: "all",

  loadFiles: async () => {
    set({ filesLoading: true, filesError: null });
    try {
      const files = await wikiListFiles();
      set({ files, filesLoading: false });
    } catch (e) {
      set({ filesLoading: false, filesError: String(e) });
    }
  },

  selectFile: async (relPath: string) => {
    set({ selectedPath: relPath, selectedLoading: true, selectedError: null });
    try {
      const full = await wikiReadFile(relPath);
      set({ selectedFile: full, selectedLoading: false });
    } catch (e) {
      set({ selectedLoading: false, selectedError: String(e) });
    }
  },

  setSearch: (s) => set({ search: s }),
  setKindFilter: (k) => set({ kindFilter: k }),
}));
