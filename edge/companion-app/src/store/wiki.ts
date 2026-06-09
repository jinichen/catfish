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

  // P17 (6/5 鸿波): 上次 loadFiles 成功的时间 (debug 用, 当前 WikiTree
  // 真**`mount 时无脑 reload`**真**`不依赖**它**). 真**`留字段 future 算 cache 用`**.
  lastLoadTs: number;

  search: string;
  kindFilter: "all" | "entity" | "concept" | "query";
  query:
    | "none"
    | "recent-week" // 真 7 天 mtime
    | "orphan-concept" // 概念 真**真**真**0 inbound link**真**
    | "top-tag" // top tag 真**filter (后续选 tag)
    | "dangling"; // file 含真 dangling wikilink
  selectedTag: string | null;

  loadFiles: () => Promise<void>;
  selectFile: (relPath: string | null) => Promise<void>;
  setSearch: (s: string) => void;
  setKindFilter: (k: "all" | "entity" | "concept" | "query") => void;
  setQuery: (q: WikiState["query"]) => void;
  setSelectedTag: (tag: string | null) => void;
}

export const useWikiStore = create<WikiState>((set) => ({
  files: [],
  filesLoading: false,
  filesError: null,
  lastLoadTs: 0,

  selectedPath: null,
  selectedFile: null,
  selectedLoading: false,
  selectedError: null,

  search: "",
  kindFilter: "all",
  query: "none",
  selectedTag: null,

  loadFiles: async () => {
    set({ filesLoading: true, filesError: null });
    try {
      const files = await wikiListFiles();
      set({ files, filesLoading: false, lastLoadTs: Date.now() });
    } catch (e) {
      set({ filesLoading: false, filesError: String(e) });
    }
  },

  selectFile: async (relPath: string | null) => {
    // P3.3.4 (6/9): 删 entity 后传 null 清 selection, list 回退到默认无选中
    if (relPath === null) {
      set({
        selectedPath: null,
        selectedFile: null,
        selectedLoading: false,
        selectedError: null,
      });
      return;
    }
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
  setQuery: (q) => set({ query: q, selectedTag: q === "top-tag" ? null : null }),
  setSelectedTag: (tag) => set({ selectedTag: tag, query: tag ? "top-tag" : "none" }),
}));
