/**
 * 关系类型的前端缓存 (10/23): 内置词表 + 员工登记的自定义词。
 *
 * 下拉、批量建议、小鲶建议都从这里拿自定义词 —— 登记 / 删除后所有用到的地方一起更新。
 * Tauri 调不通 (浏览器预览 / 测试) 时自定义为空, 内置词表照常 (前端本来就有一份内置词)。
 */
import { create } from "zustand";
import { wikiRelationVocabAdd, wikiRelationVocabList, wikiRelationVocabRemove, type RelationTypeInfo } from "./tauri_wiki";

interface RelationVocabState {
  builtin: RelationTypeInfo[];
  custom: RelationTypeInfo[];
  loaded: boolean;
  load: () => Promise<void>;
  add: (name: string, meaning: string, hierarchy?: "child" | "parent") => Promise<void>;
  remove: (name: string) => Promise<void>;
}

export const useRelationVocab = create<RelationVocabState>((set) => ({
  builtin: [],
  custom: [],
  loaded: false,
  load: async () => {
    try {
      const v = await wikiRelationVocabList();
      set({ builtin: v.builtin, custom: v.custom, loaded: true });
    } catch {
      set({ loaded: true });
    }
  },
  add: async (name, meaning, hierarchy) => {
    const v = await wikiRelationVocabAdd(name, meaning, hierarchy);
    set({ builtin: v.builtin, custom: v.custom });
  },
  remove: async (name) => {
    const v = await wikiRelationVocabRemove(name);
    set({ builtin: v.builtin, custom: v.custom });
  },
}));

/** 自定义关系里的上下级关系 (词 → child/parent), 给「层级」视图用 */
export function customHierarchy(custom: readonly RelationTypeInfo[]): Record<string, "child" | "parent"> {
  return Object.fromEntries(custom.filter((c) => c.hierarchy).map((c) => [c.name, c.hierarchy!]));
}
