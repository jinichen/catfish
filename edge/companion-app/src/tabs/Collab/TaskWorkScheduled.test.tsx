// @vitest-environment jsdom
/** 定时 / 周期任务 (11/4 第二步): 接下可选「每次直接交回」, 每期核对后交回, 退出要原因, 现在做一次走工作台。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  taskGet: vi.fn(),
  taskAccept: vi.fn(() => Promise.resolve({ accepted_at: "x", next_run: "2026-11-09T09:00:00+08:00" })),
  taskPeriodSubmit: vi.fn(() => Promise.resolve({ period: 1 })),
  taskQuit: vi.fn(() => Promise.resolve({ ok: true })),
  taskDecline: vi.fn(() => Promise.resolve({ ok: true })),
};
vi.mock("../../lib/tauri_colleague_tasks", () => ({
  taskGet: (...a: unknown[]) => api.taskGet(...a),
  taskAccept: (...a: unknown[]) => api.taskAccept(...a),
  taskPeriodSubmit: (...a: unknown[]) => api.taskPeriodSubmit(...a),
  taskQuit: (...a: unknown[]) => api.taskQuit(...a),
  taskDecline: (...a: unknown[]) => api.taskDecline(...a),
  taskDraft: vi.fn(), taskSubmit: vi.fn(),
}));
vi.mock("../../lib/colleagueTasksStore", () => ({ refreshTasks: vi.fn(() => Promise.resolve()) }));

import TaskWork from "./TaskWork";
import { useUIStore } from "../../store/ui";

const REC = {
  task_id: "t_20261104_ab12cd34", owner: "zhou@example.com", status: "open", received_at: "2026-11-04T08:00:00+08:00",
  task: { title: "周报", text: "每周一把上周拜访整理成三行", deadline: null, schedule: { kind: "cron", expr: "0 9 * * 1", times: 4 } },
};
const startChat = vi.fn();

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockClear());
  startChat.mockClear();
  useUIStore.setState({ startEmailChat: startChat } as never);
});
afterEach(cleanup);

async function open(rec: object, state: string, nextRun: string | null = null) {
  api.taskGet.mockResolvedValue({ role: "recipient", record: rec, state, schedule_text: "每周一 09:00, 最多 4 次", next_run: nextRun });
  render(<TaskWork taskId={REC.task_id} onClose={vi.fn()} />);
  await screen.findByText("「周报」");
}

describe("TaskWorkScheduled", () => {
  it("接下时可以选每次直接交回", async () => {
    await open(REC, "待处理");
    expect(screen.getByText(/每周一 09:00, 最多 4 次/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/每次做完直接交回/));
    fireEvent.click(screen.getByText("🐟 接下, 到点让小鲶做"));
    await waitFor(() => expect(api.taskAccept).toHaveBeenCalledWith(REC.task_id, true));
  });

  it("待核对的一期: 改好点交回", async () => {
    const rec = { ...REC, accepted_at: "x", job_id: "j1",
                  periods: [{ period: 1, result: "拜访 2 家", at: "2026-11-09T09:01", late_minutes: 0, state: "待核对" }] };
    await open(rec, "待核对", "2026-11-16T09:00:00+08:00");
    expect(screen.getByText(/下次 2026-11-16 09:00/)).toBeTruthy();
    const box = screen.getByLabelText("第 1 次的结果");
    fireEvent.change(box, { target: { value: "拜访 2 家: 甲 / 乙" } });
    fireEvent.click(screen.getByText("交回"));
    await waitFor(() => expect(api.taskPeriodSubmit).toHaveBeenCalledWith(REC.task_id, 1, "拜访 2 家: 甲 / 乙"));
  });

  it("现在让小鲶做一次: 提示词要它用 task_period", async () => {
    await open({ ...REC, accepted_at: "x", job_id: "j1" }, "已安排");
    fireEvent.click(screen.getByText("🐟 现在让小鲶做一次"));
    expect(startChat).toHaveBeenCalled();
    expect(startChat.mock.calls[0][1]).toContain("task_period");
  });

  it("退出要写原因", async () => {
    await open({ ...REC, accepted_at: "x", job_id: "j1" }, "进行中");
    fireEvent.click(screen.getByText("退出"));
    fireEvent.change(screen.getByPlaceholderText(/为什么退出/), { target: { value: "调岗了" } });
    fireEvent.click(screen.getByText("告诉对方"));
    await waitFor(() => expect(api.taskQuit).toHaveBeenCalledWith(REC.task_id, "调岗了"));
  });

  it("对方改了: 提示重新同意", async () => {
    await open({ ...REC, changed_at: "x" }, "要重新同意");
    expect(screen.getByText(/对方改了任务/)).toBeTruthy();
    expect(screen.getByText("🐟 接下, 到点让小鲶做")).toBeTruthy();
  });
});
