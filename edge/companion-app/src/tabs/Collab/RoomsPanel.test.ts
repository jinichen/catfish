/** 群聊里的小工具 (11/5)。邮箱都是 example.com。 */
import { describe, expect, it, vi } from "vitest";

vi.mock("../../lib/colleagueRoomsStore", () => ({ refreshRooms: vi.fn(), setActiveRoom: vi.fn(), useColleagueRooms: () => ({}) }));

import { labelOf, mentionsIn, timeLabel } from "./RoomsPanel";
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
});
