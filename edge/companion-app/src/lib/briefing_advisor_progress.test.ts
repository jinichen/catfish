/** 10/21: 早安卡片要能说出任务最新进展, 不能只复述「责任 / 截止时间」。 */
import { afterEach, describe, expect, it, vi } from "vitest";

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

  afterEach(() => vi.useRealTimers());

  it("新进展追加在正文末尾时也带上 (11/27 ITSS 一级: 开头还是 11/20 的等发证, 末尾 11/27 已取证)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-27T22:08:00+08:00"));
    const body =
      "【当前状态 2026-11-20（员工确认口径）】11月20日官网公示, 公示≠已发证, 现阶段不写已取证。" + "通稿改动记录".repeat(80) +
      "\n- 2026-11-22 15:52 全稿清「技术评审」。" +
      "\n2026-11-27 09:08 官网挂网取证：ITSS-YW-1-350020260016｜一级｜有效｜2026-11-27至2029-11-26。" +
      "\n【截止】2026-12-31。\n[catfish-task:itsspub]";
    const p = latestProgress(body);
    expect(p.startsWith("【当前状态 2026-11-20")).toBe(true);
    expect(p).toContain("正文里日期最新的一条");
    expect(p).toContain("2026-11-27 09:08 官网挂网取证");
    expect(p).not.toContain("15:52");
    expect(p).not.toContain("【截止】"); // 今天以后的日期 (截止 / 有效期至) 不算进展
  });

  it("开头就是最新的, 或正文里没有更新的日期: 只给开头", () => {
    expect(latestProgress(BODY)).not.toContain("日期最新");
    const body = "【进展 2026-11-27】已取证。" + "旧记录".repeat(150) + "\n2026-11-20 公示。";
    expect(latestProgress(body)).not.toContain("日期最新");
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
