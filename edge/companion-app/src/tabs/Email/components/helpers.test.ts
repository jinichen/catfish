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

// 11/13 邮件页整理: 已发送显示收件人 / 附件名中间省略 / 大小
import { _formatSize, _recipientsLabel, _splitFilename } from "./helpers";

describe("_recipientsLabel", () => {
  it("一两个人直接列, 多了说人数", () => {
    expect(_recipientsLabel(["张三 <zhang@example.com>"])).toBe("→ 张三");
    expect(_recipientsLabel(["zhang@example.com", "李四 <li@example.com>"])).toBe("→ zhang、李四");
    expect(_recipientsLabel(["a@example.com", "b@example.com", "c@example.com"])).toBe("→ a、b 等 3 人");
  });
  it("没有收件人返回空串 (调用方退回显示发件人)", () => {
    expect(_recipientsLabel([])).toBe("");
    expect(_recipientsLabel(undefined)).toBe("");
  });
});

describe("_splitFilename", () => {
  it("长名字: 结尾 4 个字 + 扩展名始终显示", () => {
    expect(_splitFilename("2026年第一批第一次结算服务内容（已签字）-南岭建设.pdf")).toEqual([
      "2026年第一批第一次结算服务内容（已签字）-", "南岭建设.pdf",
    ]);
  });
  it("短名字不拆", () => {
    expect(_splitFilename("报价单.xlsx")).toEqual(["报价单.xlsx", ""]);
  });
  it("没有扩展名也行", () => {
    expect(_splitFilename("一个没有扩展名但是很长很长的文件名字")[1]).toBe("文件名字");
  });
});

describe("_formatSize", () => {
  it("KB / MB", () => {
    expect(_formatSize(12398)).toBe("12 KB");
    expect(_formatSize(2435541)).toBe("2.3 MB");
    expect(_formatSize(300)).toBe("1 KB");
    expect(_formatSize(0)).toBe("");
  });
});
