/**
 * 层级 (10/23): 由「层级型」关系拼出来的树 —— 不限于 公司→部门→人员。
 *
 * 一条关系算不算层级, 看关系类型, 不看条目是什么:
 *   · 隶属 = 本条目是对方的下级 ("child"); 包含 = 本条目是对方的上级 ("parent")
 *     (edge/contracts/wiki_relation_vocab.json 的 hierarchy 字段)
 *   · 员工登记自定义关系时可以选它是不是层级、哪个方向 (wiki_relation_vocab.rs)
 * 所以 证书族 (CCRC → 子证书)、规则 (评分规则 → 子规则)、项目 (招投标 → 集采 → 标包) 都是层级,
 * 以后新加的也自动出现。
 *
 * 顶层 = 有下级、自己没有上级的条目。
 * 质量检查 (进关系整理): 跨级重复 (已在部门下, 又直挂公司) / 多个上级 (挂在两个不相干的上级下)。
 */
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import { resolveWikiRefOrNull } from "../../lib/wikiResolve";

export type HierarchyDirection = "child" | "parent";

/** 内置的层级型关系 (跟 contracts hierarchy 字段一致, 测试钉住) */
export const BUILTIN_HIERARCHY: Readonly<Record<string, HierarchyDirection>> = { 隶属: "child", 包含: "parent" };

export interface HierarchyLink {
  parent: string; // rel_path
  child: string;
  /** 这条关系写在哪个条目上、写的什么名字 —— 改 / 删时要用 */
  declaredOn: string;
  name: string;
  rel: string;
}

export interface HierarchyModel {
  children: Map<string, string[]>;
  parents: Map<string, string[]>;
  links: HierarchyLink[];
  roots: { path: string; size: number }[];
  byPath: Map<string, WikiFileInfo>;
}

export function buildHierarchy(
  files: WikiFileInfo[],
  custom: Readonly<Record<string, HierarchyDirection>> = {},
): HierarchyModel {
  const kinds: Record<string, HierarchyDirection> = { ...BUILTIN_HIERARCHY, ...custom };
  const byPath = new Map(files.map((f) => [f.rel_path, f]));
  const children = new Map<string, string[]>();
  const parents = new Map<string, string[]>();
  const links: HierarchyLink[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    for (const related of file.related) {
      if (related.source === "body") continue;
      const dir = kinds[related.rel?.trim() ?? ""];
      if (!dir) continue;
      const target = resolveWikiRefOrNull(related.name, files);
      if (!target || target.rel_path === file.rel_path) continue;
      const [parent, child] = dir === "child" ? [target.rel_path, file.rel_path] : [file.rel_path, target.rel_path];
      const key = `${parent}→${child}`;
      if (seen.has(key)) continue;
      seen.add(key);
      links.push({ parent, child, declaredOn: file.rel_path, name: related.name, rel: related.rel ?? "" });
      children.set(parent, [...(children.get(parent) ?? []), child]);
      parents.set(child, [...(parents.get(child) ?? []), parent]);
    }
  }
  const size = (path: string, stack = new Set<string>()): number => {
    if (stack.has(path)) return 0; // 环: 不再往下数
    stack.add(path);
    const n = (children.get(path) ?? []).reduce((acc, c) => acc + 1 + size(c, stack), 0);
    stack.delete(path);
    return n;
  };
  const roots = [...children.keys()]
    .filter((p) => !(parents.get(p)?.length))
    .map((path) => ({ path, size: size(path) }))
    .sort((a, b) => b.size - a.size || (byPath.get(a.path)?.title ?? "").localeCompare(byPath.get(b.path)?.title ?? "", "zh-CN"));
  return { children, parents, links, roots, byPath };
}

export interface HierarchyIssue {
  kind: "skiplevel" | "multiparent";
  child: string;
  /** skiplevel: 多余的那条 (直挂祖先的); multiparent: 全部上级 */
  links: HierarchyLink[];
  via?: string; // skiplevel: 中间那一级
}

/** 跨级重复: child 已经挂在 p 下、p 又挂在 g 下, child 还直挂 g —— 直挂 g 那条多余。
 *  多个上级: 去掉跨级重复之后仍有 ≥2 个上级。 */
export function hierarchyIssues(model: HierarchyModel): HierarchyIssue[] {
  const out: HierarchyIssue[] = [];
  for (const [child, ps] of model.parents) {
    const redundant = new Map<string, string>(); // 祖先 → 经由哪一级
    for (const p of ps) {
      for (const g of model.parents.get(p) ?? []) {
        if (ps.includes(g)) redundant.set(g, p);
      }
    }
    for (const [g, via] of redundant) {
      const link = model.links.find((l) => l.parent === g && l.child === child);
      if (link) out.push({ kind: "skiplevel", child, links: [link], via });
    }
    const rest = ps.filter((p) => !redundant.has(p));
    if (rest.length > 1) {
      out.push({ kind: "multiparent", child, links: model.links.filter((l) => l.child === child && rest.includes(l.parent)) });
    }
  }
  return out;
}
