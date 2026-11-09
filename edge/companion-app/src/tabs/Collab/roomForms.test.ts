/** 群里发表的小工具 (11/09)。邮箱都是 example.com。 */
import { describe, expect, it } from "vitest";

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import { formStates, hiddenFormStatus } from "./roomForms";

const M = (email: string, name: string): RoomMember => ({ email, name, state: "joined", owner: false, me: false });
const members = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三"), M("li@example.com", "")];
const st = (from: string, state: "submitted" | "declined" | "closed", form_id = "f_20261109_aaaaaaaa"): RoomMessage =>
  ({ msg_id: `m_${from}_${state}`, kind: "form_status", from, at: "2026-11-09T10:00:00", text: "", form_status: { form_id, title: "安全检查", state } });

describe("formStates", () => {
  it("每人最近的状态; 没报的 = 还没交; 结束收集单独标", () => {
    const msgs = [st("zhang@example.com", "declined"), st("zhang@example.com", "submitted"), st("li@example.com", "submitted", "f_20261109_bbbbbbbb"),
                  st("zhou@example.com", "closed")];
    const r = formStates("f_20261109_aaaaaaaa", ["zhang@example.com", "li@example.com"], msgs, members);
    expect(r.people).toEqual([
      { email: "zhang@example.com", name: "张三", state: "submitted" },
      { email: "li@example.com", name: "li", state: "waiting" },
    ]);
    expect(r.closed).toBe(true);
  });
  it("表卡在眼前时, 它的状态行藏起来", () => {
    const card: RoomMessage = { msg_id: "m_c", kind: "form", at: "x", text: "请填", from: "zhou@example.com",
      form: { form_id: "f_20261109_aaaaaaaa", title: "安全检查", deadline: null, fields: 2, recipients: [], owner: "zhou@example.com" } };
    expect(hiddenFormStatus([card]).has("f_20261109_aaaaaaaa")).toBe(true);
    expect(hiddenFormStatus([]).size).toBe(0);
  });
});
