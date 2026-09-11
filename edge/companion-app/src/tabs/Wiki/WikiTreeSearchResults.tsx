/** WikiTree 的搜索结果视图 (P37, 6/5)。
 *
 * 8/15 从 WikiTree.tsx 搬出来。搜索态下它**整个替掉** group tree, 是一条独立
 * 的渲染分支, 跟分组那套没有共享状态 —— 所以单独一个文件。
 *
 * kindColor / kindEmoji 跟着走: 只有这里用。
 */

/** P37 (6/5 鸿波) — 搜索结果列表 (替 group tree).
 *
 * 8/14: 加了 mode。这个组件是**全文和语义共用**的, 而标题和空态原来都写死成
 * 全文的说法:
 *
 *   标题: "🔍 全文搜 — N 个结果"        ← 语义模式下条目上明明写着 semantic·0.7
 *   空态: "没匹配 (title / body / tags 都试过)"
 *
 * 空态那句在语义模式下是**双重误导**: 既没试过 title/body/tags, 而且当时
 * 根本不是"没匹配" —— 是 provider 没就绪。鸿波今晚看到的就是这一句 +
 * 上面那段"未就绪", 两条凑在一起把人往"本地模型坏了"引。
 */
import { wikiKindLabel } from "./wikiLabels";

export function SearchResults({
  hits,
  searching,
  mode,
  selectedPath,
  onSelect,
}: {
  hits: import("../../lib/tauri").WikiSearchHit[];
  searching: boolean;
  mode: "title" | "body" | "semantic" | "hybrid";
  selectedPath: string | null;
  onSelect: (relPath: string) => Promise<void>;
}) {
  const isMeaningSearch = mode === "semantic" || mode === "hybrid";
  if (searching && hits.length === 0) {
    return <div className="wiki-search-results__status">正在搜索…</div>;
  }
  if (hits.length === 0) {
    return (
      <div className="wiki-search-results__status">
        {isMeaningSearch ? "没有找到含义相近的知识" : "没有找到匹配的知识"}
      </div>
    );
  }
  return (
    <div className="wiki-search-results">
      <div className="wiki-search-results__summary">找到 {hits.length} 条知识</div>
      <ul>
        {hits.map((h) => (
          <li key={h.rel_path}>
            <button
              type="button"
              onClick={() => void onSelect(h.rel_path)}
              data-active={selectedPath === h.rel_path}
            >
              <span className={`wiki-kind-badge wiki-kind-badge--${h.kind}`}>
                {wikiKindLabel(h.kind)}
              </span>
              <strong>{h.title}</strong>
              <small>匹配：{h.matched_in.map(matchedInLabel).join("、")}</small>
              <span className="wiki-search-results__snippet">{h.snippet}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function matchedInLabel(value: string): string {
  if (value === "title") return "标题";
  if (value === "body") return "正文";
  if (value === "tags") return "标签";
  if (value === "semantic") return "含义";
  if (value === "graph-1hop") return "关联实体";
  return value;
}
