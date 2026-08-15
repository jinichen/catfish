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
export function SearchResults({
  hits,
  searching,
  mode,
  selectedPath,
  onSelect,
}: {
  hits: import("../../lib/tauri").WikiSearchHit[];
  searching: boolean;
  mode: "title" | "body" | "semantic";
  selectedPath: string | null;
  onSelect: (relPath: string) => Promise<void>;
}) {
  const isSemantic = mode === "semantic";
  if (searching && hits.length === 0) {
    return (
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, padding: 8 }}>
        🔍 搜索中…
      </div>
    );
  }
  if (hits.length === 0) {
    return (
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, padding: 8 }}>
        {isSemantic
          ? "语义检索没找到相近的条目"
          : "没匹配 (title / body / tags 都试过)"}
      </div>
    );
  }
  return (
    <div style={{ marginTop: "var(--space-2)" }}>
      <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
        {isSemantic ? "🧭 语义检索" : "🔍 全文搜"} — {hits.length} 个结果
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {hits.map((h) => (
          <li
            key={h.rel_path}
            onClick={() => void onSelect(h.rel_path)}
            style={{
              padding: "6px 8px",
              marginBottom: 4,
              cursor: "pointer",
              borderRadius: 4,
              fontSize: 11,
              background:
                selectedPath === h.rel_path ? "var(--catfish-bg-elevated, rgba(0,0,0,0.05))" : "transparent",
              borderLeft: `2px solid ${kindColor(h.kind)}`,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 2 }}>
              {kindEmoji(h.kind)} {h.title}
              <span style={{ marginLeft: 6, fontSize: 9, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
                {h.matched_in.join(" · ")} · {h.score.toFixed(1)}
              </span>
            </div>
            <div
              style={{
                color: "var(--catfish-text-muted)",
                fontSize: 10,
                lineHeight: 1.4,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {h.snippet}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function kindColor(k: string): string {
  return k === "entity" ? "#4a9eff" : k === "concept" ? "#ff9933" : "#5fc878";
}
function kindEmoji(k: string): string {
  return k === "entity" ? "🧑" : k === "concept" ? "📐" : "💬";
}
