/** ChatInput 显示用 helper — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * fileEmoji: 看文件名返合适 emoji
 * metaSummary: 看 attachment meta 拼成一行人话 (页数 / 字符数 / 时长 等)
 */

import type { Attachment } from "../../../types/chat";

export function fileEmoji(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "📕";
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls") || lower.endsWith(".csv")) return "📊";
  if (lower.endsWith(".docx")) return "📝";
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "📋";
  return "📄";
}

/** 把 meta 转成简短显示文案. excel: "5 sheets · 365 行"; pdf: "12 页"; etc */
export function metaSummary(att: Attachment): string {
  const sizeKb = Math.max(1, Math.round(att.sizeBytes / 1024));
  const sizeLabel = sizeKb >= 1024
    ? `${(sizeKb / 1024).toFixed(1)} MB`
    : `${sizeKb} KB`;
  const m = att.meta || {};
  const kind = att.fileKind;

  if (kind === "excel") {
    const sheets = (m.sheets as string[] | undefined) || [];
    const counts = (m.row_counts as Record<string, number> | undefined) || {};
    const totalRows = Object.values(counts).reduce((a, b) => a + b, 0);
    return `${sheets.length} sheet · ${totalRows} 行 · ${sizeLabel}`;
  }
  if (kind === "pdf") {
    return `${m.page_count ?? "?"} 页 · ${sizeLabel}`;
  }
  if (kind === "word") {
    const tables = m.table_count ?? 0;
    const tableLabel = tables ? ` · ${tables} 表格` : "";
    return `${m.paragraph_count ?? "?"} 段${tableLabel} · ${sizeLabel}`;
  }
  if (kind === "csv") {
    return `${m.total_rows ?? "?"} 行 · ${sizeLabel}`;
  }
  if (kind === "text") {
    return `${m.total_chars ?? "?"} 字 · ${sizeLabel}`;
  }
  // BL-VOICE3 (5/10): 音频转录后显示时长 + 转录字数 (字段名跟 chat.ts 对齐)
  if (kind === "audio") {
    const sec = m.duration_sec as number | null | undefined;
    const chars = m.transcript_chars as number | undefined;
    const durLabel = sec
      ? sec >= 60
        ? `${Math.floor(sec / 60)}分${Math.round(sec % 60)}秒`
        : `${Math.round(sec)}秒`
      : "?";
    return `🎵 ${durLabel} · 转录 ${chars ?? "?"} 字 · ${sizeLabel}`;
  }
  return sizeLabel;
}

