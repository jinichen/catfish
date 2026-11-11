/** 一起改文档的待办: 截止说法、哪几条该弹通知 (11/11)。 */
import { describe, expect, it } from "vitest";

import { dueLabel, dueToNotify, noticeKey } from "./revisePending";
import type { RevisePendingItem } from "./tauri_room_revise";

const item = (rev: string, days: number | null): RevisePendingItem => ({
  room_id: "r", room_name: "方案群", rev_id: rev, title: "实施方案", version: 1, owner_name: "周总",
  deadline: days === null ? null : "2026-11-20", days_left: days, state: "pending", local_path: null, msg_id: "m", at: "x",
});

describe("revisePending", () => {
  it("截止说法", () => {
    expect([dueLabel(null), dueLabel(0), dueLabel(1), dueLabel(3), dueLabel(-2)]).toEqual(["不限时", "今天截止", "明天截止", "3 天后截止", "已过截止 2 天"]);
  });
  it("今天 / 明天截止、没弹过的才弹; 夜里不弹; 弹过的同一天不再弹", () => {
    const xs = [item("a", 0), item("b", 1), item("c", 2), item("d", null), item("e", -1)];
    const day = new Date("2026-11-19T10:00:00");
    expect(dueToNotify(xs, new Set(), day).map((x) => x.rev_id)).toEqual(["a", "b"]);
    expect(dueToNotify(xs, new Set(), new Date("2026-11-19T23:00:00"))).toEqual([]);
    expect(dueToNotify(xs, new Set([noticeKey(xs[0])]), day).map((x) => x.rev_id)).toEqual(["b"]);
    // 明天截止弹过了, 到了截止当天 (days_left 变 0) 还会再弹一次
    expect(dueToNotify([item("b", 0)], new Set([noticeKey(item("b", 1))]), day).map((x) => x.rev_id)).toEqual(["b"]);
  });
});
