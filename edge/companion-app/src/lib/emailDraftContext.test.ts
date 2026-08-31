import { describe, expect, it } from "vitest";

import { buildDraftContextQuery, senderQueryKey } from "./emailDraftContext";

describe("emailDraftContext", () => {
  it("用发件人、主题和正文前段共同检索本地 Wiki", () => {
    expect(senderQueryKey("林莹 <lin@example.com>")).toBe("林莹");
    const query = buildDraftContextQuery(
      "林莹 <lin@example.com>",
      "关于软件企业资质申请需要支持事项",
      "附件已收到，请确认审计报告和申报时间。",
    );
    expect(query).toContain("林莹");
    expect(query).toContain("关于软件企业资质申请需要支持事项");
    expect(query).toContain("附件已收到");
  });

  it("限制正文检索提示长度，不把整封邮件复制进 Wiki 查询", () => {
    const body = "前段 ".repeat(2000);
    const query = buildDraftContextQuery("sender@example.com", "主题", body);
    expect(query.length).toBeLessThan(600);
  });
});
