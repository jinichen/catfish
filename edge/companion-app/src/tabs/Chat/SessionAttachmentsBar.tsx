/** 会话顶部「📎 本会话历史附件」(BL-FILE-SESSION-INDEX-V1 Phase 1, 5/30)。
 *
 *  切回老会话时列出这个会话上传过的附件 (~/.catfish/attachments.db), 跟 LLM 那边
 *  catfish_search_attachments 是同一张表 —— 界面看到的就是模型能搜到的。无附件不渲染。
 *
 *  10/16 鸿波: "点上传的文件, 能查看文件吗?" —— 原来只是标签, 点了没反应, 只能悬停看路径。
 *  现在点 = 用默认 App 打开 (图片走「预览」), ⌥/Alt+点 = 在文件管理器里显示。
 *  文件被挪走 / 没留副本 (keptPath 为空) 时就地说明, 不弹窗。
 *
 *  10/28 鸿波「文件数 +X 不能按?」: 超过 MAX_CHIPS 的原来只显示一行「+N 更多」文字, 点了没反应,
 *  后面那些文件在界面上根本打不开。现在点它展开全部, 再点收起。
 */
import { useState } from "react";

import { openFile, revealInFinder } from "../../lib/tauri";
import { fileManagerName } from "../../lib/platformLabels";
import { useChatStore, type SessionAttachment } from "../../store/chat";

const ICON_BY_KIND: Record<string, string> = { image: "🖼", file: "📄", audio: "🎙" };
const MAX_CHIPS = 12;

export default function SessionAttachmentsBar() {
  const sessionAttachments = useChatStore((s) => s.sessionAttachments);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  if (sessionAttachments.length === 0) return null;

  // 去重按 name (同一文件可能被 record 多次 — 比如同一附件在多轮提及)
  const byName = new Map<string, SessionAttachment>();
  for (const a of sessionAttachments) {
    if (!byName.has(a.name)) byName.set(a.name, a);
  }
  const all = Array.from(byName.values());
  const unique = showAll ? all : all.slice(0, MAX_CHIPS);
  const more = all.length - Math.min(all.length, MAX_CHIPS);

  async function open(a: SessionAttachment, reveal: boolean) {
    setErr(null);
    if (!a.keptPath) {
      setErr(`「${a.name}」当时没在本机留副本, 打不开`);
      return;
    }
    try {
      await (reveal ? revealInFinder(a.keptPath) : openFile(a.keptPath));
    } catch (e) {
      setErr(`「${a.name}」打不开: ${String(e).slice(0, 120)} (文件可能被移动或删除了)`);
    }
  }

  return (
    <div
      title={`本会话历史附件 — 点开查看, ⌥/Alt+点在${fileManagerName()}里显示`}
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 6,
        padding: "6px 12px",
        fontSize: 11,
        background: "var(--catfish-bg-elevated)",
        borderBottom: "1px solid var(--catfish-border)",
        color: "var(--catfish-text-muted)",
      }}
    >
      <span style={{ marginRight: 4 }}>📎 本会话历史附件:</span>
      {unique.map((a) => (
        <button
          type="button"
          key={a.id}
          onClick={(e) => void open(a, e.altKey)}
          disabled={!a.keptPath}
          title={
            `${a.name}${a.sizeBytes ? ` · ${(a.sizeBytes / 1024).toFixed(1)} KB` : ""}` +
            (a.keptPath ? `\n${a.keptPath}\n点开查看 · ⌥/Alt+点在${fileManagerName()}里显示` : "\n当时没在本机留副本")
          }
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            border: "none",
            borderRadius: 10,
            font: "inherit",
            background: "rgba(14, 158, 140, 0.12)",
            color: "var(--catfish-accent, #0e9e8c)",
            maxWidth: 220,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: a.keptPath ? "pointer" : "not-allowed",
            opacity: a.keptPath ? 1 : 0.55,
          }}
        >
          <span>{ICON_BY_KIND[a.kind] ?? "📎"}</span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{a.name}</span>
        </button>
      ))}
      {more > 0 && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          aria-expanded={showAll}
          style={{
            marginLeft: 4,
            padding: "2px 6px",
            border: "none",
            background: "transparent",
            font: "inherit",
            fontStyle: "italic",
            color: "var(--catfish-cyan)",
            cursor: "pointer",
          }}
        >
          {showAll ? "收起" : `+${more} 更多`}
        </button>
      )}
      {err && (
        <span role="status" style={{ flexBasis: "100%", color: "var(--status-err)" }}>
          {err}
        </span>
      )}
    </div>
  );
}
