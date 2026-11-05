// @vitest-environment jsdom
/** 同事派给我的任务 (11/4): 接下才通知对方、「让小鲶做」交给工作台且小鲶不能交回、交回才发出去、不接要原因。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  taskGet: vi.fn(),
  taskAccept: vi.fn(() => Promise.resolve({ accepted_at: "x" })),
  taskDraft: vi.fn(() => Promise.resolve({ chars: 1 })),
  taskSubmit: vi.fn(() => Promise.resolve({ done_at: "x", late: false })),
  taskDecline: vi.fn(() => Promise.resolve({ ok: true })),
};
vi.mock("../../lib/tauri_colleague_tasks", () => ({
  taskGet: (...a: unknown[]) => api.taskGet(...a),
  taskAccept: (...a: unknown[]) => api.taskAccept(...a),
  taskDraft: (...a: unknown[]) => api.taskDraft(...a),
  taskSubmit: (...a: unknown[]) => api.taskSubmit(...a),
  taskDecline: (...a: unknown[]) => api.taskDecline(...a),
}));
vi.mock("../../lib/colleagueTasksStore", () => ({ refreshTasks: vi.fn(() => Promise.resolve()) }));

import TaskWork from "./TaskWork";
import { doPrompt } from "../../lib/taskPrompts";
import { useUIStore } from "../../store/ui";

const REC = {
  task_id: "t_20261104_ab12cd34", owner: "zhou@example.com", status: "open", received_at: "2026-11-04T08:00:00+08:00",
  task: { title: "整理本周拜访", text: "把本周客户拜访情况整理成三行发我", deadline: "2099-11-05T18:00" },
};

const startChat = vi.fn();
beforeEach(() => {
  Object.values(api).forEach((f) => f.mockClear());
  startChat.mockClear();
  useUIStore.setState({ startEmailChat: startChat } as never);
});
afterEach(cleanup);

async function open(rec: object, state: string) {
  api.taskGet.mockResolvedValue({ role: "recipient", record: rec, state });
  const onClose = vi.fn();
  render(<TaskWork taskId={REC.task_id} onClose={onClose} />);
  await screen.findByText("「整理本周拜访」");
  return onClose;
}

describe("TaskWork", () => {
  it("接下 (默认): 小鲶在后台开工, 不开对话", async () => {
    await open(REC, "待处理");
    fireEvent.click(screen.getByText("🐟 接下 (小鲶马上开工)"));
    await screen.findByText("接下了, 小鲶已经在后台开工, 做好了会通知你核对");
    expect(api.taskAccept).toHaveBeenCalledWith(REC.task_id);
    expect(startChat).not.toHaveBeenCalled();
  });

  it("接下, 在对话里看着小鲶做: 先通知对方在做 (不另起后台), 再开工作台对话, 提示词里同事原话被隔开且不让小鲶交回", async () => {
    const onClose = await open(REC, "待处理");
    fireEvent.click(screen.getByText("接下, 在对话里看着小鲶做"));
    await waitFor(() => expect(startChat).toHaveBeenCalled());
    expect(api.taskAccept).toHaveBeenCalledWith(REC.task_id, false, false);
    expect(onClose).toHaveBeenCalled();
    const prompt = startChat.mock.calls[0][1] as string;
    expect(prompt).toBe(doPrompt(REC as never));
    expect(prompt).toContain("<<<\n把本周客户拜访情况整理成三行发我\n>>>");
    expect(prompt).toContain("task_draft");
    expect(prompt).toContain("不要自己把结果发给对方");
  });

  it("在做时: 结果框带出小鲶的草稿, 点交回才发", async () => {
    await open({ ...REC, accepted_at: "x", draft: { result: "拜访 3 家", updated_at: "2026-11-04T09:00" } }, "在做");
    const box = screen.getByPlaceholderText(/小鲶做完会把结果存在这里/) as HTMLTextAreaElement;
    expect(box.value).toBe("拜访 3 家");
    fireEvent.change(box, { target: { value: "拜访 3 家: 甲 / 乙 / 丙" } });
    fireEvent.click(screen.getByText("交回"));
    await waitFor(() => expect(api.taskSubmit).toHaveBeenCalledWith(REC.task_id, "拜访 3 家: 甲 / 乙 / 丙"));
  });

  it("不接要写原因, 告诉对方后弹窗关掉", async () => {
    const onClose = await open(REC, "待处理");
    fireEvent.click(screen.getByText("不接"));
    const btn = screen.getByText("告诉对方").closest("button")!;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/为什么不接/), { target: { value: "这周出差" } });
    fireEvent.click(btn);
    await waitFor(() => expect(api.taskDecline).toHaveBeenCalledWith(REC.task_id, "这周出差"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("已经回了不接: 还能改主意接下, 不再有「不接」", async () => {
    await open({ ...REC, declined: { reason: "这周出差", at: "x" } }, "不接");
    expect(screen.getByText("改主意了, 还能接下:")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "不接" })).toBeNull();
    expect(screen.getByText("🐟 接下 (小鲶马上开工)")).toBeTruthy();
  });

  it("对方取消了: 不再有接下 / 交回", async () => {
    await open({ ...REC, status: "cancelled" }, "已取消");
    expect(screen.getByText(/对方已经取消了这个任务/)).toBeTruthy();
    expect(screen.queryByText("🐟 接下, 让小鲶做")).toBeNull();
    expect(screen.queryByText("交回")).toBeNull();
  });
});
