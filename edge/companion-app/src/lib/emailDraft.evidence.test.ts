import { expect, it, vi } from "vitest";
import { FACT_EVIDENCE_RULES } from "./factEvidence";

vi.mock("./expertBots", () => ({
  resolveExpertBotRequest: async () => ({ url: "http://test", model: "test" }),
}));
vi.mock("./me", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "./me";
import { draftEmailReply } from "./emailDraft";

it("邮件拟稿请求保留来源和原始时间，使用共享规则且仍返回草稿协议", async () => {
  vi.mocked(fetchWithAuth).mockResolvedValue(new Response(JSON.stringify({
    choices: [{ message: { content: "领取状态为 [TODO: 待确认]。" } }],
  })));
  const result = await draftEmailReply({
    sender: "sender@example.com", subject: "证书审核通过", date: "2026-09-10",
    bodyText: "审核已通过。", agentName: "小鲶", model: "test",
    context: [{ title: "证书申报", relPath: "wiki/entities/certificate.md", body: "申报中" }],
  });
  const request = JSON.parse(String(vi.mocked(fetchWithAuth).mock.calls[0][1]?.body));
  expect(request.messages[0].content).toContain(FACT_EVIDENCE_RULES);
  expect(request.messages[0].content).not.toContain("冲突时以原邮件为准");
  expect(request.messages[1].content).toContain("wiki/entities/certificate.md");
  expect(request.messages[1].content).toContain("事件时间: 未知");
  expect(request.messages[1].content).toContain("2026-09-10");
  expect(result).toEqual({ ok: true, kind: "draft", body: "领取状态为 [TODO: 待确认]。" });
});
