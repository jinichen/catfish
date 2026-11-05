/** 群里派任务的小工具 (11/5)。邮箱都是 example.com, 时间都钉死。 */
import { describe, expect, it } from "vitest";

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import { cardStates, draftFromText, isTaskAsk, parseDeadline, summaryText } from "./roomTasks";

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

describe("draftFromText", () => {
  it("「@小鲶 让张三、李四周五前…」→ 收件人 + 截止 + 去掉 @小鲶", () => {
    expect(isTaskAsk("@小鲶 让张三写周报")).toBe(true);
    expect(isTaskAsk("@张三 写周报")).toBe(false);
    const d = draftFromText("@小鲶 让张三、@李四 周五前把本周拜访记录交上来", members, "zhou@example.com", NOW);
    expect(d).toEqual({ text: "张三、@李四 周五前把本周拜访记录交上来", recipients: ["zhang@example.com", "li@example.com"],
                        deadline: "2026-11-06T18:00" });
  });
  it("没加入的、自己不当收件人", () => {
    const d = draftFromText("@小鲶 让王五和周总、张三看下", members, "zhou@example.com", NOW);
    expect(d.recipients).toEqual(["zhang@example.com"]);
    expect(d.deadline).toBe("");
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
