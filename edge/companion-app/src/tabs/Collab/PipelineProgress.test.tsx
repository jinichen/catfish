// @vitest-environment jsdom
/** 多阶段 (11/4 第三步): 交给下一段的内容默认是这一段的结果、可改、放行才发; 有人没交时提示; 最后一段是完成流程。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  pipelineGet: vi.fn(),
  pipelineRelease: vi.fn(() => Promise.resolve({ done: false, stage: 2, sent: ["wang@example.com"], failed: [], cancelled: [] })),
  pipelineCancel: vi.fn(() => Promise.resolve({ ok: true })),
};
vi.mock("../../lib/tauri_colleague_tasks", () => ({
  pipelineGet: (...a: unknown[]) => api.pipelineGet(...a),
  pipelineRelease: (...a: unknown[]) => api.pipelineRelease(...a),
  pipelineCancel: (...a: unknown[]) => api.pipelineCancel(...a),
}));
vi.mock("../../lib/colleagueTasksStore", () => ({ refreshTasks: vi.fn(() => Promise.resolve()) }));
vi.mock("./TaskProgress", () => ({ default: () => <div>stage-task-progress</div> }));

import PipelineProgress from "./PipelineProgress";

const row = (email: string, state: string, result: string | null) => ({
  email, state, invited_at: "x", reminded_at: null, accepted_at: null, done_at: null, result, reason: null, periods: [],
});
const stage = (index: number, name: string, extra: object) => ({
  index, name, text: `${name}要做的`, recipients: ["zhang@example.com"], deadline: null, task_id: null, input: null,
  started_at: null, released_at: null, finished: false, progress: null, ...extra,
});

function detail(current: number, rows: object[]) {
  return {
    pipeline: { pipeline_id: "p_20261104_ab12cd34", title: "本周客户周报", owner: "zhou@example.com", status: "open", current,
      stages: [
        stage(0, "收集", { task_id: "t_20261104_00000001", finished: true, progress: { rows, counts: {}, total: 2 } }),
        stage(1, "分析", {}),
      ] },
    handoff: "【zhang@example.com】\n拜访甲",
  };
}

beforeEach(() => Object.values(api).forEach((f) => f.mockClear()));
afterEach(cleanup);

describe("PipelineProgress", () => {
  it("改了交给下一段的内容再放行", async () => {
    api.pipelineGet.mockResolvedValue(detail(0, [row("zhang@example.com", "已完成", "拜访甲")]));
    render(<PipelineProgress pipelineId="p_20261104_ab12cd34" onClose={vi.fn()} />);
    const box = await screen.findByLabelText("交给下一段的内容") as HTMLTextAreaElement;
    expect(box.value).toBe("【zhang@example.com】\n拜访甲");
    fireEvent.change(box, { target: { value: "本周拜访了甲 (去掉了电话)" } });
    fireEvent.click(screen.getByText("放行到下一段"));
    await waitFor(() => expect(api.pipelineRelease).toHaveBeenCalledWith("p_20261104_ab12cd34", "本周拜访了甲 (去掉了电话)"));
  });

  it("还有人没交回: 提示放行会取消他们", async () => {
    api.pipelineGet.mockResolvedValue(detail(0, [row("zhang@example.com", "已完成", "拜访甲"), row("li@example.com", "在做", null)]));
    render(<PipelineProgress pipelineId="p_20261104_ab12cd34" onClose={vi.fn()} />);
    expect(await screen.findByText(/还有 1 人没交回/)).toBeTruthy();
  });

  it("最后一段: 完成流程, 不要交接内容", async () => {
    api.pipelineGet.mockResolvedValue(detail(1, []));
    render(<PipelineProgress pipelineId="p_20261104_ab12cd34" onClose={vi.fn()} />);
    expect(await screen.findByText("完成流程")).toBeTruthy();
    expect(screen.queryByLabelText("交给下一段的内容")).toBeNull();
  });

  it("点这一段的详情打开那一段的任务进度", async () => {
    api.pipelineGet.mockResolvedValue(detail(0, [row("zhang@example.com", "已完成", "拜访甲")]));
    render(<PipelineProgress pipelineId="p_20261104_ab12cd34" onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText(/这一段的详情/));
    expect(screen.getByText("stage-task-progress")).toBeTruthy();
  });
});
