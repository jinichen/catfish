import { describe, expect, it } from "vitest";

import { groupSameMatter, matterKey, shelveStaleTodos, SHELVED_TYPE } from "./briefing_todo_shelf";
import type { ReminderTodo } from "./tauri_briefing";

const NOW = Date.parse("2026-11-25T21:00:00+08:00");
const daysAgo = (n: number) => (NOW - n * 86_400_000) / 1000;
const todo = (text: string, extra: Partial<ReminderTodo> = {}): ReminderTodo => ({
  text, line: 0, source: "reminder", section: "Reminders", ...extra,
});

describe("早安任务清单整理", () => {
  it("没截止日期又两周没动的收到搁置; 有截止日期 / 标重点 / 最近动过 / 不知道几时动的都留下", () => {
    const { active, shelved } = shelveStaleTodos([
      todo("统计安全服务团队人员数量", { updated_at: daysAgo(13.6) }),
      todo("在收到缴费通知后10天内完成认证费用缴纳", { updated_at: daysAgo(20) }),
      todo("高新技术企业年度报表报送", { updated_at: daysAgo(40), due_date_iso: "2027-05-31T18:00:00" }),
      todo("资质日常管理", { updated_at: daysAgo(60), is_priority: true }),
      todo("CIC 一级证书落款待核实", { updated_at: daysAgo(8) }),
      todo("老数据没有时间"),
    ], NOW);
    expect(shelved).toEqual([
      { type: SHELVED_TYPE, count: 0, category: "在收到缴费通知后10天内完成认证费用缴纳（20 天没动，没有截止日期）" },
    ]);
    expect(active.map((t) => t.text)).toEqual([
      "统计安全服务团队人员数量", "高新技术企业年度报表报送", "资质日常管理", "CIC 一级证书落款待核实", "老数据没有时间",
    ]);
  });

  it("冒号前的名字一样算同一件事, 并成一组, 顺序不变", () => {
    expect(matterKey("资质集采三期：12/01 公示期满，取两包成交通知书")).toBe(matterKey("⭐ 资质集采三期: 两包均已定第一成交候选人"));
    expect(matterKey("资质集采三期：12/01 公示期满")).toBe("资质集采三期");
    expect(matterKey("资质集采三期：x")).not.toBe(matterKey("资质集采二期：x"));
    expect(matterKey("高新：等科技局反馈")).toBeNull(); // 名字太短, 不拿来并
    expect(matterKey("钱总的项目")).toBeNull();
    const groups = groupSameMatter([
      todo("资质集采三期：两包均已定第一成交候选人，11/30 公示期满取成交通知书"),
      todo("钱总的项目"),
      todo("资质集采三期：12/01 公示期满，取两包成交通知书"),
      todo("ITSS运维一级：公示届满当天复查官网"),
    ]);
    expect(groups.map((g) => g.map((t) => t.text.slice(0, 8)))).toEqual([
      ["资质集采三期：两", "资质集采三期：1"], ["钱总的项目"], ["ITSS运维一级"],
    ]);
  });
});
