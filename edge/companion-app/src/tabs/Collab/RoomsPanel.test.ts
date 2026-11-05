/** 群聊里的小工具 (11/5)。邮箱都是 example.com。 */
import { describe, expect, it, vi } from "vitest";

vi.mock("../../lib/colleagueRoomsStore", () => ({ refreshRooms: vi.fn(), setActiveRoom: vi.fn(), useColleagueRooms: () => ({}) }));

import { labelOf, mentionsIn, notJoinedYet, timeLabel, waitingNotice } from "./RoomsPanel";
import type { RoomMember } from "../../lib/tauri_colleague_rooms";

const M = (email: string, name: string, state: RoomMember["state"] = "joined"): RoomMember =>
  ({ email, name, state, owner: false, me: false });
const members = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三"), M("li@example.com", ""), M("wang@example.com", "王五", "left")];

describe("RoomsPanel helpers", () => {
  it("@ 名字 → 邮箱; 不含自己和已退出的; 没名字用邮箱前缀", () => {
    expect(mentionsIn("@张三 @li 和 @王五 看下, @周总", members, "zhou@example.com")).toEqual(["zhang@example.com", "li@example.com"]);
    expect(mentionsIn("没提醒谁", members, "zhou@example.com")).toEqual([]);
  });
  it("显示名", () => {
    expect(labelOf(members, "zhang@example.com")).toBe("张三");
    expect(labelOf(members, "li@example.com")).toBe("li");
    expect(labelOf(members, "x@example.com")).toBe("x");
  });
  it("时间: 今天只显示时分, 别的天带日期", () => {
    const now = new Date(2026, 10, 5, 18, 0);
    expect(timeLabel(new Date(2026, 10, 5, 9, 5).toISOString(), now)).toBe("09:05");
    expect(timeLabel(new Date(2026, 10, 3, 9, 5).toISOString(), now)).toBe("11/3 09:05");
    expect(timeLabel("bad", now)).toBe("");
  });
  it("还没加入的人: 提示收不到", () => {
    const ms = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三", "invited"), M("li@example.com", "", "invited")];
    expect(notJoinedYet(ms, "zhou@example.com").map((m) => m.email)).toEqual(["zhang@example.com", "li@example.com"]);
    expect(notJoinedYet(ms, "zhang@example.com").map((m) => m.email)).toEqual(["li@example.com"]);
    expect(waitingNotice(notJoinedYet(ms, "zhou@example.com"))).toContain("张三、li 还没点「加入」");
    const many = ["a", "b", "c", "d"].map((x) => M(`${x}@example.com`, x, "invited"));
    expect(waitingNotice(many)).toContain("a、b、c 等 4 人");
  });
});

describe("RoomsPanel 显示 (11/5 UI 优化)", () => {
  it("任务就在眼前时, 它的状态不再单独一行; 更早的任务 (卡不在) 状态照常显示", async () => {
    const { visibleMessages } = await import("./RoomsPanel");
    const card = { msg_id: "m_card", kind: "card", at: "x", text: "@张三 交周报",
                   card: { task_id: "t_20261105_0000000a", title: "交周报", deadline: null, schedule: null, assignees: [], owner: "z" } };
    const st = (tid: string) => ({ msg_id: `m_${tid}`, kind: "status", at: "x", text: "张三 交回了",
                                   status: { task_id: tid, title: "交周报", state: "done" } });
    const msgs = [card, st("t_20261105_0000000a"), st("t_20261105_0000000b"), { msg_id: "m_t", kind: "text", at: "x", text: "hi" }];
    expect(visibleMessages(msgs as never).map((m) => m.msg_id)).toEqual(["m_card", "m_t_20261105_0000000b", "m_t"]);
  });
  it("列表预览: 状态 / 自动转发不再加名字; 截止写短", async () => {
    const { lastPreview, shortWhen } = await import("./RoomsPanel");
    expect(lastPreview({ name: "chenhongbo", text: "chenhongbo 交回了「周报」", kind: "status" })).toBe("chenhongbo 交回了「周报」");
    expect(lastPreview({ name: "张三", text: "收到", kind: "text" })).toBe("张三: 收到");
    const now = new Date(2026, 10, 5);
    expect(shortWhen("2026-11-06", now)).toBe("11/6");
    expect(shortWhen("2026-11-06T15:00", now)).toBe("11/6 15:00");
    expect(shortWhen("2027-01-03", now)).toBe("2027/1/3");
  });
});
