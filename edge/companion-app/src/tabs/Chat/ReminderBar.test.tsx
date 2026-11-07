// @vitest-environment jsdom
/** 提醒条 (11/07): 主按钮开新对话、稍后、已完成写回任务库、✕、多条逐条翻。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMarkCompleted = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri")>()),
  taskMarkCompleted,
}));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import { deadlineReminder, urgentEmailReminder, type Reminder } from "../../lib/reminders";
import { reloadRemindersForTest, useRemindersStore } from "../../store/reminders";
import { useUIStore } from "../../store/ui";
import { useChatStore } from "../../store/chat";
import ReminderBar from "./ReminderBar";

installMemoryLocalStorage();

/** 明天 18:00 到期的一条 (相对真实的现在, 组件按 Date.now() 算可见) */
function tomorrowTask(title: string, taskId = `t-${title}`): Reminder {
  const due = new Date();
  due.setDate(due.getDate() + 1);
  due.setHours(18, 0, 0, 0);
  const iso = `${due.getFullYear()}-${String(due.getMonth() + 1).padStart(2, "0")}-${String(due.getDate()).padStart(2, "0")}T18:00:00`;
  return deadlineReminder({ text: title, due_date_iso: iso, task_id: taskId, section: "资质集采二期" }, `deadline:${title}`, new Date())!;
}

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
  taskMarkCompleted.mockReset().mockResolvedValue(undefined);
  useUIStore.setState({ activeTab: "chat", pendingReminderChat: null });
});
afterEach(cleanup);

describe("ReminderBar", () => {
  it("没有提醒就什么都不渲染", () => {
    const { container } = render(<ReminderBar />);
    expect(container.innerHTML).toBe("");
  });

  it("显示事实; 主按钮 = 新开对话交给小鲶 (不写进当前对话), 条收起", () => {
    useChatStore.setState({ messages: [], persistedSessionId: "s-open" });
    act(() => void useRemindersStore.getState().add(tomorrowTask("11/9 材料闭环")));
    render(<ReminderBar />);
    expect(screen.getByText("待办「11/9 材料闭环」明天 18:00 到期")).toBeTruthy();
    expect(screen.getByText(/来自 资质集采二期/)).toBeTruthy();
    fireEvent.click(screen.getByText("让小鲶看看还差啥"));
    const pending = useUIStore.getState().pendingReminderChat;
    expect(pending?.title).toBe("待办：11/9 材料闭环");
    expect(pending?.prompt).toContain("待办「11/9 材料闭环」明天 18:00 到期");
    expect(useChatStore.getState().messages).toHaveLength(0);
    expect(screen.queryByText("让小鲶看看还差啥")).toBeNull();
  });

  it("已完成: 写回任务库 → 「已标完成」→ 约 1 秒后收起, 不再挂回来", async () => {
    act(() => void useRemindersStore.getState().add(tomorrowTask("交标书", "reminders:r9")));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("已完成"));
    expect(taskMarkCompleted).toHaveBeenCalledWith("reminders:r9", "交标书");
    await screen.findByText("已标完成");
    await waitFor(() => expect(screen.queryByText("已标完成")).toBeNull(), { timeout: 2000 });
    expect(useRemindersStore.getState().items).toHaveLength(0);
    expect(useRemindersStore.getState().add(tomorrowTask("交标书", "reminders:r9"))).toBe(false);
  });

  it("已完成写失败: 条留着, 说清没标上", async () => {
    taskMarkCompleted.mockRejectedValue(new Error("tool-bridge 没起"));
    act(() => void useRemindersStore.getState().add(tomorrowTask("交标书")));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("已完成"));
    await screen.findByText(/没标上：tool-bridge 没起/);
    expect(useRemindersStore.getState().items).toHaveLength(1);
  });

  it("稍后提醒 ▾ → 2 小时后: 条收起, 记下稍后时刻", () => {
    act(() => void useRemindersStore.getState().add(tomorrowTask("交标书")));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("稍后提醒"));
    fireEvent.click(screen.getByRole("menuitem", { name: "2 小时后" }));
    const [r] = useRemindersStore.getState().items;
    expect(r.snoozedUntil).toBeGreaterThan(Date.now() + 3600_000);
    expect(screen.queryByText("交标书", { exact: false })).toBeNull();
  });

  it("✕: 收起, 今天不再挂", () => {
    act(() => void useRemindersStore.getState().add(tomorrowTask("交标书")));
    render(<ReminderBar />);
    fireEvent.click(screen.getByLabelText("关掉这条提醒"));
    expect(useRemindersStore.getState().items).toHaveLength(0);
    expect(useRemindersStore.getState().add(tomorrowTask("交标书"))).toBe(false);
  });

  it("几条同时挂着: 只显示最急的, 「还有 N 条」逐条翻", () => {
    act(() => {
      useRemindersStore.getState().add(tomorrowTask("明天的事"));
      useRemindersStore.getState().add(urgentEmailReminder([{ sender: "张三", subject: "合同今天签" }], ["m1"], new Date())!);
    });
    render(<ReminderBar />);
    // 急邮件比明天到期的急
    expect(screen.getByText("1 封急邮件")).toBeTruthy();
    expect(screen.getByText("让小鲶看看")).toBeTruthy();
    expect(screen.queryByText("稍后提醒")).toBeNull(); // 急邮件没有稍后 / 已完成
    fireEvent.click(screen.getByText("还有 1 条 ›"));
    expect(screen.getByText(/明天的事/)).toBeTruthy();
  });
});
