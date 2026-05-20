/** ChatInput ThumbCard — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * 图片 attachment 缩略图 (base64 dataURI 渲染) + 删除按钮.
 */

import type { Attachment } from "../../../types/chat";


function ThumbCard({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const dataUri = `data:${attachment.mimeType};base64,${attachment.base64}`;
  const sizeKb = Math.max(1, Math.round(attachment.sizeBytes / 1024));
  return (
    <div
      style={{
        position: "relative",
        width: 64,
        height: 64,
        borderRadius: "var(--radius-sm)",
        overflow: "hidden",
        border: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg)",
      }}
      title={`${attachment.name} · ${sizeKb} KB`}
    >
      <img
        src={dataUri}
        alt={attachment.name}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          display: "block",
        }}
      />
      <button
        onClick={onRemove}
        style={{
          position: "absolute",
          top: 2,
          right: 2,
          width: 18,
          height: 18,
          borderRadius: "50%",
          border: "none",
          background: "rgba(0,0,0,0.6)",
          color: "white",
          fontSize: 11,
          fontWeight: 700,
          cursor: "pointer",
          lineHeight: 1,
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
        title="删除"
      >
        ×
      </button>
    </div>
  );
}


// ── BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行") ────────────
//
// 教学模式 toggle 按钮. 开启 → 这个 session 的 LLM 请求带
// X-Catfish-Teaching-Mode: 1 header → gateway 走 LEAN (关 9 个 inject + L8
// feedback retry, mid_task retry 不影响). 关闭 → 完整注入回常态.
// 状态走 zustand store + localStorage 持久化, 跨 session 隔离.

export default ThumbCard;
