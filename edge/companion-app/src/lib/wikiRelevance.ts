/** BL-CATFISH-WIKI-MODE P3.2 (6/4) — 4 信号 wiki entity/concept relevance.
 *
 * 借鉴 llm_wiki + 学术 community-detection 算法:
 *   - direct link (×3): A.related 含 [[B]] (1-hop 直接引用)
 *   - source overlap (×4): Jaccard(A.sources, B.sources) — 共 source 信号强
 *   - Adamic-Adar (×1.5): 共同邻居 weighted (1/log(degree)) — 低度共邻信号更强
 *   - type affinity (×1): 同 kind / subtype bonus
 *
 * 用真 WikiPreview 底部 📊 相关推荐 section — 类 Obsidian backlinks panel,
 * 但 weighted ranking top-K 排序, 比 frontmatter related 更智能.
 *
 * Pure JS — 不依赖 graphology metrics (它 没**真 built-in Adamic-Adar**).
 */

import type { WikiFileInfo } from "./tauri";

/** build name → file lookup. name 真 title / slug / lowercase 匹配. */
function findFileByName(name: string, files: WikiFileInfo[]): WikiFileInfo | null {
  const lower = name.toLowerCase().trim();
  return (
    files.find((f) => f.title.toLowerCase() === lower) ||
    files.find((f) => f.slug.toLowerCase() === lower) ||
    files.find((f) => f.title.toLowerCase().includes(lower)) ||
    null
  );
}

/** build neighbor map: rel_path → Set<neighbor rel_path>. undirected. */
export function buildNeighborMap(files: WikiFileInfo[]): Map<string, Set<string>> {
  const m = new Map<string, Set<string>>();
  for (const f of files) {
    if (!m.has(f.rel_path)) m.set(f.rel_path, new Set());
  }
  for (const f of files) {
    for (const r of f.related) {
      // P3.5.132 #5: r 真 RelatedRef
      const target = findFileByName(r.name, files);
      if (!target || target.rel_path === f.rel_path) continue;
      m.get(f.rel_path)?.add(target.rel_path);
      m.get(target.rel_path)?.add(f.rel_path); // undirected
    }
  }
  return m;
}

/** Jaccard |a ∩ b| / |a ∪ b|. 真空返 0**. */
function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  const union = a.size + b.size - inter;
  return union > 0 ? inter / union : 0;
}

export interface RelevanceBreakdown {
  total: number;
  direct: number;
  sourceOverlap: number;
  adamicAdar: number;
  typeAffinity: number;
}

/** 4 信号 weighted relevance score A → B (undirected). */
export function computeRelevance(
  a: WikiFileInfo,
  b: WikiFileInfo,
  files: WikiFileInfo[],
  neighborMap?: Map<string, Set<string>>
): RelevanceBreakdown {
  if (a.rel_path === b.rel_path) {
    return { total: 0, direct: 0, sourceOverlap: 0, adamicAdar: 0, typeAffinity: 0 };
  }

  // 1. direct link (×3): A.related 含 B 真 title/slug 或 反向
  // P3.5.132 #5: r 真 RelatedRef
  const aRelatedNames = new Set(a.related.map((r) => r.name.toLowerCase().trim()));
  const bRelatedNames = new Set(b.related.map((r) => r.name.toLowerCase().trim()));
  const bIdentifiers = [b.title.toLowerCase(), b.slug.toLowerCase()];
  const aIdentifiers = [a.title.toLowerCase(), a.slug.toLowerCase()];
  let direct = 0;
  if (bIdentifiers.some((id) => aRelatedNames.has(id))) direct = 1;
  if (aIdentifiers.some((id) => bRelatedNames.has(id))) direct = 1;
  direct *= 3.0;

  // 2. source overlap (×4): Jaccard(A.sources, B.sources)
  const aSrc = new Set(a.sources);
  const bSrc = new Set(b.sources);
  const sourceOverlap = jaccard(aSrc, bSrc) * 4.0;

  // 3. Adamic-Adar (×1.5): Σ 1/log(degree(common_neighbor)) over 真共邻
  let adamicAdar = 0;
  const nbMap = neighborMap || buildNeighborMap(files);
  const aNeighbors = nbMap.get(a.rel_path) || new Set();
  const bNeighbors = nbMap.get(b.rel_path) || new Set();
  for (const n of aNeighbors) {
    if (bNeighbors.has(n)) {
      const deg = (nbMap.get(n) || new Set()).size;
      // log(1) = 0, avoid divide by zero
      if (deg >= 2) {
        adamicAdar += 1 / Math.log(deg);
      } else {
        adamicAdar += 1; // degree 1 真罕见**`hub-like` count as 1
      }
    }
  }
  adamicAdar *= 1.5;

  // 4. type affinity (×1): kind + subtype 双 bonus
  let typeAffinity = 0;
  if (a.kind === b.kind) typeAffinity += 0.5;
  if (a.subtype && a.subtype === b.subtype) typeAffinity += 0.5;
  typeAffinity *= 1.0;

  const total = direct + sourceOverlap + adamicAdar + typeAffinity;
  return { total, direct, sourceOverlap, adamicAdar, typeAffinity };
}

/** 取 top-K 相关 真 file. exclude self. */
export function topKRelated(
  target: WikiFileInfo,
  files: WikiFileInfo[],
  k = 5
): Array<{ file: WikiFileInfo; breakdown: RelevanceBreakdown }> {
  const neighborMap = buildNeighborMap(files);
  const scored = files
    .filter((f) => f.rel_path !== target.rel_path)
    .map((f) => ({ file: f, breakdown: computeRelevance(target, f, files, neighborMap) }))
    .filter((x) => x.breakdown.total > 0)
    .sort((a, b) => b.breakdown.total - a.breakdown.total)
    .slice(0, k);
  return scored;
}
