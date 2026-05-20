/** ChatInput FileChip — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * 文件 / 音频 attachment 一条 chip 显示 (emoji + name + meta + 删除按钮).
 */

import type { Attachment } from "../../../types/chat";

import { fileEmoji, metaSummary } from "./inputHelpers";


function FileChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const summary = metaSummary(attachment);
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        padding: "6px 10px",
        background: "var(--catfish-bg)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        fontSize: 12,
        maxWidth: 320,
      }}
      title={`${attachment.name}\n${summary}\n完整文件: ${attachment.keptPath || "(未保留)"}`}
    >
      <span style={{ fontSize: 16 }}>{fileEmoji(attachment.name)}</span>
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 180,
        }}
      >
        {attachment.name}
      </span>
      <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
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
        }}
        title="删除"
      >
        ×
      </button>
    </div>
  );
}

export default FileChip;
