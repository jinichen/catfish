/** 10/21: 早安卡片要能说出任务最新进展, 不能只复述「责任 / 截止时间」。 */
import { describe, expect, it } from "vitest";

import type { AdvisorInput } from "./briefing_advisor_common";
import { buildUserPrompt, latestProgress } from "./briefing_advisor_prompts";
import { buildAdvisorAgentRequest } from "./briefing_advisor_request";

const BODY =
  "【进展 2026-10-21（员工确认）】两封索取邮件上午已发，黄骅、邱青青当日回数。\n\n" +
  "【下一步】10/23 收总经办补充材料。" + "很长的历史".repeat(200) + "\n[catfish-task:abc]";

describe("早安任务最新进展", () => {
  it("取正文开头一段, 压成一行, 去同步标记, 限长", () => {
    const p = latestProgress(BODY);
    expect(p.startsWith("【进展 2026-10-21（员工确认）】两封索取邮件上午已发")).toBe(true);
    expect(p).not.toContain("\n");
    expect(p).not.toContain("catfish-task");
    expect(p.length).toBeLessThanOrEqual(301);
    expect(latestProgress(undefined)).toBe("");
    expect(latestProgress("  [catfish-task:x] ")).toBe("");
  });

  it("用户提示里每条任务带上最新进展", () => {
    const input = {
      profile: { tier: "mid", style: "", confidence: 0.5, keyPeople: [], keyProjects: [] },
      emails: [],
      events: [],
      todos: [
        { text: "确认能源数据怎么取", line: 0, source: "reminder", section: "提醒事项", body: BODY,
          reminder_id: "r1", due_date_iso: "2026-10-23T18:00:00" },
        { text: "没写过进展的任务", line: 0, source: "reminder", section: "提醒事项", body: "" },
      ],
      ctx: { projects: "", workplan: "" },
      urgencyMap: {},
      model: "m",
    } as unknown as AdvisorInput;
    const prompt = buildUserPrompt(input);
    expect(prompt).toContain("确认能源数据怎么取");
    expect(prompt).toContain("└ 最新进展: 【进展 2026-10-21（员工确认）】两封索取邮件上午已发");
    expect(prompt.match(/最新进展/g)).toHaveLength(1);
  });

  it("系统提示要求 reason 据最新进展写, 不只是责任和时间", () => {
    const sys = buildAdvisorAgentRequest("m", "u").messages[0].content;
    expect(sys).toContain("最新进展");
    expect(sys).toContain("现在走到哪一步");
  });
});
