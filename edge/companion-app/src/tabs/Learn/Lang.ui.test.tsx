// @vitest-environment jsdom
/** 学习第 5 步 (11/18): 外语 —— 选练法 / 对话 (台词朗读、看中文、纠错、🎤 按语言转写、发送、结束复盘) / 跟读 (逐词标红) / 复盘。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LearnView } from "../../lib/tauri_learn";

const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), lang: vi.fn(), start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), speak: vi.fn(),
  createLang: vi.fn() }));
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  learnList: api.list, learnGet: api.get, learnLang: api.lang, learnCreateLang: api.createLang,
  speechStart: api.start, speechStopAndTranscribeLang: api.stop, speechCancel: api.cancel,
  learnReviewNext: vi.fn(async () => ({ questions: [], stats: { total: 0, due_today: 0 }, previews: {} })),
  learnGraphView: vi.fn(async () => ({ points: 0, chains: 0, sources: [], running: null, last: null })),
}));
vi.mock("../../lib/speak", () => ({ speak: api.speak, speechAvailable: () => true, stopSpeaking: vi.fn() }));

import LearnTab from "./LearnTab";

function view(events: LearnView["events"], lang: NonNullable<LearnView["session"]["lang"]>): LearnView {
  return {
    running: false,
    events: [{ id: "e0", at: 1, role: "learner", kind: "say", text: "商务英语" }, ...events],
    session: { id: "lrn_1", goal: "商务英语: 跟客户开会", mode: "language", phase: "lang", created: 1, updated: 1, plan: null, pending: null,
      last_error: null, sources: [], stalled: false, lang },
  };
}
const LINE = { id: "l1", at: 2, role: "tutor" as const, kind: "lang_line" as const, who: "role" as const, text: "Can we confirm the date?", zh: "能确认日期吗?" };

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.list.mockResolvedValue([{ id: "lrn_1", goal: "商务英语: 跟客户开会", mode: "language", phase: "lang", created: 1, updated: 1700000000, nodes: 0, visited: 0 }]);
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", { configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), removeItem: () => undefined } });
});
afterEach(cleanup);

async function mount() {
  await act(async () => { render(<LearnTab />); });
}

describe("外语", () => {
  it("还没选练法: 填情景, 点情景对话", async () => {
    api.get.mockResolvedValue(view([], { code: "en", activity: null, ending: false }));
    api.lang.mockResolvedValue(view([], { code: "en", activity: "dialogue", ending: false }));
    await mount();
    fireEvent.change(screen.getByLabelText("情景"), { target: { value: "确认交付时间" } });
    await act(async () => { fireEvent.click(screen.getByText("💬 情景对话")); });
    expect(api.lang).toHaveBeenCalledWith("lrn_1", "start", { activity: "dialogue", scene: "确认交付时间" });
  });

  it("对话: 新台词用英语声音读; 看中文; 纠错; 🎤 按英语转写后发送; 结束复盘", async () => {
    api.get.mockResolvedValue(view([
      { id: "s", at: 1, role: "tutor", kind: "lang_scene", activity: "dialogue", setting: "视频会议", role_name: "客户 Tom" },
      { id: "f", at: 2, role: "tutor", kind: "lang_fix", items: [{ said: "at next Friday", better: "by next Friday", why: "截止用 by" }] },
      LINE], { code: "en", activity: "dialogue", ending: false }));
    api.stop.mockResolvedValue("We can deliver by Friday");
    api.lang.mockResolvedValue(view([LINE], { code: "en", activity: "dialogue", ending: false }));
    await mount();
    expect(api.speak).toHaveBeenCalledWith("Can we confirm the date?", 0.95, "en-US");
    fireEvent.click(screen.getByText("看中文"));
    expect(screen.getByText("能确认日期吗?")).toBeTruthy();
    expect(screen.getByText("by next Friday")).toBeTruthy();
    expect(screen.getByText(/小鲶扮: 客户 Tom/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("🎤 说")); });
    await act(async () => { fireEvent.click(screen.getByText("⏹ 说完了")); });
    expect(api.stop).toHaveBeenCalledWith("en");
    await act(async () => { fireEvent.click(screen.getByText("发送")); });
    expect(api.lang.mock.calls[0][1]).toBe("say");
    expect(api.lang.mock.calls[0][2].text).toBe("We can deliver by Friday");
  });

  it("跟读: 没对上的词标红, 显示对上几成; 读完复盘", async () => {
    api.get.mockResolvedValue(view([
      { id: "set", at: 1, role: "tutor", kind: "shadow_set", sentences: [{ text: "We isolate the server first.", zh: "先隔离服务器", note: "" },
        { text: "Then we collect logs.", zh: "再收集日志", note: "logs 别读成 locks" }] },
      { id: "r0", at: 2, role: "learner", kind: "shadow_result", idx: 0, target: "We isolate the server first.", heard: "we isolate server first",
        missed: ["the"], extra: [], score: 0.8 }], { code: "en", activity: "shadow", ending: false }));
    api.lang.mockResolvedValue(view([], { code: "en", activity: null, ending: true }));
    await mount();
    expect(screen.getByText(/对上 80% · 没听出来: the/)).toBeTruthy();
    const the = screen.getByText("the");
    expect(the.getAttribute("style")).toContain("underline");
    expect(screen.getByText(/logs 别读成 locks/)).toBeTruthy();
    expect(screen.getByText(/读了 1 句, 平均对上 80%/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("读完了, 复盘")); });
    expect(api.lang).toHaveBeenCalledWith("lrn_1", "end");
  });

  it("新建外语学习: 选语言", async () => {
    api.list.mockResolvedValue([]);
    api.createLang.mockResolvedValue(view([], { code: "ja", activity: null, ending: false }));
    api.get.mockResolvedValue(view([], { code: "ja", activity: null, ending: false }));
    await mount();
    fireEvent.click(screen.getByText("外语"));
    fireEvent.change(screen.getByLabelText("语言"), { target: { value: "ja" } });
    fireEvent.change(screen.getByLabelText("想学会什么"), { target: { value: "日语: 接待客户" } });
    await act(async () => { fireEvent.click(screen.getByText("开始")); });
    expect(api.createLang).toHaveBeenCalledWith("日语: 接待客户", "ja");
  });
});
