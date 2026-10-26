import { beforeEach, describe, expect, it, vi } from "vitest";

const toolBridgeChatApproval = vi.fn();
vi.mock("../lib/tauri", () => ({
  toolBridgeChatApproval: (...a: unknown[]) => toolBridgeChatApproval(...a),
  sendNotification: vi.fn(() => Promise.resolve()),
  hermesApprovalTimeout: vi.fn(() => Promise.resolve(1800)),
}));

import {
  addItem, belongsTo, decideApproval, expireItems, itemFromDetail, receiveApproval, resetApprovalsForTest,
  retryApprovalNote, retryNote, useApprovalsStore, type ApprovalsState,
} from "./approvals";

const empty: ApprovalsState = { items: [], notes: [], submitting: {}, preapprovals: [] };
const detail = (key: string, session: string | null, command = "rm -f /tmp/a.xlsx") => ({
  approval_session_key: key, command, description: "delete", pattern_key: "rm", timeout_s: 1800, session_id: session,
});

describe("等批准 —— 几段对话各管各的 (10/26)", () => {
  beforeEach(() => {
    resetApprovalsForTest();
    toolBridgeChatApproval.mockReset().mockResolvedValue({});
  });

  it("两段对话同时等批准: 后来的不盖掉先来的", () => {
    const now = 1_000;
    let s = addItem(empty, itemFromDetail(detail("run-a", "A"), now), now).state;
    s = addItem(s, itemFromDetail(detail("run-b", "B"), now), now).state;
    expect(s.items.map((i) => i.key)).toEqual(["run-a", "run-b"]);
    // 同一个 key 重发就替换
    s = addItem(s, itemFromDetail(detail("run-a", "A", "rm -f /tmp/b"), now), now).state;
    expect(s.items.map((i) => `${i.key}:${i.command}`)).toEqual(["run-b:rm -f /tmp/a.xlsx", "run-a:rm -f /tmp/b"]);
    expect(s.items.length).toBe(2);
  });

  it("按会话显示; 不知道是哪段的 (老插件) 哪里都显", () => {
    expect(belongsTo("A", "A")).toBe(true);
    expect(belongsTo("A", "B")).toBe(false);
    expect(belongsTo(null, "B")).toBe(true);
    expect(belongsTo("A", null)).toBe(false);
  });

  it("各自到点各自作废, 作废的留一句带命令的话 (可以批准并继续)", () => {
    const now = 1_000;
    let s = addItem(empty, itemFromDetail(detail("run-a", "A"), now), now).state;
    s = addItem(s, itemFromDetail({ ...detail("run-b", "B"), timeout_s: 60 }, now), now).state;
    s = expireItems(s, now + 61_000);
    expect(s.items.map((i) => i.key)).toEqual(["run-a"]);
    expect(s.notes).toHaveLength(1);
    expect(s.notes[0]).toMatchObject({ sessionId: "B", command: "rm -f /tmp/a.xlsx" });
    expect(s.notes[0].text).toContain("1 分钟");
    expect(expireItems(s, now + 61_000)).toBe(s);
  });

  it("批准并继续: 记下这段对话的预先批准; 重做时同一条命令直接批, 别的对话 / 别的命令照常问", () => {
    const now = 1_000;
    let s = expireItems(addItem(empty, itemFromDetail(detail("run-a", "A"), now), now).state, now + 1_801_000);
    const r = retryNote(s, s.notes[0].id, now + 1_802_000);
    expect(r.prompt).toContain("现在我批准");
    s = r.state;
    expect(s.notes).toHaveLength(0);
    const t = now + 1_803_000;
    expect(addItem(s, itemFromDetail(detail("run-x", "B"), t), t).preapproved).toBe(false);
    expect(addItem(s, itemFromDetail(detail("run-x", "A", "rm -rf /"), t), t).preapproved).toBe(false);
    const hit = addItem(s, itemFromDetail(detail("run-a2", "A"), t), t);
    expect(hit.preapproved).toBe(true);
    expect(hit.state.items).toHaveLength(0);
    expect(addItem(hit.state, itemFromDetail(detail("run-a3", "A"), t), t).preapproved).toBe(false);
  });

  it("预先批准真的送回执 (once), 不进横幅", async () => {
    receiveApproval(detail("run-a", "A"));
    useApprovalsStore.setState(expireItems(useApprovalsStore.getState(), Date.now() + 1_801_000));
    const note = useApprovalsStore.getState().notes[0];
    expect(retryApprovalNote(note.id)).toContain("rm -f /tmp/a.xlsx");
    receiveApproval(detail("run-a2", "A"));
    expect(useApprovalsStore.getState().items).toHaveLength(0);
    expect(toolBridgeChatApproval).toHaveBeenCalledWith("run-a2", "once");
  });

  it("点晚了 (409): 收起横幅, 留一句可以批准并继续的话; 拒绝失败不给", async () => {
    toolBridgeChatApproval.mockRejectedValueOnce(new Error("chat_approval HTTP 409: resolved"));
    receiveApproval(detail("run-a", "A"));
    await decideApproval("run-a", "once");
    const st = useApprovalsStore.getState();
    expect(st.items).toHaveLength(0);
    expect(st.notes[0]).toMatchObject({ sessionId: "A", command: "rm -f /tmp/a.xlsx" });

    resetApprovalsForTest();
    toolBridgeChatApproval.mockRejectedValueOnce(new Error("chat_approval HTTP 409: resolved"));
    receiveApproval(detail("run-b", "B"));
    await decideApproval("run-b", "deny");
    expect(useApprovalsStore.getState().notes[0].command).toBeNull();
  });
});
