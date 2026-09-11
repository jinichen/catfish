/** WikiPreview 里三段互相独立的展示 section。
 *
 * 8/15 从 WikiPreview.tsx 搬出来 (1096 行 → 目标 800 以下, CLAUDE.md §1)。
 *
 * # 挑这三段的理由: props 少
 *
 * WikiPreview 是**一个** 1001 行的组件, 里面的 state 高度交织。从 JSX 里往外
 * 抠东西, 真正的成本不是行数, 是**要往下传多少 props** —— props 一多, 拆出来
 * 的东西就不是"一个组件", 而是"原组件的一半 state 换了个地方写"。
 *
 * 逐块量过之后:
 *
 *     hubStale banner   32 行 →  1 个 props   ← 拆
 *     变更历史          29 行 →  4 个 props   ← 拆
 *     metadata header   71 行 →  5 个 props   ← 拆
 *     ─────────────────────────────────────
 *     action bar       119 行 → 18 个 props   ← **没拆**
 *
 * action bar 那 18 个 props 基本就是组件一半的 state (editing / saving /
 * deleting / sharing / uninstalling / confirmDelete / confirmUninstall + 7 个
 * handler)。它是整个编辑-删除-分享-卸载流程的控制面, 搬出去只是把耦合从
 * "同一个函数里的变量" 换成 "18 行 props 声明", 读起来不会更清楚。
 * 分享 dialog 拆得动是因为它是个 modal, 有天然边界; action bar 没有。
 *
 * # 这次同样是纯搬运
 *
 * JSX 一行没改, 只整体减了 4 格缩进。这个文件**零测试覆盖**, 所以不顺手
 * 优化任何东西 —— 改了就说不清是哪一步动的。
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { WikiFileInfo } from "../../lib/tauri";
import { wikiSourceLabel, wikiSubtypeLabel } from "./wikiLabels";

/** 部门 wiki 已被原作者撤回的警告 banner (P3.3.18 Phase 4 P2, 6/10)。
 *
 * 选中 wiki-shared/ 下的文件时后台会 fetch hub 查 stale_after_unpublish。
 * 这个 banner 是员工唯一能知道"手里这份已经被撤了"的地方 —— 别默默去掉。
 */
export function WikiHubStaleBanner({
  hubStaleInfo,
}: {
  hubStaleInfo: {
    stale: boolean;
    unpublished_at: string | null;
    unpublished_reason: string | null;
  } | null;
}) {
  if (!hubStaleInfo?.stale) return null;
  return (
    <div
      style={{
        background: "#fef3c7",
        border: "1px solid #f59e0b",
        color: "#78350f",
        padding: "10px 12px",
        borderRadius: 6,
        fontSize: 13,
        lineHeight: 1.5,
        marginBottom: 10,
      }}
    >
      <strong>⚠ 原作者已撤回这条 wiki</strong>
      {hubStaleInfo.unpublished_at && (
        <span style={{ marginLeft: 6, fontSize: 12, opacity: 0.8 }}>
          ({hubStaleInfo.unpublished_at.slice(0, 10)})
        </span>
      )}
      <div style={{ marginTop: 4 }}>
        中央 hub body 已清零, 但你本机这份副本不动 (manifesto 公理 4 — 中央不强制清你本机).
        自己决定是否点 "🗑 卸载本机副本".
      </div>
      {hubStaleInfo.unpublished_reason && (
        <div style={{ marginTop: 4, fontStyle: "italic" }}>
          原作者撤回原因: {hubStaleInfo.unpublished_reason}
        </div>
      )}
    </div>
  );
}

/** 变更历史 collapsible (P39, 6/5)。
 *
 * P19 的 LLM merge 会自动在正文末尾生成 `## 变更历史` 段, 这里把它折起来。
 * 编辑态不显 (editing 时正文是 textarea, 历史段还在 draftBody 里)。
 */
export function WikiHistorySection({
  editing,
  historyBody,
  showHistory,
  setShowHistory,
}: {
  editing: boolean;
  historyBody: string;
  showHistory: boolean;
  setShowHistory: (v: boolean) => void;
}) {
  if (editing || !historyBody) return null;
  return (
    <div
      className={
        "wiki-preview__history" +
        (showHistory ? " wiki-preview__history--open" : "")
      }
    >
      <button
        className="wiki-preview__history-toggle"
        onClick={() => setShowHistory(!showHistory)}
        aria-expanded={showHistory}
      >
        <span className="wiki-preview__history-caret">▶</span>
        变更历史
        <span className="wiki-preview__history-hint">
          (LLM merge 自动记录)
        </span>
      </button>
      {showHistory && (
        <div className="wiki-preview__history-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {historyBody}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}

/** 顶部 metadata header —— kind badge / 标题 / 路径 / 大小 / tags / related。
 *
 * related 里的每一项会走 isDangling 判一下"这个名字有没有对应文件", 没有的
 * 显成 dangling 样式, 点了由 handleWikilinkClick 去真建文件 (P3.5.114)。
 */
export function WikiMetaHeader({
  info,
  kind,
  kindLabel,
  isDangling,
  handleWikilinkClick,
}: {
  info: WikiFileInfo;
  kind: string;
  kindLabel: string;
  isDangling: (name: string) => boolean;
  handleWikilinkClick: (name: string) => void;
}) {
  const subtypeLabel = wikiSubtypeLabel(info.subtype, info.kind);
  return (
  <div className="wiki-preview__meta">
    <div className="wiki-preview__meta-kicker">
      <span className={`wiki-kind-badge wiki-kind-badge--${kind}`}>{kindLabel}</span>
      <span>{subtypeLabel}</span>
    </div>
    <div className="wiki-preview__meta-title">
      <span className="wiki-preview__title-text">{info.title}</span>
    </div>
    <div className="wiki-preview__meta-facts" aria-label="知识摘要">
      <span>已确认知识</span>
      <span>关系 <strong>{info.related.length}</strong></span>
      <span>来源 <strong>{info.sources.length}</strong></span>
        {info.tags.map((t) => (
          <span key={t} className="wiki-preview__tag">
            #{t}
          </span>
        ))}
    </div>
    {info.related.length > 0 && (
      <details className="wiki-preview__relation-details">
        <summary>
          已确认关系 ({info.related.length})
        </summary>
        <div className="wiki-preview__relations">
        {info.related.map((r, i) => {
          const dangling = isDangling(r.name);
          const title = r.rel
            ? `${dangling ? "暂未找到对应条目" : "查看 " + r.name} · 关系：${r.rel}`
            : (dangling ? "暂未找到对应条目" : "查看 " + r.name);
          return (
            <button
              key={i}
              className={
                "wiki-preview__wikilink" +
                (dangling ? " wiki-preview__wikilink--dangling" : "")
              }
              aria-label={title}
              onClick={() => handleWikilinkClick(r.name)}
              title={title}
            >
              <span className="wiki-preview__relation-dot" aria-hidden="true" />
              <span>{r.name}</span>
              {r.rel && (
                <span className="wiki-preview__relation-label">{r.rel}</span>
              )}
            </button>
          );
        })}
        </div>
      </details>
    )}
    <details className="wiki-preview__technical">
      <summary>文件信息</summary>
      <div className="wiki-preview__meta-row">
        <span>大小 {(info.size_bytes / 1024).toFixed(1)} KB</span>
        <span>内部路径 <code>{info.rel_path}</code></span>
      </div>
      {info.sources.length > 0 && (
        <div className="wiki-preview__technical-sources">
          来源：{info.sources.map(wikiSourceLabel).join("、")}
        </div>
      )}
    </details>
  </div>
  );
}
