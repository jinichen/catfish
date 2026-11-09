// @vitest-environment jsdom
/** 群里发的表 (11/09): 表卡下面每人状态 + 该点的按钮。邮箱都是 example.com。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/colleagueRoomsStore", () => ({ refreshRooms: vi.fn(), setActiveRoom: vi.fn(), useColleagueRooms: () => ({}) }));
vi.mock("./RoomsDelivery", () => ({ DeliveryLine: () => null }));

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import RoomFormMessage from "./RoomFormMessage";

const M = (email: string, name: string): RoomMember => ({ email, name, state: "joined", owner: false, me: false });
const members = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三"), M("li@example.com", "李四")];
const card: RoomMessage = { msg_id: "m_c", kind: "form", from: "zhou@example.com", at: "2026-11-09T10:00:00", text: "📋 请填《10 月安全检查》(2 项, 11/24 截止)",
  form: { form_id: "f_20261109_aaaaaaaa", title: "10 月安全检查", deadline: "2026-11-24", fields: 2, recipients: ["zhang@example.com", "li@example.com"], owner: "zhou@example.com" } };
const zhangDone: RoomMessage = { msg_id: "m_s", kind: "form_status", from: "zhang@example.com", at: "2026-11-09T11:00:00", text: "张三 交了《10 月安全检查》",
  form_status: { form_id: "f_20261109_aaaaaaaa", title: "10 月安全检查", state: "submitted" } };

afterEach(cleanup);

describe("RoomFormMessage", () => {
  it("收件人没交: 「去填」; 别人的状态也列出来", () => {
    const onFill = vi.fn();
    render(<RoomFormMessage m={card} me="li@example.com" members={members} messages={[card, zhangDone]} roomId="r1" onFill={onFill} onWatch={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText(/已交 1\/2/)).toBeTruthy();
    expect(screen.getByText("张三 已交")).toBeTruthy();
    expect(screen.getByText("我 还没交")).toBeTruthy();
    fireEvent.click(screen.getByText("去填"));
    expect(onFill).toHaveBeenCalledWith("f_20261109_aaaaaaaa");
    expect(screen.queryByText("看进度")).toBeNull();
  });
  it("交过的看自己填的; 发起人看进度; 结束收集后不再有「去填」", () => {
    const onWatch = vi.fn();
    render(<RoomFormMessage m={card} me="zhang@example.com" members={members} messages={[card, zhangDone]} roomId="r1" onFill={vi.fn()} onWatch={onWatch} onChanged={vi.fn()} />);
    expect(screen.getByText("看我填的")).toBeTruthy();
    cleanup();
    render(<RoomFormMessage m={card} me="zhou@example.com" members={members} messages={[card, zhangDone]} roomId="r1" onFill={vi.fn()} onWatch={onWatch} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("看进度"));
    expect(onWatch).toHaveBeenCalledWith("f_20261109_aaaaaaaa");
    cleanup();
    const closed: RoomMessage = { ...zhangDone, msg_id: "m_x", from: "zhou@example.com", text: "周总 结束收集了", form_status: { ...zhangDone.form_status!, state: "closed" } };
    render(<RoomFormMessage m={card} me="li@example.com" members={members} messages={[card, zhangDone, closed]} roomId="r1" onFill={vi.fn()} onWatch={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText(/已结束收集/)).toBeTruthy();
    expect(screen.queryByText("去填")).toBeNull();
  });
});
