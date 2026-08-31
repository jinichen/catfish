import { describe, expect, it } from "vitest";

import {
  formatAttachmentContext,
  formatThreadContext,
  isSameEmailThread,
  selectRecentThreadMessages,
  selectReplyAttachments,
  threadIds,
} from "./emailReplyContext";

const base = {
  id: "current",
  date: "2026-08-28T10:00:00Z",
  sender: "当前发件人 <sender@example.com>",
  subject: "合同确认",
  message_id: "<current@example.com>",
  in_reply_to: "<parent@example.com>",
  references: "<root@example.com> <parent@example.com>",
};

describe("emailReplyContext", () => {
  it("只用 RFC 822 头识别线程，不因主题相同而串线", () => {
    expect(isSameEmailThread(base, {
      ...base,
      id: "same-thread",
      message_id: "<reply@example.com>",
      in_reply_to: "<current@example.com>",
      references: "<root@example.com> <current@example.com>",
    })).toBe(true);
    expect(isSameEmailThread(base, {
      ...base,
      id: "same-subject-only",
      message_id: "<other@example.com>",
      in_reply_to: undefined,
      references: undefined,
    })).toBe(false);
    expect(threadIds(base)).toEqual(new Set([
      "<current@example.com>",
      "<parent@example.com>",
      "<root@example.com>",
    ]));
  });

  it("排除当前邮件并按时间选择有限数量的历史邮件", () => {
    const candidates = [
      { ...base, id: "old", date: "2026-08-27T10:00:00Z", message_id: "<old@example.com>", in_reply_to: "<root@example.com>", references: "<root@example.com>" },
      { ...base, id: "new", date: "2026-08-28T11:00:00Z", message_id: "<new@example.com>", in_reply_to: "<current@example.com>", references: "<root@example.com> <current@example.com>" },
      { ...base, id: "unrelated", date: "2026-08-28T12:00:00Z", message_id: "<unrelated@example.com>", in_reply_to: "<else@example.com>", references: "<else@example.com>" },
    ];
    expect(selectRecentThreadMessages(base, candidates, 2).map((item) => item.id))
      .toEqual(["new", "old"]);
  });

  it("对线程和附件上下文加来源标签并限制附件大小与数量", () => {
    const thread = formatThreadContext([{
      ...base,
      id: "history",
      body_text: "历史正文",
    }]);
    expect(thread).toContain("同一邮件线程的近期往来");
    expect(thread).toContain("历史正文");

    const attachments = selectReplyAttachments([
      { filename: "ok.txt", size_bytes: 10, content_type: "text/plain" },
      { filename: "too-large.txt", size_bytes: 10 * 1024 * 1024 + 1, content_type: "text/plain" },
      { filename: "ok-2.txt", size_bytes: 10, content_type: "text/plain" },
      { filename: "ok-3.txt", size_bytes: 10, content_type: "text/plain" },
      { filename: "ok-4.txt", size_bytes: 10, content_type: "text/plain" },
      { filename: "ignored.txt", size_bytes: 10, content_type: "text/plain" },
    ]);
    expect(attachments.map((item) => item.filename)).toEqual([
      "ok.txt", "ok-2.txt", "ok-3.txt", "ok-4.txt",
    ]);
    expect(formatAttachmentContext([{
      filename: "ok.txt",
      contentType: "text/plain",
      sizeBytes: 10,
      previewText: "附件正文",
    }])).toContain("当前邮件附件的本地解析预览");
  });
});
