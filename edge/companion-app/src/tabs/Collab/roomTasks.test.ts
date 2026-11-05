/** 群里派任务的小工具 (11/5)。邮箱都是 example.com, 时间都钉死。 */
import { describe, expect, it } from "vitest";

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import { cardStates, parseChatTask, parseDeadline, summaryText } from "./roomTasks";

// 2026-11-05 是周四
const NOW = new Date(2026, 10, 5, 15, 30);
const M = (email: string, name: string, state: RoomMember["state"] = "joined"): RoomMember => ({ email, name, state, owner: false, me: false });
const members = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三"), M("li@example.com", "李四"), M("wang@example.com", "王五", "invited")];

describe("parseDeadline", () => {
  it.each([
    ["周五前交", "2026-11-06T18:00"],
    ["周四", "2026-11-05T18:00"],
    ["周一", "2026-11-09T18:00"],
    ["下周五", "2026-11-13T18:00"],
    ["下周一", "2026-11-09T18:00"],
    ["明天中午", "2026-11-06T12:00"],
    ["明天下午3点", "2026-11-06T15:00"],
    ["后天 9:30", "2026-11-07T09:30"],
    ["11月20日前", "2026-11-20T18:00"],
    ["1月3号", "2027-01-03T18:00"],
    ["2026-12-01", "2026-12-01T18:00"],
    ["今天下班前", "2026-11-05T18:00"],
    ["下午5点前", "2026-11-05T17:00"],
    ["上午9点", "2026-11-06T09:00"],
    ["把拜访记录整理一下", ""],
    ["拜访 5 家", ""],
  ])("%s → %s", (text, want) => {
    expect(parseDeadline(text, NOW)).toBe(want);
  });
});

describe("parseChatTask", () => {
  const ME = "zhou@example.com";
  const P = (t: string) => parseChatTask(t, members, ME, NOW);

  it("没 @ 到已加入的人 = 普通聊天", () => {
    expect(P("大家周五前交周报")).toBeNull();
    expect(P("@王五 周五前交周报")).toBeNull();          // 王五还没加入
    expect(P("@周总 我自己")).toBeNull();
  });
  it("一次性: @ 到的人 + 截止", () => {
    expect(P("@张三 @李四 周五前把拜访记录交上来")).toEqual({
      kind: "task", text: "@张三 @李四 周五前把拜访记录交上来", recipients: ["zhang@example.com", "li@example.com"],
      deadline: "2026-11-06T18:00", schedule: null, label: "派给 张三、李四 · 截止 2026-11-06 18:00" });
    expect(P("@张三 看下合同")).toMatchObject({ deadline: "", label: "派给 张三 · 不限时" });
  });
  it("周期: 每周五下午5点, 做4次 / 到年底; 没说结束 → 提示", () => {
    expect(P("@张三 每周五下午5点交周报, 做4次")).toMatchObject({
      schedule: { kind: "cron", expr: "0 17 * * 5", until: null, times: 4 }, label: "派给 张三 · 每周五 17:00, 做 4 次" });
    expect(P("@李四 每个工作日 9:30 看一眼工单, 到年底")).toMatchObject({
      schedule: { kind: "cron", expr: "30 9 * * 1-5", until: "2026-12-31", times: null } });
    expect(P("@李四 每周一、三 上午10点同步进度 到12月31日")).toMatchObject({
      schedule: { kind: "cron", expr: "0 10 * * 1,3", until: "2026-12-31" } });
    expect(P("@张三 每月15号交报销 做三次")).toMatchObject({ schedule: { expr: "0 9 15 * *", times: 3 } });
    expect(P("@张三 每天交日报")).toMatchObject({ schedule: null, problem: expect.stringContaining("什么时候结束") });
  });
  it("定时做一次: 「明天上午9点做」「定时」; 「X点前」还是截止", () => {
    expect(P("@张三 明天上午9点做一次库存盘点")).toMatchObject({ schedule: { kind: "once", at: "2026-11-06T09:00" }, deadline: "" });
    expect(P("@张三 定时 后天 14:00 发提醒")).toMatchObject({ schedule: { kind: "once", at: "2026-11-07T14:00" } });
    expect(P("@张三 明天下午3点前交")).toMatchObject({ schedule: null, deadline: "2026-11-06T15:00" });
  });
  it("多阶段: 「先让@张三 …, 再让@李四 …」", () => {
    const t = P("先让@张三 周五前收本月数据, 再让@李四 下周一前汇总成月报");
    expect(t).toMatchObject({ kind: "pipeline", label: "分 2 段: ① 张三 → ② 李四" });
    expect(t?.kind === "pipeline" && t.stages.map((s) => [s.text, s.recipients, s.deadline])).toEqual([
      ["@张三 周五前收本月数据", ["zhang@example.com"], "2026-11-06T18:00"],
      ["@李四 下周一前汇总成月报", ["li@example.com"], "2026-11-09T18:00"],
    ]);
    expect(P("先让@张三 收数, 再让王五汇总")).toMatchObject({ kind: "task" });   // 第二段没 @ 人: 不算多阶段
    // @ 后面不带空格也认 (11/5 实测「再让@test下周一前汇总」)
    expect(P("先让@张三周五前收数据，再让@李四下周一前汇总")).toMatchObject({ kind: "pipeline", label: "分 2 段: ① 张三 → ② 李四" });
    expect(P("@张三周五前交")).toMatchObject({ recipients: ["zhang@example.com"], deadline: "2026-11-06T18:00" });
  });
});

describe("多行待办 → 一行派一个 (11/5)", () => {
  const ME = "zhou@example.com";
  it("小鲶整理的待办: 每行各自的人和截止; 标题行 / 没 @ 人的行不管", () => {
    const t = parseChatTask("待办:\n1. @张三 发周报 (明天)\n2. @李四 交拜访记录 (周五前)\n· @张三 对一下报价 (待定)", members, ME, NOW);
    expect(t?.kind).toBe("batch");
    if (t?.kind !== "batch") return;
    expect(t.items.map((x) => [x.text, x.recipients, x.deadline])).toEqual([
      ["@张三 发周报 (明天)", ["zhang@example.com"], "2026-11-06T18:00"],
      ["@李四 交拜访记录 (周五前)", ["li@example.com"], "2026-11-06T18:00"],
      ["@张三 对一下报价 (待定)", ["zhang@example.com"], ""],
    ]);
    expect(t.label).toBe("分别派 3 条: 张三 2 条、李四 1 条");
    expect(t.problem).toBeUndefined();
  });
  it("某一条有问题就说是第几条; 「先…再…」还是多阶段", () => {
    const t = parseChatTask("@张三 每天交日报\n@李四 周五前交", members, ME, NOW);
    expect(t).toMatchObject({ kind: "batch", problem: expect.stringContaining("第 1 条") });
    expect(parseChatTask("先让@张三 收数,\n再让@李四 汇总", members, ME, NOW)?.kind).toBe("pipeline");
  });
});

describe("cardStates / summaryText", () => {
  const st = (from: string, state: "accepted" | "done" | "declined", task_id = "t_20261105_0123abcd"): RoomMessage =>
    ({ msg_id: `m_${from.slice(0, 4)}${state}`, kind: "status", from, at: "x", text: "", status: { task_id, title: "周报", state } });
  it("每人取最后一条状态, 别的任务的不算", () => {
    const msgs = [st("zhang@example.com", "accepted"), st("zhang@example.com", "done"), st("li@example.com", "declined", "t_20261105_ffffffff")];
    expect(cardStates("t_20261105_0123abcd", ["zhang@example.com", "li@example.com"], msgs, members).map((x) => [x.name, x.state]))
      .toEqual([["张三", "done"], ["李四", "waiting"]]);
  });
  it("汇总: 每人状态 + 结果截 200 字", () => {
    const row = (email: string, state: "已完成" | "在做", result: string | null) =>
      ({ email, state, result, invited_at: null, reminded_at: null, accepted_at: null, done_at: null, reason: null, periods: [] });
    const t = summaryText("周报", [row("zhang@example.com", "已完成", "字".repeat(250)), row("li@example.com", "在做", null)],
                          (e) => (e.startsWith("zhang") ? "张三" : "李四"));
    expect(t.split("\n")).toEqual(["「周报」汇总", `· 张三 已完成: ${"字".repeat(200)}…`, "· 李四 在做"]);
  });
});
