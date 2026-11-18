// @vitest-environment jsdom
/** 学习第 4 步 (11/18): 答辩陪练 —— 评审提问自动朗读 (可关)、🎤 录音转文字填进框、交给评审、点评 ✓✗⚠、下一题 / 看总结。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LearnView } from "../../lib/tauri_learn";

const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), drill: vi.fn(), start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), speak: vi.fn() }));
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  learnList: api.list, learnGet: api.get, learnDrill: api.drill,
  speechStart: api.start, speechStopAndTranscribe: api.stop, speechCancel: api.cancel,
  learnReviewNext: vi.fn(async () => ({ questions: [], stats: { total: 0, due_today: 0 }, previews: {} })),
  learnGraphView: vi.fn(async () => ({ points: 0, chains: 0, sources: [], running: null, last: null })),
}));
vi.mock("../../lib/speak", () => ({ speak: api.speak, speechAvailable: () => true, stopSpeaking: vi.fn() }));

import LearnTab from "./LearnTab";

function view(events: LearnView["events"], over: Partial<LearnView["session"]> = {}): LearnView {
  return {
    running: false,
    events: [{ id: "e0", at: 1, role: "learner", kind: "say", text: "CCRC 答辩" }, ...events],
    session: { id: "lrn_1", goal: "CCRC 应急处理二级现场答辩", mode: "defense", phase: "drill", created: 1, updated: 1, plan: null, pending: null,
      last_error: null, sources: [], stalled: false, drill: { idx: 0, total: 2, done: false }, ...over },
  };
}
const Q = { id: "q1", at: 2, role: "tutor" as const, kind: "examiner_q" as const, qi: 0, text: "你们分级标准怎么定的?", total: 2 };

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.list.mockResolvedValue([{ id: "lrn_1", goal: "CCRC 应急处理二级现场答辩", mode: "defense", phase: "drill", created: 1, updated: 1700000000, nodes: 0, visited: 0 }]);
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v),
      removeItem: (k: string) => void data.delete(k), clear: () => data.clear() },
  });
});
afterEach(cleanup);

async function mount() {
  await act(async () => { render(<LearnTab />); });
}

describe("答辩陪练", () => {
  it("新问题自动朗读; 🎤 录音 → 说完了 → 转出的字填进框 → 交给评审 (带说了几秒)", async () => {
    api.get.mockResolvedValue(view([Q]));
    api.stop.mockResolvedValue("我们有正式发布的分级管理办法");
    api.drill.mockResolvedValue(view([Q], {}));
    await mount();
    expect(api.speak).toHaveBeenCalledWith("你们分级标准怎么定的?");
    expect(screen.getByText(/第 1 \/ 2 题/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("🎤 口头回答")); });
    expect(api.start).toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByText("⏹ 说完了")); });
    expect((screen.getByLabelText("你的回答") as HTMLTextAreaElement).value).toBe("我们有正式发布的分级管理办法");
    await act(async () => { fireEvent.click(screen.getByText("交给评审")); });
    const [id, action, text, secs] = api.drill.mock.calls[0];
    expect([id, action, text]).toEqual(["lrn_1", "answer", "我们有正式发布的分级管理办法"]);
    expect(typeof secs).toBe("number");
  });

  it("关掉朗读就不读; 点评显示 ✓ ✗ ⚠ 和评审的话; 点「下一题」", async () => {
    api.get.mockResolvedValue(view([Q,
      { id: "a1", at: 3, role: "learner", kind: "oral_answer", qi: 0, text: "看情况定", secs: 8 },
      { id: "v1", at: 4, role: "tutor", kind: "drill_eval", qi: 0, covered: [0], missed: [1], wrong: [{ said: "看情况定", fix: "按办法里的条款定" }],
        feedback: "漏了报告里写依据", points: ["有分级管理办法", "报告写明依据哪条"], followup: null }]));
    api.drill.mockResolvedValue(view([]));
    await mount();
    expect(api.speak).not.toHaveBeenCalled();            // 最新的不是提问 (已经点评了), 不重读
    expect(screen.getByText("✓ 有分级管理办法")).toBeTruthy();
    expect(screen.getByText("✗ 报告写明依据哪条")).toBeTruthy();
    expect(screen.getByText(/⚠ 你说「看情况定」—— 应该是: 按办法里的条款定/)).toBeTruthy();
    expect(screen.getByText("🎤 说了 8 秒")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/评审提问自动读出来/));
    expect(localStorage.getItem("catfish:learn:drill-voice")).toBe("off");
    await act(async () => { fireEvent.click(screen.getByText("下一题")); });
    expect(api.drill).toHaveBeenCalledWith("lrn_1", "next");
  });

  it("问完: 总结 (讲到几成 / 每题漏了什么 / 做了几张卡), 可以再来一轮", async () => {
    api.get.mockResolvedValue(view([{ id: "s", at: 9, role: "tutor", kind: "drill_summary", score: 3, total: 5, cards: 2,
      items: [{ qi: 0, question: "分级标准怎么定?", covered: 1, total: 2, missed: ["报告写明依据哪条"] },
              { qi: 1, question: "二级几个项目?", covered: 2, total: 3, missed: ["近三年"] }] }],
      { drill: { idx: 2, total: 2, done: true } }));
    api.drill.mockResolvedValue(view([], { drill: null }));
    await mount();
    expect(screen.getByText(/评分点讲到 3 \/ 5 \(60%\)/)).toBeTruthy();
    expect(screen.getByText("漏了: 报告写明依据哪条")).toBeTruthy();
    expect(screen.getByText(/漏掉的点做成了 2 张复习卡/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("🎤 再来一轮")); });
    expect(api.drill).toHaveBeenCalledWith("lrn_1", "start");
  });
});
