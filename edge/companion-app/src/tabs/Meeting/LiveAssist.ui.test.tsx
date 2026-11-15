// @vitest-environment jsdom
/** 会中小鲶 (11/15): 实时文字 / 问小鲶 / 主动提示 / 会后列表。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  meetingLiveRead: vi.fn(),
  meetingLiveAsk: vi.fn(),
  meetingLiveHint: vi.fn(),
  meetingLiveNotes: vi.fn(),
  meetingLiveNoteSet: vi.fn(),
  meetingLiveNoteDelete: vi.fn(),
  meetingLiveHintsSet: vi.fn(),
}));
vi.mock("../../lib/tauri_meeting_live", async (orig) => ({ ...(await orig<object>()), ...api }));

import { clock, liveStatusText, quoteLine } from "../../lib/tauri_meeting_live";
import type { MeetingMeta } from "../../lib/tauri_meeting";
import LiveAssist, { HINT_EVERY_MS, LiveNotes } from "./LiveAssist";

const META: MeetingMeta = { id: "mtg_1", title: "CC RC末次", created_at: "2026-11-15T19:00:00", attendees: 3, hotwords: [],
                            status: "recording", duration_secs: 0, device: null, error: null };
const L1 = { start: 200, end: 206, text: "严重不符合项要先发暂停通知。", track: "mic" as const };
const ASK = { id: "a1", kind: "ask" as const, q: "暂停通知后多久交恢复申请?", answer: "03:20 说的: 下个月前。", state: "new" as const, at: 206, created: 1,
             sources: [{ kind: "邮件", title: "关于现场评审会安排的通知", where: "m1" }] };
const HINT = { id: "h1", kind: "hint" as const, from: 180, at: 360, created: 2,
               items: [{ id: "i1", type: "todo" as const, text: "下个月前交恢复申请", state: "new" as const },
                       { id: "i2", type: "conflict" as const, text: "引用的是 07 版旧标准", state: "new" as const }] };

beforeEach(() => {
  api.meetingLiveRead.mockResolvedValue({ lines: [L1], total: 1, state: { status: "running", lag_secs: 3, partial: "然后呢" } });
  api.meetingLiveNotes.mockResolvedValue({ notes: [], hint_cursor: 0 });
  api.meetingLiveHintsSet.mockResolvedValue(META);
});
afterEach(() => { cleanup(); vi.useRealTimers(); Object.values(api).forEach((f) => f.mockReset()); });

describe("helpers", () => {
  it("状态一句话 / 时间 / 引一句", () => {
    expect(liveStatusText({ status: "running", lag_secs: 3, partial: "" }, 5, 60)).toBeNull();
    expect(liveStatusText({ status: "lagging", lag_secs: 75.4, partial: "" }, 5, 60)).toMatch(/落后约 75 秒/);
    expect(liveStatusText(null, 0, 60)).toMatch(/会议组件包还没装好/);
    expect(liveStatusText(null, 0, 5)).toMatch(/准备中/);
    expect(liveStatusText({ status: "error", lag_secs: 0, partial: "", message: "模型不全" }, 0, 60)).toMatch(/模型不全.*录音不受影响/);
    expect(clock(3725)).toBe("1:02:05");
    expect(quoteLine(L1)).toBe("关于 [03:20]「严重不符合项要先发暂停通知。」: ");
  });
});

describe("LiveAssist (录音中)", () => {
  it("显示实时文字和半句; 点一句填进问题框; 问了出问答卡, 能记到纪要", async () => {
    api.meetingLiveAsk.mockResolvedValue({ note: ASK });
    api.meetingLiveNoteSet.mockResolvedValue({ notes: [{ ...ASK, state: "pinned" }], hint_cursor: 0 });
    render(<LiveAssist meta={META} />);
    await act(async () => {});
    expect(screen.getByText("严重不符合项要先发暂停通知。")).toBeTruthy();
    expect(screen.getByText("然后呢…")).toBeTruthy();
    fireEvent.click(screen.getByText("严重不符合项要先发暂停通知。"));
    const box = screen.getByPlaceholderText(/问小鲶/) as HTMLTextAreaElement;
    expect(box.value).toBe("关于 [03:20]「严重不符合项要先发暂停通知。」: ");
    fireEvent.change(box, { target: { value: "暂停通知后多久交恢复申请?" } });
    await act(async () => { fireEvent.click(screen.getByText("🐟 问小鲶")); });
    expect(api.meetingLiveAsk).toHaveBeenCalledWith("mtg_1", "暂停通知后多久交恢复申请?");
    expect(screen.getByText("03:20 说的: 下个月前。")).toBeTruthy();
    expect(screen.getByText("依据 (你电脑上的): 邮件「关于现场评审会安排的通知」")).toBeTruthy();
    expect(box.value).toBe("");
    await act(async () => { fireEvent.click(screen.getByText("记到纪要")); });
    expect(api.meetingLiveNoteSet).toHaveBeenCalledWith("mtg_1", "a1", null, "pinned");
    expect(screen.getByText("✓ 已记到纪要")).toBeTruthy();
  });

  it("主动提示: 每 3 分钟看一次, 有东西才出卡; 关掉就不看", async () => {
    vi.useFakeTimers();
    api.meetingLiveHint.mockResolvedValueOnce({ note: null, skipped: "新说的话还不多" }).mockResolvedValueOnce({ note: HINT });
    render(<LiveAssist meta={META} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(HINT_EVERY_MS); });
    expect(api.meetingLiveHint).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("live-note-hint")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(HINT_EVERY_MS); });
    expect(screen.getByText("下个月前交恢复申请")).toBeTruthy();
    expect(screen.getByText("跟资料对不上")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(api.meetingLiveHintsSet).toHaveBeenCalledWith("mtg_1", false);
    await act(async () => { await vi.advanceTimersByTimeAsync(HINT_EVERY_MS * 2); });
    expect(api.meetingLiveHint).toHaveBeenCalledTimes(2);
  });
});

describe("LiveNotes (会后)", () => {
  it("没有就不显示; 有就能展开、删一条, 删了通知纪要重读", async () => {
    api.meetingLiveNotes.mockResolvedValueOnce({ notes: [], hint_cursor: 0 });
    const { container, unmount } = render(<LiveNotes meetingId="mtg_1" />);
    await act(async () => {});
    expect(container.textContent).toBe("");
    unmount();
    api.meetingLiveNotes.mockResolvedValue({ notes: [ASK, { ...HINT, items: [{ ...HINT.items[0], state: "pinned" }, HINT.items[1]] }], hint_cursor: 0 });
    api.meetingLiveNoteDelete.mockResolvedValue({ notes: [HINT], hint_cursor: 0 });
    const onChanged = vi.fn();
    render(<LiveNotes meetingId="mtg_1" onChanged={onChanged} />);
    await act(async () => {});
    fireEvent.click(screen.getByText(/会中小鲶 \(2 张卡, 1 条记到纪要\)/));
    await act(async () => { fireEvent.click(screen.getAllByText("删").at(-1)!); });   // 新的在上: 提示卡在前, 问答卡在后
    expect(api.meetingLiveNoteDelete).toHaveBeenCalledWith("mtg_1", "a1", null);
    expect(onChanged).toHaveBeenCalled();
  });
});
