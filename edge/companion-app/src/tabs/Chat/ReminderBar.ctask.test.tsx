// @vitest-environment jsdom
/** 提醒条 · 派给同事的任务 (11/07): 催一下 / 延期 / 看任务 (发起人), 去交回 (收件人)。不开对话。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskRemind = vi.hoisted(() => vi.fn());
const taskExtend = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri_colleague_tasks", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_colleague_tasks")>()),
  taskRemind, taskExtend,
}));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import { colleagueDueSoonReminder, colleagueOverdueReminder } from "../../lib/reminders";
import { reloadRemindersForTest, useRemindersStore } from "../../store/reminders";
import { useUIStore } from "../../store/ui";
import ReminderBar from "./ReminderBar";

installMemoryLocalStorage();

const pad = (n: number) => String(n).padStart(2, "0");
const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

function overdue() {
  return colleagueOverdueReminder({ task_id: "t_20261107_aaaaaaaa", title: "整理拜访记录",
    deadline: local(new Date(Date.now() - 3600_000)), people: ["zhang@example.com"] }, new Date())!;
}

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
  taskRemind.mockReset().mockResolvedValue({ ok: true, reminded: ["zhang@example.com"], failed: [] });
  taskExtend.mockReset().mockResolvedValue({ ok: true, sent: ["zhang@example.com"], failed: [], deadline: "2026-11-08T18:00" });
  useUIStore.setState({ activeTab: "chat", pendingReminderChat: null, pendingTaskFocus: null });
});
afterEach(cleanup);

describe("ReminderBar · 同事任务", () => {
  it("逾期: 催一下 → 「催了 1 人」→ 收起; 不开对话", async () => {
    act(() => void useRemindersStore.getState().add(overdue()));
    render(<ReminderBar />);
    expect(screen.getByText("「整理拜访记录」过了截止，zhang 还没交")).toBeTruthy();
    fireEvent.click(screen.getByText("催一下"));
    expect(taskRemind).toHaveBeenCalledWith("t_20261107_aaaaaaaa");
    await screen.findByText("催了 1 人");
    await waitFor(() => expect(useRemindersStore.getState().items).toHaveLength(0), { timeout: 2000 });
    expect(useUIStore.getState().pendingReminderChat).toBeNull();
  });

  it("延期 ▾ → 选一个 → 只改截止", async () => {
    act(() => void useRemindersStore.getState().add(overdue()));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText(/延期/)[0]);
    const item = screen.getAllByRole("menuitem").find((b) => b.textContent?.startsWith("延到明天"))!;
    fireEvent.click(item);
    expect(taskExtend.mock.calls[0][0]).toBe("t_20261107_aaaaaaaa");
    expect(taskExtend.mock.calls[0][1]).toMatch(/T18:00$/);
    await screen.findByText(/截止改到明天 18:00，通知了 1 人/);
  });

  it("催失败: 条留着, 说清没成功", async () => {
    taskRemind.mockRejectedValue(new Error("连不上中央"));
    act(() => void useRemindersStore.getState().add(overdue()));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("催一下"));
    await screen.findByText(/没成功：连不上中央/);
    expect(useRemindersStore.getState().items).toHaveLength(1);
  });

  it("看任务: 切到协同页并打开这个任务的进度", () => {
    act(() => void useRemindersStore.getState().add(overdue()));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("看任务")[0]);
    expect(useUIStore.getState()).toMatchObject({ activeTab: "collab", collabView: "work",
      pendingTaskFocus: { taskId: "t_20261107_aaaaaaaa", role: "owner" } });
  });

  it("收件人快到截止: 去交回 = 打开处理窗口", () => {
    const r = colleagueDueSoonReminder({ task_id: "t_20261107_bbbbbbbb", title: "周报", from: "zhou@example.com",
      deadline: local(new Date(Date.now() + 3600_000)), state: "在做" }, new Date())!;
    act(() => void useRemindersStore.getState().add(r));
    render(<ReminderBar />);
    expect(screen.queryByText("催一下")).toBeNull();
    expect(screen.queryByText(/延期/)).toBeNull();
    fireEvent.click(screen.getByText("去交回"));
    expect(useUIStore.getState().pendingTaskFocus).toEqual({ taskId: "t_20261107_bbbbbbbb", role: "recipient" });
  });
});
