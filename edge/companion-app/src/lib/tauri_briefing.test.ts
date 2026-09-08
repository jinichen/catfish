import { beforeEach, describe, expect, it, vi } from "vitest";

const toolBridgeCallToolMock = vi.fn();
vi.mock("./tauri_services", () => ({
  toolBridgeCallTool: (...args: unknown[]) => toolBridgeCallToolMock(...args),
}));
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { remindersWeekFetch } from "./tauri_briefing";

describe("remindersWeekFetch", () => {
  beforeEach(() => toolBridgeCallToolMock.mockReset());

  it("从本机任务库读取本周任务并映射为早安待办", async () => {
    toolBridgeCallToolMock.mockResolvedValue({
      ok: true,
      result: {
        ok: true,
        tasks: [{
          task_id: "reminders:abc",
          title: "整理材料",
          source: "reminders",
          source_id: "abc",
          list_name: "工作",
          due_date_iso: "2026-09-09T18:00:00",
          priority: 2,
          body: "发给项目组",
        }],
      },
    });

    const todos = JSON.parse(await remindersWeekFetch());
    expect(toolBridgeCallToolMock).toHaveBeenCalledWith("catfish_list_tasks", {
      scope: "active",
      include_completed: false,
      limit: 100,
    });
    expect(todos).toEqual([expect.objectContaining({
      text: "整理材料",
      section: "工作",
      reminder_id: "abc",
      due_date_iso: "2026-09-09T18:00:00",
      priority: 2,
      body: "发给项目组",
    })]);
  });

  it("任务库返回错误时保留可诊断错误", async () => {
    toolBridgeCallToolMock.mockResolvedValue({
      ok: false,
      error: "任务库不可用",
    });

    await expect(remindersWeekFetch()).rejects.toThrow("任务库不可用");
  });
});
