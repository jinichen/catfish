/** 反向关系改记 (10/23): 「本条目 关联 对方」其实是「对方 持有 本条目」这种 —— 只有反过来才成立。
 *
 * 采纳 = 本条目上删掉这条, 对方条目上记「rel 本条目」。按条目分组, 每个文件只读写一次。
 * 对方已经有一条指向本条目的关系: 是「关联」就改成新类型, 已有具体类型就不动 (员工写的为准)。
 */
import { buildConfirmedWikiContent, RELATION_FALLBACK } from "../tabs/Wiki/wikiRelationshipTasks";
import { wikiReadFile, wikiUpdateFile, type RelatedRef, type WikiFileInfo } from "./tauri_wiki";
import { findEntryByName } from "./wikiRelationSuggest";

export interface ReverseMove {
  /** 本条目 (关系现在写在这里) */
  source: WikiFileInfo;
  /** 关系里写的对方名字 */
  name: string;
  rel: string;
}

export interface ReverseResult {
  /** 两边都改成了的 */
  moved: ReverseMove[];
  /** 没改成的条目 (路径: 原因) */
  failed: string[];
}

export async function applyReverseMoves(moves: readonly ReverseMove[], files: readonly WikiFileInfo[]): Promise<ReverseResult> {
  const failed: string[] = [];
  const removeBySource = new Map<string, Set<string>>();
  const addByTarget = new Map<string, RelatedRef[]>();
  for (const move of moves) {
    const target = findEntryByName(files, move.name);
    if (!target) {
      failed.push(`${move.source.rel_path}: 找不到「${move.name}」条目`);
      continue;
    }
    removeBySource.set(move.source.rel_path, (removeBySource.get(move.source.rel_path) ?? new Set()).add(move.name));
    addByTarget.set(target.rel_path, [...(addByTarget.get(target.rel_path) ?? []), { name: move.source.title, rel: move.rel }]);
  }
  // 先加到对方, 再从本条目删 —— 中途失败时宁可两边都有, 不丢关系
  const doneTargets = new Set<string>();
  for (const [relPath, adds] of addByTarget) {
    try {
      const full = await wikiReadFile(relPath);
      const next = [...full.info.related];
      for (const add of adds) {
        const at = next.findIndex((r) => r.source !== "body" && r.name.trim() === add.name);
        if (at < 0) next.push(add);
        else if ((next[at].rel ?? "").trim() === RELATION_FALLBACK || !next[at].rel) next[at] = { ...next[at], rel: add.rel };
      }
      await wikiUpdateFile(relPath, buildConfirmedWikiContent(full.content, next, full.info, { keepStatus: true }));
      doneTargets.add(relPath);
    } catch (err) {
      failed.push(`${relPath}: ${String(err)}`);
    }
  }
  const moved: ReverseMove[] = [];
  for (const [relPath, names] of removeBySource) {
    // 对方没写成的那几条不从本条目删
    const removable = new Set([...names].filter((name) => doneTargets.has(findEntryByName(files, name)!.rel_path)));
    if (removable.size === 0) continue;
    try {
      const full = await wikiReadFile(relPath);
      const next = full.info.related.filter((r) => r.source === "body" || !removable.has(r.name.trim()));
      await wikiUpdateFile(relPath, buildConfirmedWikiContent(full.content, next, full.info, { keepStatus: true }));
      moved.push(...moves.filter((m) => m.source.rel_path === relPath && removable.has(m.name)));
    } catch (err) {
      failed.push(`${relPath}: ${String(err)}`);
    }
  }
  return { moved, failed };
}
