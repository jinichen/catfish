// @vitest-environment jsdom
/** 学习第 2 步 (11/18): 练习 (交答案 / 提示 / 不做了 / 下一题) + 复习 (看答案 → 四档自评; 选择题直接判) + 删除点两下。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LearnView } from "../../lib/tauri_learn";

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), practice: vi.fn(), attempt: vi.fn(), action: vi.fn(), del: vi.fn(),
  reviewNext: vi.fn(), reviewAnswer: vi.fn(),
}));
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  learnList: api.list, learnGet: api.get, learnPractice: api.practice, learnAttempt: api.attempt, learnAction: api.action,
  learnDelete: api.del, learnReviewNext: api.reviewNext, learnReviewAnswer: api.reviewAnswer,
}));

import { useUIStore } from "../../store/ui";
import LearnTab from "./LearnTab";
import { _clearPracticeDrafts } from "./PracticeParts";
import ReviewPanel from "./ReviewPanel";

const PLAN = { nodes: [{ id: "levels", title: "资质分几级" }, { id: "level-2", title: "二级要求" }], edges: [["levels", "level-2"]] as [string, string][] };
const PROB = { statement: "近三年 2 个项目, 能申请二级吗?", targets: ["level-2"], mode: "recall" as const };

function view(over: Partial<LearnView["session"]> = {}, events: LearnView["events"] = [], running = false): LearnView {
  return {
    running,
    events: [{ id: "e0", at: 1, role: "learner", kind: "say", text: "准备 CCRC 答辩" },
      { id: "e1", at: 2, role: "tutor", kind: "say", text: "二级要近三年 3 个项目。" }, ...events],
    session: { id: "lrn_1", goal: "准备 CCRC 答辩", mode: "quick", phase: "teach", created: 1, updated: 1, plan: PLAN, pending: null,
      last_error: null, sources: [], stalled: false, ...over },
  };
}
const STATS = { total: 3, new: 0, wrong: 3, due_today: 2, reviewed_today: 0, mastered: 0 };
const PREV = { again: "10 分钟", hard: "1 天", good: "4 天", easy: "14 天" };

async function mount(el = <LearnTab />) {
  await act(async () => { render(el); });
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  _clearPracticeDrafts();
  api.list.mockResolvedValue([{ id: "lrn_1", goal: "准备 CCRC 答辩", mode: "quick", phase: "teach", created: 1, updated: 1700000000, nodes: 2, visited: 1 }]);
  api.reviewNext.mockResolvedValue({ questions: [], stats: { ...STATS, due_today: 0 }, previews: {} });
});
afterEach(cleanup);

describe("练习", () => {
  it("讲解中点「去练习」; 做题: 交答案带想法、要提示、不做了", async () => {
    api.get.mockResolvedValue(view({ phase: "practice", problem: { pid: "p1" } },
      [{ id: "e2", at: 3, role: "tutor", kind: "problem", pid: "p1", p: PROB },
       { id: "e3", at: 4, role: "tutor", kind: "hint", pid: "p1", rung: "R1", text: "想想数量要求" }]));
    api.attempt.mockResolvedValue(view({ phase: "practice", problem: { pid: "p1" } }, [], true));
    api.action.mockResolvedValue(view({ phase: "practice" }, [], true));
    await mount();
    expect(screen.getByText(/想想数量要求/)).toBeTruthy();
    expect(screen.getByText(/第 1 级 · 指方向/)).toBeTruthy();
    expect(screen.getByText(/要提示 \(已用 1 级\)/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("你的答案"), { target: { value: "不能, 要 3 个" } });
    fireEvent.change(screen.getByLabelText("练习想法"), { target: { value: "看了提示" } });
    await act(async () => { fireEvent.click(screen.getByText("交答案")); });
    expect(api.attempt).toHaveBeenCalledWith("lrn_1", "不能, 要 3 个", "看了提示");
  });

  it("要提示 / 不做了 / 回去接着学 走对应的操作", async () => {
    api.get.mockResolvedValue(view({ phase: "practice", problem: { pid: "p1" } }, [{ id: "e2", at: 3, role: "tutor", kind: "problem", pid: "p1", p: PROB }]));
    api.action.mockResolvedValue(view({ phase: "practice", problem: { pid: "p1" } }));
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("要提示")); });
    await act(async () => { fireEvent.click(screen.getByText("不做了, 看解法")); });
    await act(async () => { fireEvent.click(screen.getByText("回去接着学")); });
    expect(api.action.mock.calls.map((c) => c[1])).toEqual(["hint", "give_up", "teach"]);
  });

  it("做完一题: 显示结果 / 原因 / 标准解法, 点「下一题」才出下一题; 顶上有练习统计", async () => {
    api.get.mockResolvedValue(view({ phase: "practice", problem: null,
      practice: { total: 1, solved: 0, solvedWithHints: 0, movedOn: 1, misses: { 跟另一条搞混: 1 } } }, [
      { id: "e2", at: 3, role: "tutor", kind: "problem", pid: "p1", p: { ...PROB, solution: "二级要 3 个 → 不能" } },
      { id: "e3", at: 4, role: "tutor", kind: "conclude", pid: "p1", outcome: "movedOn", verdict: "数量记成一级的了", rung: "R2",
        miss: "discrimination", retest: true },
    ]));
    api.practice.mockResolvedValue(view({ phase: "practice" }, [], true));
    await mount();
    expect(screen.getByText("✗ 这题没做对, 先过")).toBeTruthy();
    expect(screen.getByText("原因: 跟另一条搞混")).toBeTruthy();
    expect(screen.getByText(/考的是: 二级要求/)).toBeTruthy();
    expect(screen.getByText("二级要 3 个 → 不能")).toBeTruthy();
    expect(screen.getByText(/练了 1 题: 自己做对 0 · 靠提示 0 · 没做对 1 \(原因: 跟另一条搞混 1\)/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("下一题")); });
    expect(api.practice).toHaveBeenCalledWith("lrn_1", true);
  });

  it("讲解中: 去练习 / 做成复习卡; 删除要点两下 (WebView 里 confirm 不弹)", async () => {
    api.get.mockResolvedValue(view({ cards: 3 }));
    api.practice.mockResolvedValue(view({ cards: 3 }));
    api.action.mockResolvedValue(view({ cards: 3 }));
    api.del.mockResolvedValue({ ok: true });
    await mount();
    await act(async () => { fireEvent.click(screen.getByText(/做成复习卡 \(已有 3\)/)); });
    expect(api.action).toHaveBeenCalledWith("lrn_1", "cards");
    await act(async () => { fireEvent.click(screen.getByText("去练习")); });
    expect(api.practice).toHaveBeenCalledWith("lrn_1");
    await act(async () => { fireEvent.click(screen.getByText("删除")); });
    expect(api.del).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByText("再点一次确认删除")); });
    expect(api.del).toHaveBeenCalledWith("lrn_1");
  });
});

describe("复习", () => {
  it("简答卡: 先想 → 看答案 → 四档自评 (按钮上写下次哪天) → 下一张", async () => {
    api.reviewNext.mockResolvedValueOnce({ questions: [{ id: 7, type: "short_answer", question: "二级几个项目?", options: {}, tags: ["CCRC"] }],
      stats: STATS, previews: { "7": PREV } }).mockResolvedValue({ questions: [], stats: { ...STATS, due_today: 0 }, previews: {} });
    api.reviewAnswer.mockResolvedValueOnce({ needs_rating: true, answer: "近三年 3 个", explanation: "二级项目数", source: "学习: 准备 CCRC 答辩",
      previews: PREV }).mockResolvedValue({ interval_days: 4 });
    const changed = vi.fn();
    await mount(<ReviewPanel onChanged={changed} />);
    fireEvent.change(screen.getByLabelText("先自己想"), { target: { value: "三个" } });
    await act(async () => { fireEvent.click(screen.getByText("看答案")); });
    expect(api.reviewAnswer).toHaveBeenCalledWith(7, "三个");
    expect(screen.getByText("近三年 3 个")).toBeTruthy();
    expect(screen.getByText("14 天")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("想起来了")); });
    expect(api.reviewAnswer).toHaveBeenLastCalledWith(7, "三个", "good");
    expect(changed).toHaveBeenCalled();
    expect(screen.getByText(/今天的复习做完了/)).toBeTruthy();
  });

  it("题库里的选择题: 选了提交直接判, 答错给答案", async () => {
    api.reviewNext.mockResolvedValue({ questions: [{ id: 3, type: "single_choice", question: "季度预算谁批?", options: { A: "王芳", B: "李雷" }, tags: [] }],
      stats: STATS, previews: {} });
    api.reviewAnswer.mockResolvedValue({ correct: false, answer: "A", next_review: "2026-11-19" });
    await mount(<ReviewPanel />);
    fireEvent.click(screen.getByLabelText(/李雷/));
    await act(async () => { fireEvent.click(screen.getByText("提交")); });
    expect(api.reviewAnswer).toHaveBeenCalledWith(3, "B");
    expect(screen.getByText("✗ 不对, 答案是 A")).toBeTruthy();
    expect(screen.getByText("下次: 2026-11-19")).toBeTruthy();
  });

  it("早安点「开始复习」→ 学习页直接打开复习; 左边显示今天要复习几条", async () => {
    api.reviewNext.mockResolvedValue({ questions: [], stats: STATS, previews: {} });
    api.get.mockResolvedValue(view());
    useUIStore.getState().openLearnReview();
    await mount();
    expect(screen.getByText("今天要复习 2 条")).toBeTruthy();
    expect(screen.getByLabelText("复习")).toBeTruthy();
    expect(useUIStore.getState().pendingLearnReview).toBe(false);
  });
});
