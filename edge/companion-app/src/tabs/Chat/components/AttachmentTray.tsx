/** 输入框上方的附件区 (10/27 鸿波「一次性上传很多文件, 这种方式感觉不好」)。
 *
 * 原来每个附件一张卡片铺开: 一次拖 30 张发票, 卡片把整个对话区占满, 上一条回答都看不见了。
 * 现在少的 (≤ COMPACT_AT 个) 照旧铺开; 多了只显示一行汇总 (几个文件 / 什么类型 / 多大),
 * 点「展开」才在一个限高、可滚动的框里列出来, 逐个删; 「全部移除」一键清掉。
 */

import { useState } from "react";

import type { Attachment } from "../../../types/chat";

import FileChip from "./FileChip";
import ThumbCard from "./ThumbCard";

/** 超过这么多个就收成一行汇总 */
export const COMPACT_AT = 4;

const KIND_LABEL: Record<string, string> = {
  pdf: "PDF",
  excel: "表格",
  csv: "表格",
  word: "Word",
  text: "文本",
  audio: "音频",
  wechat: "聊天记录",
};

/** 「28 份 PDF · 2 张图片」—— 按数量从多到少 */
export function kindBreakdown(attachments: Attachment[]): string {
  const counts = new Map<string, number>();
  for (const a of attachments) {
    const label = a.kind === "image" ? "图片" : KIND_LABEL[a.fileKind ?? ""] ?? "文件";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((x, y) => y[1] - x[1])
    .map(([label, n]) => `${n} ${label === "图片" ? "张" : "份"}${label}`)
    .join(" · ");
}

export function totalSizeLabel(attachments: Attachment[]): string {
  const bytes = attachments.reduce((sum, a) => sum + (a.sizeBytes || 0), 0);
  const kb = Math.max(1, Math.round(bytes / 1024));
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;
}

const linkButton = {
  border: "none",
  background: "transparent",
  color: "var(--catfish-cyan)",
  cursor: "pointer",
  fontSize: 12,
  padding: "2px 4px",
} as const;

export default function AttachmentTray({
  attachments,
  onRemove,
  onClear,
}: {
  attachments: Attachment[];
  onRemove: (idx: number) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (attachments.length === 0) return null;

  const items = attachments.map((att, idx) =>
    att.kind === "image" ? (
      <ThumbCard key={idx} attachment={att} onRemove={() => onRemove(idx)} />
    ) : (
      <FileChip key={idx} attachment={att} onRemove={() => onRemove(idx)} />
    ),
  );
  const row = { display: "flex", gap: "var(--space-2)", flexWrap: "wrap" } as const;

  if (attachments.length <= COMPACT_AT) {
    return <div style={{ ...row, marginBottom: "var(--space-2)" }}>{items}</div>;
  }

  return (
    <div
      style={{
        marginBottom: "var(--space-2)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--catfish-bg)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          padding: "6px 10px",
          fontSize: 12,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 14 }}>📎</span>
        <span style={{ color: "var(--catfish-text)" }}>{attachments.length} 个文件</span>
        <span style={{ color: "var(--catfish-text-muted)", flex: 1, minWidth: 0 }}>
          {kindBreakdown(attachments)} · 共 {totalSizeLabel(attachments)}
        </span>
        <button type="button" style={linkButton} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "收起 ▴" : "展开 ▾"}
        </button>
        <button type="button" style={{ ...linkButton, color: "var(--catfish-text-muted)" }} onClick={onClear}>
          全部移除
        </button>
      </div>
      {open && (
        // 单行小条按宽度排几列 (10/27 鸿波「是不是空间太浪费」: 原来一个文件一整行、只占左半边)
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: 4,
            maxHeight: 168,
            overflowY: "auto",
            padding: "var(--space-2)",
            borderTop: "1px solid var(--catfish-border)",
          }}
        >
          {attachments.map((att, idx) =>
            att.kind === "image" ? (
              <ThumbCard key={idx} attachment={att} onRemove={() => onRemove(idx)} />
            ) : (
              <FileChip key={idx} attachment={att} onRemove={() => onRemove(idx)} compact />
            ),
          )}
        </div>
      )}
    </div>
  );
}
