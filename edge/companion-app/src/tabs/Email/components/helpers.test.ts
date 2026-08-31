import { describe, expect, it } from "vitest";

import { _mergeReplyDraftWithQuote } from "./helpers";

describe("_mergeReplyDraftWithQuote", () => {
  it("puts the generated reply before the original-message quote", () => {
    expect(_mergeReplyDraftWithQuote("回复正文", "\n\n--- 原邮件 ---\n> 原文"))
      .toBe("回复正文\n\n--- 原邮件 ---\n> 原文");
  });

  it("does not duplicate the quote when redrafting", () => {
    const quote = "--- 原邮件 ---\n> 原文";
    expect(_mergeReplyDraftWithQuote("第二版", quote)).toBe(`第二版\n\n${quote}`);
  });

  it("keeps a draft without a quote unchanged", () => {
    expect(_mergeReplyDraftWithQuote("  正文  ", "")).toBe("  正文  ");
  });
});
