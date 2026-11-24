// @vitest-environment jsdom
/** 学习页 (11/18): 新建 / 答题 (把握 + 想法) / 答过的显示对错 / 路线图「开始学」/ 后台在跑时轮询 / 出错重试 / 草稿切走不丢。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LearnView } from "../../lib/tauri_learn";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  answer: vi.fn(),
  say: vi.fn(),
  retry: vi.fn(),
  del: vi.fn(),
}));
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  learnList: api.list, learnGet: api.get, learnCreate: api.create, learnAnswer: api.answer,
  learnSay: api.say, learnRetry: api.retry, learnDelete: api.del,
}));

import LearnTab from "./LearnTab";
import PlanMap, { planLayers } from "./PlanMap";
import { _clearDrafts } from "./QuestionCard";
import { _clearSayDrafts } from "./LearnSession";

const PLAN = { nodes: [{ id: "levels", title: "资质分几级" }, { id: "level-2", title: "二级要求" }, { id: "goal", title: "答辩" }],
  edges: [["levels", "level-2"], ["level-2", "goal"]] as [string, string][] };
const Q = { type: "choice" as const, role: "probe" as const, question: "二级要几个项目?", options: [{ value: "a", label: "1 个" }, { value: "b", label: "3 个" }] };

function view(over: Partial<LearnView["session"]> = {}, events: LearnView["events"] = [], running = false): LearnView {
  return {
    running,
    events: [{ id: "e0", at: 1, role: "learner", kind: "say", text: "准备 CCRC 答辩" }, ...events],
    session: { id: "lrn_1", goal: "准备 CCRC 答辩", mode: "quick", phase: "probe", created: 1, updated: 1, plan: null, pending: null,
      last_error: null, sources: [], stalled: false, ...over },
  };
}
const SUMMARY = { id: "lrn_1", goal: "准备 CCRC 答辩", mode: "quick", phase: "probe", created: 1, updated: 1700000000, nodes: 0, visited: 0 };

async function mount() {
  await act(async () => { render(<LearnTab />); });
}

beforeEach(() => {
  vi.useRealTimers();
  Object.values(api).forEach((f) => f.mockReset());
  _clearDrafts();
  _clearSayDrafts();
  api.list.mockResolvedValue([SUMMARY]);
});
afterEach(cleanup);

describe("学习页", () => {
  it("新建: 写目标选模式点开始, 选中新的那个", async () => {
    api.list.mockResolvedValueOnce([]).mockResolvedValue([SUMMARY]);
    api.create.mockResolvedValue(view({}, [], true));
    api.get.mockResolvedValue(view({}, [], true));
    await mount();
    fireEvent.change(screen.getByLabelText("想学会什么"), { target: { value: "准备 CCRC 答辩" } });
    fireEvent.click(screen.getByText("深入"));
    await act(async () => { fireEvent.click(screen.getByText(/开始 \(小鲶先问几道题/)); });
    expect(api.create).toHaveBeenCalledWith("准备 CCRC 答辩", "deep");
    expect(screen.getByText(/小鲶在想/)).toBeTruthy();
  });

  it("例子按钮只给还没学过的人看; 学过以后不占地方", async () => {
    api.list.mockResolvedValue([]);
    await mount();
    expect(screen.getByText("公司的差旅报销规定")).toBeTruthy();
    cleanup();
    api.list.mockResolvedValue([SUMMARY]);
    api.get.mockResolvedValue(view());
    await mount();
    expect(screen.queryByText("公司的差旅报销规定")).toBeNull();
  });

  it("答题: 选项 + 把握 + 想法一起交; 我不知道单独交", async () => {
    api.get.mockResolvedValue(view({ pending: { qid: "q1" } }, [{ id: "e1", at: 2, role: "tutor", kind: "question", qid: "q1", q: Q }]));
    api.answer.mockResolvedValue(view({}, [], true));
    await mount();
    fireEvent.click(screen.getByLabelText(/3 个/));
    fireEvent.change(screen.getByLabelText("把握"), { target: { value: "70" } });
    fireEvent.change(screen.getByLabelText("你怎么想的"), { target: { value: "好像是三个" } });
    await act(async () => { fireEvent.click(screen.getByText("提交")); });
    expect(api.answer).toHaveBeenCalledWith("lrn_1", { qid: "q1", picked: ["b"], confidence: 70, thinking: "好像是三个" });
  });

  it("草稿切走再回来还在", async () => {
    api.get.mockResolvedValue(view({ pending: { qid: "q1" } }, [{ id: "e1", at: 2, role: "tutor", kind: "question", qid: "q1", q: Q }]));
    await mount();
    fireEvent.change(screen.getByLabelText("你怎么想的"), { target: { value: "半句话" } });
    fireEvent.change(screen.getByLabelText("跟小鲶说"), { target: { value: "插一句" } });
    cleanup();
    await mount();
    expect((screen.getByLabelText("你怎么想的") as HTMLTextAreaElement).value).toBe("半句话");
    expect((screen.getByLabelText("跟小鲶说") as HTMLTextAreaElement).value).toBe("插一句");
    api.answer.mockResolvedValue(view());
    await act(async () => { fireEvent.click(screen.getByText("我不知道")); });
    expect(api.answer).toHaveBeenCalledWith("lrn_1", { qid: "q1", dont_know: true, thinking: "半句话" });
  });

  it("答过的题显示对错、把握、正确答案; 路线图点「开始学」", async () => {
    api.get.mockResolvedValue(view({ phase: "plan_review", plan: PLAN }, [
      { id: "e1", at: 2, role: "tutor", kind: "question", qid: "q1", q: { ...Q, answer: ["b"], explanation: "规则 7.2" } },
      { id: "e2", at: 3, role: "learner", kind: "answer", qid: "q1", picked: ["a"], verdict: "wrong", confidence: 90, thinking: "猜的" },
      { id: "e3", at: 4, role: "tutor", kind: "assessment", goal: "讲清二级要求", summary: "从二级讲起",
        strands: [{ strand: "分级", solid: "知道三级", gap: "二级要求", misconceptions: "以为一个项目就够" }] },
      { id: "e4", at: 5, role: "tutor", kind: "plan", plan: PLAN, approach: "先讲分级再讲二级" },
    ]));
    api.say.mockResolvedValue(view({ phase: "teach", plan: PLAN }, [], true));
    await mount();
    expect(screen.getByText("✗ 不对")).toBeTruthy();
    expect(screen.getByText("把握 90%")).toBeTruthy();
    expect(screen.getByText(/← 正确答案/).textContent).toContain("3 个");
    expect(screen.getByText(/误解: 以为一个项目就够/)).toBeTruthy();
    expect(screen.getByLabelText("学习路线图").textContent).toContain("二级要求");
    await act(async () => { fireEvent.click(screen.getByText("就这样, 开始学")); });
    expect(api.say).toHaveBeenCalledWith("lrn_1", "", true);
  });

  it("后台在跑时每 1.5 秒拉一次, 跑完停", async () => {
    vi.useFakeTimers();
    api.get.mockResolvedValueOnce(view({}, [], true)).mockResolvedValueOnce(view({}, [], true))
      .mockResolvedValue(view({ pending: { qid: "q1" } }, [{ id: "e1", at: 2, role: "tutor", kind: "question", qid: "q1", q: Q }]));
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(screen.getByText("二级要几个项目?")).toBeTruthy();
    const calls = api.get.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.get.mock.calls.length).toBe(calls);
  });

  it("出错 / 没接上: 显示原因和「重试」", async () => {
    api.get.mockResolvedValue(view({ last_error: "网关 502" }));
    api.retry.mockResolvedValue(view({}, [], true));
    await mount();
    expect(screen.getByText(/网关 502/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("重试")); });
    expect(api.retry).toHaveBeenCalledWith("lrn_1");
    cleanup();
    api.get.mockResolvedValue(view({ stalled: true }));
    await mount();
    expect(screen.getByText(/上一步没接上/)).toBeTruthy();
  });

  it("路线图按「要先懂」分层, 当前高亮、问过打勾", async () => {
    expect(planLayers(PLAN)).toEqual([["levels"], ["level-2"], ["goal"]]);
    render(<PlanMap plan={PLAN} current="goal" visited={["levels", "level-2", "goal"]} />);
    expect(screen.getByText("✓ 资质分几级")).toBeTruthy();
    expect(screen.getByText("✓ 二级要求").getAttribute("title")).toBe("要先懂: 资质分几级");
    expect(screen.getByText("答辩").getAttribute("title")).toBe("要先懂: 二级要求");   // 当前那个不打勾, 高亮
  });
});
