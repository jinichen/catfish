/** 名字 → 条目 的唯一解析入口。
 *
 * # 为什么要抽出来 (8/4 鸿波 "怎么判定 ontology 的效果" 量出来的)
 *
 * 在此之前, 「一个 related 名字指向哪个条目」这件事有**四套互不相同的实现**:
 *
 *   WikiGraph.tsx  findTargetForFilter / findTarget  (两处几乎一样的拷贝)
 *   wikiRelevance.ts                                 (只按 title/slug 精确比)
 *   catfish_memory_helpers._check_dangling_related    (Python, 又一套)
 *   wiki_health.py                                    (体检脚本, 再一套)
 *
 * 于是同一条边, 图上连着、体检说它断了、蒸馏侧又是第三种看法。**口径分叉本身
 * 就是 bug**, 因为没有任何一处是"对"的——它们只是不同。
 *
 * # 子串兜底那一档更严重
 *
 * 老 findTarget 的第三档是:
 *
 *     files.find((f) => f.title.toLowerCase().includes(lower))
 *
 * 而 wiki_list_files 返回的数组是**按 mtime 倒序**排的。所以:
 *
 *   · 「中电福富」子串同时命中「中电福富信息科技有限公司」(org, 7/27)
 *     和「销售许可证-中电福富API与应用系统安全审计V2.0」(cert, 7/21)
 *   · find() 取第一个 = 取最近改过的那个
 *   · 8/4 当天 org 较新, 97 条边碰巧连对了
 *   · **谁去动一下那个证书文件, 97 条边当场全翻到证书上**, 无声无息
 *
 * 断链至少还看得出是"少了"; 连错是**理直气壮地错**, 员工没法发现。
 *
 * # 这里的规则
 *
 * 精确优先, 别名是写下来的事实, 子串只在**唯一命中**时才认。歧义 → 返回 null
 * 并报出候选, 让它变成一条能被看见的问题, 而不是一次沉默的抛硬币。
 */

import type { WikiFileInfo } from "./tauri";

export type ResolveOutcome =
  | { kind: "hit"; file: WikiFileInfo; how: "title" | "alias" | "slug" | "substring" }
  | { kind: "miss" }
  /** 子串命中多个 —— 不猜。candidates 给 UI/体检报出来。 */
  | { kind: "ambiguous"; candidates: WikiFileInfo[] };

const norm = (s: string) => s.toLowerCase().trim();

/** 去掉所有标点/空白后比 —— 只吸收「同一个名字写法不同」, 不做语义猜测。
 *
 * 实测断链里有一半是纯标点差异:
 *   related 写「CS4 信息系统建设及服务能力等级证书」(空格)
 *   title 是「CS4-信息系统建设及服务能力等级证书」(连字符)
 *   related 写「通信工程施工总承包二级」, title 是「通信工程施工总承包(二级)」
 *
 * 它**不会**把「中电福富」和「中电福富信息科技有限公司」合并 (字符本身就不同),
 * 那种必须靠 aliases 明写。
 */
const normTitle = (s: string) =>
  s.toLowerCase().replace(/[\s\-_.·、,，:：;；(){}（）[\]【】"'“”‘’/\\　]/g, "");

/** slug 变体归一: zhongdianfufu / zhongdian-fufu / zhong_dian_fu_fu 视为同一个。
 *  跟 Python _normalize_slug_for_dedup 同语义 (由 wiki_resolve_cases.json 对拍钉住)。 */
const normSlug = (s: string) =>
  s.toLowerCase().replace(/[\s\-_.　]/g, "");

/** 解析一个 related 名字。files 顺序**不影响结果** —— 这是跟老实现最大的区别。 */
export function resolveWikiRef(name: string, files: WikiFileInfo[]): ResolveOutcome {
  const n = norm(name);
  if (!n) return { kind: "miss" };

  // ① 精确 title
  const byTitle = files.filter((f) => norm(f.title) === n);
  if (byTitle.length) return { kind: "hit", file: byTitle[0], how: "title" };

  // ② 别名 —— 「这两个名字是同一个东西」是写下来的, 不是猜的
  const byAlias = files.filter((f) => (f.aliases || []).some((a) => norm(a) === n));
  if (byAlias.length === 1) return { kind: "hit", file: byAlias[0], how: "alias" };
  if (byAlias.length > 1) return { kind: "ambiguous", candidates: byAlias };

  // ③ 标点无关的 title —— 「CS4 信息系统…」vs「CS4-信息系统…」
  const byNormTitle = files.filter((f) => normTitle(f.title) === normTitle(name));
  if (byNormTitle.length === 1) return { kind: "hit", file: byNormTitle[0], how: "title" };
  if (byNormTitle.length > 1) return { kind: "ambiguous", candidates: byNormTitle };

  // ④ 精确 slug
  const bySlug = files.filter((f) => norm(f.slug) === n);
  if (bySlug.length) return { kind: "hit", file: bySlug[0], how: "slug" };

  // ④ slug 变体 (拼音分词不稳定造成的 zhongdianfufu / zhong-dian-fu-fu)
  const byNormSlug = files.filter((f) => normSlug(f.slug) === normSlug(name));
  if (byNormSlug.length === 1) return { kind: "hit", file: byNormSlug[0], how: "slug" };

  // ⑤ 子串 —— 只在唯一命中时才认。
  //
  // 保留这一档是因为它确实救回不少"全称 vs 简称"的边; 砍掉会让存量数据大面积
  // 断链。但**多个命中时绝不挑一个** —— 那正是 mtime 决定图长什么样的根源。
  const bySub = files.filter((f) => norm(f.title).includes(n));
  if (bySub.length === 1) return { kind: "hit", file: bySub[0], how: "substring" };
  if (bySub.length > 1) return { kind: "ambiguous", candidates: bySub };

  return { kind: "miss" };
}

/** 老调用点的便捷包装: 只要节点, 歧义按 miss 处理 (不猜)。 */
export function resolveWikiRefOrNull(
  name: string,
  files: WikiFileInfo[],
): WikiFileInfo | null {
  const r = resolveWikiRef(name, files);
  return r.kind === "hit" ? r.file : null;
}

/** 全库扫一遍, 给体检 / UI 用。歧义单独列, 因为它跟断链是两种病:
 *  断链是"少了", 歧义是"可能连错了"。 */
export function auditWikiRefs(files: WikiFileInfo[]): {
  total: number;
  hits: number;
  misses: { from: string; name: string }[];
  ambiguous: { from: string; name: string; candidates: string[] }[];
} {
  const misses: { from: string; name: string }[] = [];
  const ambiguous: { from: string; name: string; candidates: string[] }[] = [];
  let total = 0;
  let hits = 0;
  for (const f of files) {
    for (const r of f.related) {
      total++;
      const res = resolveWikiRef(r.name, files);
      if (res.kind === "hit") hits++;
      else if (res.kind === "miss") misses.push({ from: f.rel_path, name: r.name });
      else
        ambiguous.push({
          from: f.rel_path,
          name: r.name,
          candidates: res.candidates.map((c) => c.title),
        });
    }
  }
  return { total, hits, misses, ambiguous };
}
