/** EmailTab pure helpers — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 跟 BriefingCard helpers 一样, 无 React 依赖, 单测友好.
 */

export function _extractSenderName(sender: string): string {
  if (!sender) return "(未知)";
  const m = sender.match(/^([^<]+?)\s*<.+>$/);
  if (m) return m[1].trim().replace(/^"|"$/g, "");
  if (sender.includes("@")) return sender.split("@")[0];
  return sender;
}

/** "张三 <zhang@x.com>" → "zhang@x.com"; "bob@example.com" → "bob@example.com" */
export function _replyAddress(sender: string): string {
  if (!sender) return "";
  const m = sender.match(/<([^>]+)>/);
  if (m) return m[1].trim();
  return sender.trim();
}

/** P3.5.158 Phase 1 (7/2 鸿波): 生"Re: X" 主题 — 空 subject 兜"Re: ", 已有 Re:
 *  前缀不重复加. 抽自 DetailPane.handleOpenCompose. 给 ComposeCore 共用.
 */
export function _buildReplySubject(subject: string | undefined | null): string {
  const s = subject || "";
  return s.startsWith("Re:") ? s : `Re: ${s}`;
}

/** P3.5.158 Phase 1 (7/2 鸿波): 生回复引用段. 抽自 DetailPane.handleOpenCompose.
 *  body_text 空时引用段仍生 (只留 header + 空 quoted), 让员工看到"这是回复给谁的".
 */
export function _buildQuotedBody(msg: {
  sender: string;
  date: string;
  subject: string;
  body_text?: string;
}): string {
  const quoted = (msg.body_text || "")
    .split("\n")
    .map((l) => `> ${l}`)
    .join("\n");
  return (
    `\n\n\n${"-".repeat(20)} 原邮件 ${"-".repeat(20)}\n` +
    `发件人: ${msg.sender}\n` +
    `时间: ${msg.date}\n` +
    `主题: ${msg.subject}\n\n` +
    quoted
  );
}

export function _formatShortDate(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const dayDiff = Math.floor((now.getTime() - d.getTime()) / (24 * 3600 * 1000));
    if (dayDiff === 0) {
      return d.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    if (dayDiff === 1) return "昨天";
    if (dayDiff < 7) return `${dayDiff}天前`;
    return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch {
    return "";
  }
}
