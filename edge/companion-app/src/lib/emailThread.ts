/** P3.5.58 (6/22 鸿波 catch "有回复了为啥还要让小鲶处理 是不是重复了"):
 *  RFC 822 thread chain 算法 — 判断一封邮件是否已被回复 (用 Message-ID/
 *  In-Reply-To/References 三件套).
 *
 *  规则 (RFC 5322 §3.6.4):
 *      M 被 R 回复 ⇔  R.in_reply_to == M.message_id
 *                  OR M.message_id ∈ R.references.split()
 *
 *  References 是空格分隔的 Message-ID 列表 (祖先链), 长 thread 用它定位.
 *
 *  注意 (鸿波本机场景):
 *    - 自己的回复在 Sent 文件夹, list_fetch 默认只拉 INBOX 不拉 Sent —
 *      如果用户回复邮件落 Sent 而 list 没拉, 本算法在 list 里找不到 reply
 *      → 返 replied=false (Phase 2 限制). Phase 3 加 --include-sent 后治本.
 *    - 但 鸿波截图里 "Re:" 那封是 list 顶部 09:39 自己发的, 说明 list 拉了
 *      Sent 或者 sender 是自己 IMAP 回信进了 INBOX, 算法能 work.
 */

import type { EmailDigestItem } from "./tauri";

export interface IsRepliedResult {
  /** 是否被回复过 */
  replied: boolean;
  /** 已知的回复邮件列表 (按时间倒序) */
  replies: EmailDigestItem[];
}

/** 规范化 RFC Message-ID — 去掉首尾空白 + 多个空白合并.
 *  不剥 <> 包装 (因为 In-Reply-To/References 也是带 <> 的, 全保留对得起来).
 */
function _normalize(id: string | undefined | null): string {
  if (!id) return "";
  return id.trim().replace(/\s+/g, " ");
}

/** 拆 References header — 空格分隔的多个 Message-ID (每个都含 <>). */
function _splitReferences(refs: string | undefined | null): string[] {
  if (!refs) return [];
  // 标准: 空格分隔. 容错: 也可能是逗号分隔 / 换行分隔.
  return refs
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 检测一封邮件 M 是否已被 list 里其他邮件回复.
 *
 *  返 {replied, replies: 按 date desc 排序} — caller 用 replies[0] 显示
 *  "最新回复 hh:mm" 提示.
 */
export function isReplied(msg: EmailDigestItem, list: EmailDigestItem[]): IsRepliedResult {
  const myMid = _normalize(msg.message_id);
  if (!myMid) {
    // 当前邮件没 Message-ID (老邮件 / 老 Mail.app 不暴露字段) → 无法判定, 默认未回复
    return { replied: false, replies: [] };
  }

  const replies: EmailDigestItem[] = [];
  for (const r of list) {
    if (r.id === msg.id) continue;  // 自身排除
    // 条件 1: R.in_reply_to == M.message_id
    if (_normalize(r.in_reply_to) === myMid) {
      replies.push(r);
      continue;
    }
    // 条件 2: M.message_id ∈ R.references.split()
    const refList = _splitReferences(r.references);
    if (refList.includes(myMid)) {
      replies.push(r);
    }
  }

  // 按时间倒序 — 最新回复在前
  replies.sort((a, b) => (a.date < b.date ? 1 : -1));

  return { replied: replies.length > 0, replies };
}

/** 给 badge 用的友好时间格式 — 今天显 "hh:mm", 昨天显 "昨天 hh:mm", 更早显 "MM-DD". */
export function formatReplyTime(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (sameDay) return `${hh}:${mm}`;
    // 昨天
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const isYesterday =
      d.getFullYear() === yesterday.getFullYear() &&
      d.getMonth() === yesterday.getMonth() &&
      d.getDate() === yesterday.getDate();
    if (isYesterday) return `昨天 ${hh}:${mm}`;
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  } catch {
    return iso.slice(0, 16);
  }
}
