/** 邮件 → 工作台 chat 的交接 (8/21, 从 EmailTab.handleAskCatfish 抽出)。
 *
 * # 交接必须带**句柄** (email_id), 不能只带给人看的摘要
 *
 * 之前 starter 只有 发件人/主题/500 字摘要 —— 而 chat 里的 agent 手里握着
 * catfish_email_read (必填 email_id) / catfish_email_attachment /
 * catfish_email_search 四把工具, 却打不开员工正在看的这封: 只能拿主题去
 * search 碰运气。工具齐备, 钥匙没给。
 *
 * 现在: id 直接给, agent 自己去读全文/拆附件/提行动项/问要不要建提醒。
 * 摘要仍保留 —— 那是给**员工**看的开场白 (对话第一条要能看懂),
 * 不再是 agent 的数据源。
 *
 * 抽成纯函数的原因: EmailTab.tsx 撞 800 行红线 + 这段是纯文本构造, 本来就该
 * 独立可测 (id 丢了 / 指引丢了, UI 上完全看不出来, 只有 agent 行为变笨)。
 */

/** 构造 starter 需要的字段 —— EmailDigestItem/FullMessage 都满足。 */
export interface HandoffMessage {
  id: string;
  account: string;
  sender: string;
  subject: string;
  date: string;
  body_text?: string;
  has_attachments?: boolean;
}

export function buildAskCatfishStarter(m: HandoffMessage): string {
  const snippet = (m.body_text || "").slice(0, 500);
  return (
    `这封邮件:\n` +
    `- email_id: ${m.id}\n` +
    `- 账号: ${m.account}\n` +
    `- 发件人: ${m.sender}\n` +
    `- 主题: ${m.subject}\n` +
    `- 时间: ${m.date}\n` +
    (m.has_attachments ? `- 有附件\n` : ``) +
    `\n正文摘要 (给我看的, 你别只靠它):\n${snippet}${(m.body_text || "").length > 500 ? "…" : ""}\n\n` +
    `请先用 catfish_email_read(email_id=上面那个) 读全文` +
    (m.has_attachments ? `, 有附件就用 catfish_email_attachment 看` : ``) +
    `, 然后告诉我: 这封要我做什么、有没有截止日期。要回的话再一起商量怎么回。\n` +
    `涉及截止日期的, 问我要不要建提醒 (catfish_create_reminder)。`
  );
}
