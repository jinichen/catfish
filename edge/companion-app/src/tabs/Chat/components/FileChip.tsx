/** ChatInput FileChip — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * 文件 / 音频 attachment 一条 chip 显示 (emoji + name + meta + 删除按钮).
 */

import type { Attachment } from "../../../types/chat";

import { fileEmoji, metaSummary } from "./inputHelpers";


/** compact (10/27): 附件区展开后的列表里用 —— 单行、撑满格子宽度, 好几列排开 (见 AttachmentTray)。 */
function FileChip({
  attachment,
  onRemove,
  compact = false,
}: {
  attachment: Attachment;
  onRemove: () => void;
  compact?: boolean;
}) {
  const summary = metaSummary(attachment);
  return (
    <div
      style={{
        display: compact ? "flex" : "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        padding: compact ? "3px 6px 3px 8px" : "6px 10px",
        background: "var(--catfish-bg)",
        border: "1px solid var(--catfish-border)",
        borderRadius: compact ? "var(--radius-sm)" : "var(--radius-md)",
        fontSize: 12,
        maxWidth: compact ? undefined : 320,
        minWidth: 0,
      }}
      title={attachment.fileKind === "wechat"
        ? `${attachment.name}\n${summary}\n已导入本机 (隐私 → 微信接入 可删除)`
        : `${attachment.name}\n${summary}\n完整文件: ${attachment.keptPath || "(未保留)"}`}
    >
      <span style={{ fontSize: compact ? 13 : 16, flexShrink: 0 }}>
        {attachment.fileKind === "wechat" ? "💬" : fileEmoji(attachment.name)}
      </span>
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: compact ? undefined : 180,
          flex: compact ? 1 : undefined,
          minWidth: 0,
        }}
      >
        {attachment.name}
      </span>
      {/* 10/27: 「1 页 · 57 KB」原来会被挤成两行, 把卡片撑高 */}
      <span style={{ color: "var(--catfish-text-muted)", fontSize: 11, whiteSpace: "nowrap", flexShrink: 0 }}>
        {summary}
      </span>
      <button
        onClick={onRemove}
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          border: "none",
          background: "rgba(0,0,0,0.4)",
          color: "white",
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          marginLeft: "var(--space-1)",
          lineHeight: 1,
          flexShrink: 0,
        }}
        title="删除"
      >
        ×
      </button>
    </div>
  );
}

export default FileChip;
