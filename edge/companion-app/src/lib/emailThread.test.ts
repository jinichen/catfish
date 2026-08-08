/** isReplied 单测 (8/6)。
 *
 *  为什么补：8/6 修了两个 adapter 的 thread 头，两个都是**让 isReplied 从「永远
 *  返 false」变成能用**：
 *
 *    · Foxmail 建 Message 时只写了 in_reply_to，message_id / references 一个没设
 *      → isReplied 第一句 `if (!myMid) return false` 直接短路
 *    · Apple Mail 的 _parse_thread_headers 用 email.parser，被 header 块里一行
 *      脏数据整段截断 → 三个头全解不出来，message_id 只是靠 `or rfc_msg_id` 兜底
 *      活下来（所以现象是「Message-ID 有值、另两个永远空」）
 *
 *  这两个 bug 谁都没报错 —— 角标不亮跟「这封确实没人回」看起来一模一样。
 *  所以这里锁住行为，别再退回去。
 */
import { describe, expect, it } from "vitest";

import { isReplied, formatReplyTime } from "./emailThread";
import type { EmailDigestItem } from "./tauri";

function mk(
  id: string,
  opts: Partial<EmailDigestItem> & { mid?: string; irt?: string; refs?: string } = {},
): EmailDigestItem {
  return {
    id,
    account: "a@x.com",
    folder: opts.folder ?? "Inbox",
    subject: opts.subject ?? "s",
    sender: opts.sender ?? "对方 <b@x.com>",
    date: opts.date ?? "2026-08-01T00:00:00+00:00",
    is_read: true,
    has_attachments: false,
    body_text: opts.body_text ?? "",
    message_id: opts.mid,
    in_reply_to: opts.irt,
    references: opts.refs,
  };
}

describe("isReplied", () => {
  it("★ message_id 缺失时一律 false —— Foxmail 8/6 之前就卡在这", () => {
    const m = mk("1"); // 没 message_id
    const reply = mk("2", { irt: "<a>", folder: "Sent" });
    expect(isReplied(m, [reply]).replied).toBe(false);
  });

  it("★ 补上 message_id 后，Sent 里的回信匹配得到", () => {
    const m = mk("1", { mid: "<a>" });
    const reply = mk("2", { mid: "<b>", irt: "<a>", folder: "Sent" });
    expect(isReplied(m, [reply]).replied).toBe(true);
  });

  it("条件 2：靠 References 全链也能匹配（不只看直接父级）", () => {
    const m = mk("1", { mid: "<a>" });
    // 孙辈：in_reply_to 指向 <b>，但 references 里含 <a>
    const grandchild = mk("3", { mid: "<c>", irt: "<b>", refs: "<a> <b>" });
    expect(isReplied(m, [grandchild]).replied).toBe(true);
  });

  it("排除自身 —— 同一封不能算自己回了自己", () => {
    const m = mk("1", { mid: "<a>", irt: "<a>" });
    expect(isReplied(m, [m]).replied).toBe(false);
  });

  it("不相干的邮件不算", () => {
    const m = mk("1", { mid: "<a>" });
    expect(isReplied(m, [mk("9", { mid: "<z>", refs: "<y>" })]).replied).toBe(false);
  });

  it("多条回复按时间倒序，replies[0] 是最新的", () => {
    const m = mk("1", { mid: "<a>" });
    const early = mk("2", { mid: "<b>", irt: "<a>", date: "2026-08-01T01:00:00+00:00" });
    const late = mk("3", { mid: "<c>", irt: "<a>", date: "2026-08-03T01:00:00+00:00" });
    const r = isReplied(m, [early, late]);
    expect(r.replies.map((x) => x.id)).toEqual(["3", "2"]);
  });

  it("References 容错：逗号 / 多空格分隔也认", () => {
    const m = mk("1", { mid: "<a>" });
    expect(isReplied(m, [mk("2", { mid: "<b>", refs: "<x>,  <a>" })]).replied).toBe(true);
  });
});

describe("formatReplyTime", () => {
  it("空串返空，不炸", () => {
    expect(formatReplyTime("")).toBe("");
  });
  it("坏字符串退回前 16 字", () => {
    expect(formatReplyTime("not-a-date-at-all-x")).toBe("not-a-date-at-al");
  });
});
