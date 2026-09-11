/** BL-CATFISH-WIKI-MODE P3.3.3 (6/4) — wiki state store.
 *
 * 负责:
 *   - load wiki/*.md 全 list (wikiListFiles)
 *   - selected file (rel_path) + 缓存 read content
 *   - filter (search / kind / tag)
 */

import { create } from "zustand";
import {
  wikiListFiles,
  wikiReadFile,
  listInstalledWikiShared,
  type WikiFileInfo,
  type WikiFileFull,
  type InstalledWikiSharedInfo,
} from "../lib/tauri";

interface WikiState {
  files: WikiFileInfo[];
  filesLoading: boolean;
  filesError: string | null;

  // P3.3.18 Phase 4 (6/10): 已装部门 wiki (~/.catfish/wiki-shared/), read-only
  sharedFiles: InstalledWikiSharedInfo[];

  selectedPath: string | null;
  selectedFile: WikiFileFull | null;
  selectedLoading: boolean;
  selectedError: string | null;

  // P17 (6/5 鸿波): 上次 loadFiles 成功的时间 (debug 用, 当前 WikiTree
  // `mount 时无脑 reload``不依赖它**). `留字段 future 算 cache 用`.
  lastLoadTs: number;

  search: string;
  kindFilter: "all" | "entity" | "concept" | "query";
  query:
    | "none"
    | "recent-week" // 真 7 天 mtime
    | "orphan-concept" // 概念 真0 inbound link
    | "top-tag" // top tag 真filter (后续选 tag)
    | "dangling"; // file 含真 dangling wikilink
  selectedTag: string | null;

  // P3.5.110 (6/25 鸿波 catch "体系名称不能选择"): create modal 跨组件触发 state.
  // 鸿波点 dangling wikilink / 组 header 真→**自动**弹 +新建 modal**, prefill title +
  // kind, 0 学习成本 自动建 dangling 虚拟体系.
  createModalState: {
    open: boolean;
    prefillTitle?: string;
    prefillKind?: "entity" | "concept" | "system";
  };

  // P3.5.111 (6/25 鸿波 catch "点体系名应显整片图不该弹窗"): 虚拟体系
  // 状态 — 鸿波点 dangling 体系名 → WikiGraph 虚拟显该体系子树 (不弹建).
  // selectFile 清掉 virtualSystemName 避免双重 selected 状态错乱.
  virtualSystemName: string | null;

  loadFiles: () => Promise<void>;
  selectFile: (relPath: string | null) => Promise<void>;
  setSearch: (s: string) => void;
  setKindFilter: (k: "all" | "entity" | "concept" | "query") => void;
  setQuery: (q: WikiState["query"]) => void;
  setSelectedTag: (tag: string | null) => void;

  // P3.5.110: modal trigger actions
  openCreateModal: (prefill?: {
    title?: string;
    kind?: "entity" | "concept" | "system";
  }) => void;
  closeCreateModal: () => void;

  // P3.5.111: 虚拟体系真setter — null 清虚拟态
  setVirtualSystem: (name: string | null) => void;
}

let selectRequest = 0;

export const useWikiStore = create<WikiState>((set) => ({
  files: [],
  filesLoading: false,
  filesError: null,
  sharedFiles: [],  // P3.3.18 Phase 4
  lastLoadTs: 0,

  selectedPath: null,
  selectedFile: null,
  selectedLoading: false,
  selectedError: null,

  search: "",
  kindFilter: "all",
  query: "none",
  selectedTag: null,

  // P3.5.110: 默认 closed
  createModalState: { open: false },

  openCreateModal: (prefill) =>
    set({
      createModalState: {
        open: true,
        prefillTitle: prefill?.title,
        prefillKind: prefill?.kind,
      },
    }),
  closeCreateModal: () => set({ createModalState: { open: false } }),

  // P3.5.111: 虚拟体系初始 null
  virtualSystemName: null,
  setVirtualSystem: (name) => set({ virtualSystemName: name }),

  loadFiles: async () => {
    set({ filesLoading: true, filesError: null });
    try {
      // P3.3.18 Phase 4: 并发拉个人 wiki + 已装部门 wiki. 部门 wiki 失败不阻塞.
      const [filesRes, sharedRes] = await Promise.allSettled([
        wikiListFiles(),
        listInstalledWikiShared(),
      ]);
      const files =
        filesRes.status === "fulfilled" ? filesRes.value : [];
      const sharedFiles =
        sharedRes.status === "fulfilled" ? sharedRes.value : [];
      if (filesRes.status === "rejected") {
        set({
          filesLoading: false,
          filesError: String(filesRes.reason),
          sharedFiles,
        });
        return;
      }
      set({
        files,
        sharedFiles,
        filesLoading: false,
        lastLoadTs: Date.now(),
      });
    } catch (e) {
      set({ filesLoading: false, filesError: String(e) });
    }
  },

  selectFile: async (relPath: string | null) => {
    // P3.3.4 (6/9): 删 entity 后传 null 清 selection, list 回退到默认无选中
    //
    // P3.5.112 (6/25 鸿波 catch "点一次就不能点了") 修正 P3.5.111:
    // selectFile 不再 auto-clear virtualSystemName — 鸿波点虚拟体系后, 再
    // 点子项 (selectFile), 子图应该保留真该体系子树**, 子项只用于高亮 + preview**.
    // 清虚拟态靠: setVirtualSystem(null) 显式 / 点别的体系 header (覆盖) /
    // 强制全图 mode (隐含).
    if (relPath === null) {
      selectRequest += 1;
      set({
        selectedPath: null,
        selectedFile: null,
        selectedLoading: false,
        selectedError: null,
      });
      return;
    }
    const request = ++selectRequest;
    set({
      selectedPath: relPath,
      selectedLoading: true,
      selectedError: null,
    });
    try {
      const full = await wikiReadFile(relPath);
      if (request !== selectRequest) return;
      set({ selectedFile: full, selectedLoading: false });
    } catch (e) {
      if (request !== selectRequest) return;
      set({ selectedLoading: false, selectedError: String(e) });
    }
  },

  setSearch: (s) => set({ search: s }),
  setKindFilter: (k) => set({ kindFilter: k }),
  setQuery: (q) => set({ query: q, selectedTag: q === "top-tag" ? null : null }),
  setSelectedTag: (tag) => set({ selectedTag: tag, query: tag ? "top-tag" : "none" }),
}));
