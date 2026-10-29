/**
 * 10/29: 写邮件的「附件」一行 —— 鸿波「新建缺少增加附件的能力」(原来只能把文件路径贴进正文)。
 *
 * - 「📎 添加附件」开原生多选文件框 (Rust email_pick_attachments, 拿真实路径和大小)
 * - 改草稿时, 旧草稿已有的附件显示为「原有」并默认保留 —— 旧草稿存完新的就去掉了, 不带过去附件就丢;
 *   员工可以点 × 不要某个
 * - 合计超过 25 MB 当场提醒 (多数邮箱会退信; 命令行那边也会拦)
 */
import { useState } from "react";

import { emailPickAttachments, type PickedAttachment } from "../../../lib/tauri";

export interface KeptAttachment {
  filename: string;
  size_bytes: number;
}

/** 跟 catfish-email CLI 的 MAX_ATTACHMENTS_BYTES 一致 */
export const MAX_ATTACHMENTS_BYTES = 25 * 1024 * 1024;

export function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** 新加的文件按路径去重 (同一个文件选两次只算一个) */
export function mergePicked(current: PickedAttachment[], picked: PickedAttachment[]): PickedAttachment[] {
  const seen = new Set(current.map((a) => a.path));
  return [...current, ...picked.filter((a) => !seen.has(a.path) && seen.add(a.path))];
}

const chipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  maxWidth: 320,
  padding: "3px 8px",
  borderRadius: 4,
  border: "1px solid var(--catfish-border)",
  background: "var(--catfish-bg-elevated)",
  fontSize: 12,
};

export default function ComposeAttachments({
  added,
  kept,
  onChangeAdded,
  onChangeKept,
  disabled,
}: {
  added: PickedAttachment[];
  kept: KeptAttachment[];
  onChangeAdded: (next: PickedAttachment[]) => void;
  onChangeKept: (next: KeptAttachment[]) => void;
  disabled?: boolean;
}) {
  const [pickError, setPickError] = useState<string | null>(null);
  const total = added.reduce((s, a) => s + a.size, 0) + kept.reduce((s, a) => s + (a.size_bytes || 0), 0);
  const tooBig = total > MAX_ATTACHMENTS_BYTES;

  const pick = async () => {
    setPickError(null);
    try {
      const picked = await emailPickAttachments();
      if (picked.length) onChangeAdded(mergePicked(added, picked));
    } catch (e) {
      setPickError(e instanceof Error ? e.message : String(e));
    }
  };

  const chip = (key: string, label: string, size: number, note: string | null, onRemove: () => void) => (
    <span key={key} style={chipStyle} title={label}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>📎 {label}</span>
      <span style={{ color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>
        {fmtSize(size)}{note ? ` · ${note}` : ""}
      </span>
      <button
        type="button"
        aria-label={`移除附件 ${label}`}
        onClick={onRemove}
        disabled={disabled}
        style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, color: "var(--catfish-text-muted)" }}
      >
        ×
      </button>
    </span>
  );

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
      <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto", lineHeight: "26px" }}>附件</span>
      <div style={{ flex: 1, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          onClick={() => void pick()}
          disabled={disabled}
          style={{
            border: "1px solid var(--catfish-border)",
            background: "var(--catfish-bg)",
            borderRadius: 4,
            padding: "4px 10px",
            fontSize: 12,
            cursor: disabled ? "not-allowed" : "pointer",
            fontFamily: "inherit",
          }}
        >
          📎 添加附件
        </button>
        {kept.map((a) =>
          chip(`kept:${a.filename}`, a.filename, a.size_bytes, "原有", () => onChangeKept(kept.filter((k) => k.filename !== a.filename))),
        )}
        {added.map((a) => chip(`add:${a.path}`, a.name, a.size, null, () => onChangeAdded(added.filter((x) => x.path !== a.path))))}
        {(added.length > 0 || kept.length > 0) && (
          <span style={{ fontSize: 11, color: tooBig ? "var(--status-err)" : "var(--catfish-text-muted)" }}>
            合计 {fmtSize(total)}
            {tooBig ? `，超过 ${fmtSize(MAX_ATTACHMENTS_BYTES)}，多数邮箱会退信 —— 请压缩或放网盘发链接` : ""}
          </span>
        )}
        {pickError && <span style={{ fontSize: 11, color: "var(--status-err)" }}>选文件失败: {pickError}</span>}
      </div>
    </div>
  );
}
