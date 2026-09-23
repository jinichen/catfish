/** 微信「合并转发」导出 ZIP 的导入 —— 聊天框拖入 (9/23)。
 *
 * 流程: 拖入 .zip → `wechat_export_stage` (Rust 暂存 + 读取器 inspect, 只看不存)
 *   → 确认框 (群名 / 我是谁 / 首次授权) → `wechat_export_import` (导入库 + 整理文本)
 *   → 变成这条消息的一个附件 (fileKind="wechat")。
 *
 * 解析 / 认群 / 去重都在 catfish-wechat-reader 里, 这里只有界面要用的类型和
 * 纯函数 (可单测), 不重复任何格式规则。
 */
import { invoke } from "@tauri-apps/api/core";

import type { Attachment } from "../types/chat";

export const MAX_WECHAT_ZIP_BYTES = 200 * 1024 * 1024;

export interface WeChatSender {
  name: string;
  count: number;
}

export interface WeChatGroupCandidate {
  group_id: string;
  name: string;
  shared: number;
  ratio: number;
  confident: boolean;
}

/** 读取器 `inspect` 的输出 (字段名跟 Python 端一致, 不改成驼峰)。 */
export interface WeChatInspect {
  message_count: number;
  start: string;
  end: string;
  senders: WeChatSender[];
  attachments: { in_archive: number; referenced: number; missing: number };
  already_imported_group_id: string | null;
  matched_group_id: string | null;
  candidates: WeChatGroupCandidate[];
  suggested_name: string;
  known_self_name: string | null;
}

export interface WeChatStage {
  stageId: string;
  filename: string;
  sizeBytes: number;
  inspect: WeChatInspect;
  needsConsent: boolean;
  pickerModel: string | null;
}

export interface WeChatImportResult {
  groupId: string;
  name: string;
  selfName: string | null;
  alreadyImported: boolean;
  transcript: string;
  truncated: boolean;
  renderedCount: number;
  messageCount: number;
}

/** 确认框里员工的选择。groupId 有值 = 归到已有群; 否则按 newGroupName 新建。 */
export interface WeChatImportChoice {
  groupId: string | null;
  newGroupName: string;
  /** undefined = 不指定 (沿用已记的); "" = 我不在这些发送人里 */
  selfName: string | undefined;
  consent: boolean;
}

export function isZipFile(file: File): boolean {
  return (file.name || "").toLowerCase().endsWith(".zip");
}

async function readBase64(file: File): Promise<string> {
  const dataUri = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error || new Error("FileReader 失败"));
    reader.readAsDataURL(file);
  });
  const comma = dataUri.indexOf(",");
  return comma >= 0 ? dataUri.slice(comma + 1) : dataUri;
}

export async function stageWeChatExport(file: File): Promise<WeChatStage> {
  if (file.size > MAX_WECHAT_ZIP_BYTES) {
    throw new Error("微信导出文件超过 200 MB");
  }
  return invoke<WeChatStage>("wechat_export_stage", {
    fileB64: await readBase64(file),
    filename: file.name || "聊天记录.zip",
  });
}

export async function importWeChatExport(
  stage: WeChatStage,
  choice: WeChatImportChoice,
): Promise<WeChatImportResult> {
  return invoke<WeChatImportResult>("wechat_export_import", {
    stageId: stage.stageId,
    groupId: choice.groupId ?? undefined,
    groupName: choice.groupId ? undefined : choice.newGroupName.trim(),
    selfName: choice.selfName,
    consent: choice.consent,
  });
}

export async function discardWeChatStage(stage: WeChatStage): Promise<void> {
  await invoke("wechat_export_discard", { stageId: stage.stageId });
}

/** 确认框的初始选择: 认出来的群 (含已导入过的) 优先, 否则新建并用建议群名。 */
export function initialChoice(stage: WeChatStage): WeChatImportChoice {
  const inspect = stage.inspect;
  return {
    groupId: inspect.already_imported_group_id ?? inspect.matched_group_id ?? null,
    newGroupName: inspect.suggested_name,
    selfName: inspect.known_self_name ?? undefined,
    consent: false,
  };
}

/** 能不能点「导入」—— 给确认框和测试共用, 规则只写一处。 */
export function choiceProblem(stage: WeChatStage, choice: WeChatImportChoice): string | null {
  if (!stage.pickerModel) return "请先在聊天 Picker 中选择模型";
  if (!choice.groupId && !choice.newGroupName.trim()) return "请填写群名";
  if (stage.needsConsent && !choice.consent) return "请先勾选同意";
  return null;
}

function day(iso: string): string {
  return iso.slice(0, 10);
}

/** 「44 条 · 2026-09-14 ~ 2026-09-20 · 8 人 · 附件 10 个 (2 个未随导出)」 */
export function stageSummary(inspect: WeChatInspect): string {
  const range = day(inspect.start) === day(inspect.end)
    ? day(inspect.start)
    : `${day(inspect.start)} ~ ${day(inspect.end)}`;
  const att = inspect.attachments;
  const attLabel = att.referenced === 0
    ? ""
    : ` · 附件 ${att.referenced} 个` + (att.missing ? ` (${att.missing} 个未随导出)` : "");
  return `${inspect.message_count} 条 · ${range} · ${inspect.senders.length} 人${attLabel}`;
}

/** 导入结果 → 这条消息的附件。previewText 是整理好的全文 (或前 3 万字)。 */
export function toAttachment(
  stage: WeChatStage,
  result: WeChatImportResult,
): Attachment {
  return {
    kind: "file",
    mimeType: "application/zip",
    name: result.name || stage.filename,
    sizeBytes: stage.sizeBytes,
    fileKind: "wechat",
    previewText: result.transcript,
    meta: {
      session_id: result.groupId,
      group_name: result.name,
      self_name: result.selfName,
      message_count: result.messageCount,
      rendered_count: result.renderedCount,
      truncated: result.truncated,
      start: stage.inspect.start,
      end: stage.inspect.end,
      missing_attachments: stage.inspect.attachments.missing,
    },
  };
}

/** 发给模型的附件段。全文在就直接给; 被截断或是历史会话恢复的 (没有全文),
 *  告诉模型用 catfish_wechat_history 按 session_id + 时间段去查, 不要猜。 */
export function formatWeChatAttachment(att: {
  name: string;
  previewText?: string;
  meta?: Record<string, unknown>;
}): string {
  const meta = att.meta || {};
  const sessionId = String(meta.session_id ?? "");
  const count = meta.message_count ?? "?";
  const start = String(meta.start ?? "");
  const end = String(meta.end ?? "");
  const header =
    `\n\n=== 微信聊天记录: ${att.name} (${count} 条 · ${day(start)} ~ ${day(end)}) ===\n` +
    `[已导入本机; 会话 session_id=${sessionId}]\n`;
  const lookup =
    `用 catfish_wechat_history (session_id="${sessionId}", 时间范围在 ${start} ~ ${end} 之内, ` +
    `每次最多 31 天) 读取完整记录, 不要凭空补全。`;
  const missing = Number(meta.missing_attachments ?? 0);
  const missingNote = missing > 0
    ? `\n注意: 有 ${missing} 个附件 (多为压缩包) 微信没有随导出, 只能看到文件名。`
    : "";
  if (!att.previewText) {
    return `${header}(这条消息里没有带全文) ${lookup}${missingNote}`;
  }
  const truncatedNote = meta.truncated
    ? `\n--- 只放了前 ${meta.rendered_count ?? "?"} 条, 后面的 ${lookup} ---`
    : "";
  return (
    `${header}--- 聊天记录 (时间 发送人: 内容; 标 (我) 的是员工本人) ---\n` +
    `${att.previewText}\n--- /聊天记录 ---${truncatedNote}${missingNote}`
  );
}
