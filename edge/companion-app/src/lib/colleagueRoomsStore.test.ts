/** 同事群聊的前端状态 (11/5): 通知、红点。数据全是虚构的。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sync = vi.fn();
const list = vi.fn();
const notify = vi.fn(() => Promise.resolve());

vi.mock("./tauri_colleague_rooms", () => ({ roomSync: () => sync(), roomList: () => list() }));
vi.mock("./tauri_app", () => ({ sendNotification: (t: string, b: string) => notify(t, b) }));

import { __resetRoomsStore, getSnapshot, notifyFor, pendingRoomCount, refreshRooms } from "./colleagueRoomsStore";
import type { RoomEvent, RoomSummary } from "./tauri_colleague_rooms";

const room = (my_state: RoomSummary["my_state"], unread = 0): RoomSummary => ({
  room_id: `r_${Math.random().toString(16).slice(2, 10)}`, name: "华东项目群", purpose: "", owner: "zhou@example.com",
  my_state, members: 3, joined: 2, unread, mentioned: 0, last: null, activity: "",
});
const ev = (kind: RoomEvent["kind"], room_id = "r1", joined = true): RoomEvent =>
  ({ kind, room_id, from: "zhang@example.com", title: "华东项目群", text: "周五交周报", joined });

beforeEach(() => {
  __resetRoomsStore();
  sync.mockReset();
  list.mockReset();
  notify.mockClear();
});
afterEach(() => __resetRoomsStore());

describe("colleagueRoomsStore", () => {
  it("邀请 / @我 / 新消息 / 被移出 / 解散发通知, 加入和退群不打扰", () => {
    notifyFor(["invite", "mention", "msg", "removed", "disband", "join", "leave", "member", "declined"].map((k) => ev(k as RoomEvent["kind"])), null);
    expect(notify.mock.calls.map((c) => c[0])).toEqual([
      "同事拉你进群", "「华东项目群」里有人@你", "「华东项目群」有新消息", "你被移出了群", "群解散了",
    ]);
  });

  it("正开着的群、还没加入的群: 消息不弹通知", () => {
    notifyFor([ev("msg", "r1"), ev("mention", "r1"), ev("msg", "r2", false), ev("msg", "r3")], "r1");
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it("红点 = 等我加入的群 + 已加入群的未读; 退了的不算", () => {
    expect(pendingRoomCount({ me: "", loaded: true, syncError: null, version: 0,
      rooms: [room("invited"), room("joined", 3), room("left", 5), room("disbanded", 2)] })).toBe(4);
  });

  it("取邮筒失败照样刷列表, 有新东西 version +1", async () => {
    sync.mockRejectedValueOnce(new Error("中央连不上"));
    list.mockResolvedValue({ me: "li@example.com", rooms: [room("joined", 1)] });
    await refreshRooms();
    expect(getSnapshot()).toMatchObject({ loaded: true, syncError: "中央连不上", version: 0 });
    sync.mockResolvedValueOnce({ events: [ev("msg")], gap: false });
    await refreshRooms();
    expect(getSnapshot()).toMatchObject({ syncError: null, version: 1 });
  });
});
