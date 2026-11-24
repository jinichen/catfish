// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { RoomSummary } from "../../lib/tauri_colleague_rooms";
import RoomList, { ROOM_FILTER_MIN, filterRooms, lastPreview } from "./RoomList";

const room = (i: number, name: string, text = "收到"): RoomSummary => ({
  room_id: `r${i}`, name, purpose: i === 3 ? "ITSS 二级材料" : "", owner: "a@x", my_state: "joined",
  members: 3, joined: 3, unread: 0, mentioned: 0, activity: "",
  last: { from: "b@x", name: "张三", text, at: "2026-11-24T10:00:00" },
});

describe("RoomList 找群", () => {
  const many = Array.from({ length: ROOM_FILTER_MIN + 2 }, (_, i) => room(i, i === 5 ? "销售一部" : `群${i}`, i === 7 ? "周五交拜访记录" : "收到"));

  it("按群名 / 用途 / 最后一条消息过滤, 不区分大小写", () => {
    expect(filterRooms(many, "销售").map((r) => r.room_id)).toEqual(["r5"]);
    expect(filterRooms(many, "itss").map((r) => r.room_id)).toEqual(["r3"]);
    expect(filterRooms(many, "拜访").map((r) => r.room_id)).toEqual(["r7"]);
    expect(filterRooms(many, "  ")).toHaveLength(many.length);
  });

  it("群少时不出找群框; 多于 8 个才出, 输入后只剩匹配的, 回车打开第一个", () => {
    const { rerender } = render(<RoomList rooms={many.slice(0, ROOM_FILTER_MIN)} loaded open={null} onOpen={() => {}} onCreate={() => {}} />);
    expect(screen.queryByPlaceholderText("找群")).toBeNull();

    const onOpen = vi.fn();
    rerender(<RoomList rooms={many} loaded open={null} onOpen={onOpen} onCreate={() => {}} />);
    const box = screen.getByPlaceholderText("找群");
    fireEvent.change(box, { target: { value: "销售" } });
    expect(screen.getByText("销售一部")).toBeTruthy();
    expect(screen.queryByText("群1")).toBeNull();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith("r5");

    fireEvent.change(box, { target: { value: "不存在的群" } });
    expect(screen.getByText(/没有名字或最近消息里带「不存在的群」的群/)).toBeTruthy();
  });

  it("列表预览去掉开头的 emoji", () => {
    expect(lastPreview({ name: "我", text: "🐟 小鲶: 已复查官网", kind: "text" })).toBe("我: 小鲶: 已复查官网");
  });
});
