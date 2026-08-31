/** 回复拟稿的分层上下文选择与格式化。
 *
 * 只处理本地已拿到的数据，不负责网络调用：
 * - 线程必须通过 RFC 822 Message-ID / In-Reply-To / References 关联；
 * - 附件只接受本地解析出的预览，不把原始文件发给模型；
 * - 所有输出都有来源标签，便于拟稿 prompt 和 UI 解释。
 */

export interface EmailThreadMetadata {
  id: string;
  date: string;
  sender: string;
  subject: string;
  message_id?: string;
  in_reply_to?: string;
  references?: string;
}

export interface ReplyThreadMessage extends EmailThreadMetadata {
  /** read_message 返回的完整正文；list 失败时可能只是摘要。 */
  body_text: string;
}

export interface ReplyAttachmentContext {
  filename: string;
  contentType: string;
  sizeBytes: number;
  previewText: string;
  kind?: string;
}

const THREAD_LIMIT = 4;
const ATTACHMENT_LIMIT = 4;
const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
const PREVIEW_LIMIT = 4_000;

function normalizeId(id: string | undefined | null): string {
  return (id || "").trim().replace(/\s+/g, " ").toLowerCase();
}

function splitReferences(refs: string | undefined | null): string[] {
  return (refs || "")
    .split(/[\s,]+/)
    .map(normalizeId)
    .filter(Boolean);
}

/** 返回一封邮件在 RFC 822 线程链中出现过的全部 ID。 */
export function threadIds(message: Pick<EmailThreadMetadata, "message_id" | "in_reply_to" | "references">): Set<string> {
  return new Set([
    normalizeId(message.message_id),
    normalizeId(message.in_reply_to),
    ...splitReferences(message.references),
  ].filter(Boolean));
}

/** 只按 RFC 线程头判断，绝不使用主题相同作为兜底，避免串入不同邮件。 */
export function isSameEmailThread(
  left: Pick<EmailThreadMetadata, "message_id" | "in_reply_to" | "references">,
  right: Pick<EmailThreadMetadata, "message_id" | "in_reply_to" | "references">,
): boolean {
  const leftIds = threadIds(left);
  if (!leftIds.size) return false;
  for (const id of threadIds(right)) {
    if (leftIds.has(id)) return true;
  }
  return false;
}

/** 从 Inbox/Sent 候选中挑最近的同线程邮件。当前邮件本身和无线程头邮件排除。 */
export function selectRecentThreadMessages<T extends EmailThreadMetadata>(
  current: EmailThreadMetadata,
  candidates: T[],
  limit = THREAD_LIMIT,
): T[] {
  return candidates
    .filter((candidate) => candidate.id !== current.id && isSameEmailThread(current, candidate))
    .sort((a, b) => (a.date < b.date ? 1 : -1))
    .slice(0, limit);
}

function clip(text: string, limit = PREVIEW_LIMIT): string {
  const clean = (text || "").trim();
  return clean.length > limit ? `${clean.slice(0, limit)}…(已截断)` : clean;
}

export function formatThreadContext(messages: ReplyThreadMessage[]): string {
  if (!messages.length) return "";
  const blocks = messages.map((message, index) => (
    `【历史邮件 ${index + 1}】\n` +
    `发件人: ${message.sender}\n` +
    `时间: ${message.date}\n` +
    `主题: ${message.subject}\n` +
    `正文:\n${clip(message.body_text)}`
  ));
  return `同一邮件线程的近期往来（仅供核对已确认事实）:\n\n${blocks.join("\n\n---\n\n")}\n\n════════\n\n`;
}

/** 只选择适合快速本地解析的附件；过大或超出数量的附件只保留元数据。 */
export function selectReplyAttachments(
  attachments: Array<{ filename: string; size_bytes: number; content_type: string }> | undefined,
): Array<{ filename: string; size_bytes: number; content_type: string }> {
  return (attachments || [])
    .filter((attachment) => attachment.size_bytes <= ATTACHMENT_MAX_BYTES)
    .slice(0, ATTACHMENT_LIMIT);
}

export function formatAttachmentContext(attachments: ReplyAttachmentContext[]): string {
  if (!attachments.length) return "";
  const blocks = attachments.map((attachment) => (
    `【附件】${attachment.filename} (${attachment.contentType || "未知类型"}, ${attachment.sizeBytes} bytes)\n` +
    `本地解析预览:\n${clip(attachment.previewText)}`
  ));
  return `当前邮件附件的本地解析预览（不是原始文件）:\n\n${blocks.join("\n\n---\n\n")}\n\n════════\n\n`;
}

export const EMAIL_REPLY_CONTEXT_LIMITS = {
  threadMessages: THREAD_LIMIT,
  attachments: ATTACHMENT_LIMIT,
  attachmentMaxBytes: ATTACHMENT_MAX_BYTES,
} as const;
