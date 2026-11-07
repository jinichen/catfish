// @vitest-environment jsdom
/** 任务进度 · 小鲶核对 (11/07): 每人一行结论, 重做框里先填好小鲶起草的; 改了再交的标出来。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ taskGet: vi.fn(), taskCheck: vi.fn(), taskRedo: vi.fn() }));
vi.mock("../../lib/tauri_colleague_tasks", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri_colleague_tasks")>()), ...api }));
vi.mock("../../lib/colleagueTasksStore", () => ({ refreshTasks: vi.fn().mockResolvedValue(undefined) }));

import TaskProgress, { checkOf } from "./TaskProgress";

afterEach(cleanup);

const row = (email: string, result: string, done_at: string) => ({ email, state: "已完成", invited_at: "x", reminded_at: null,
  accepted_at: "x", done_at, result, reason: null, periods: [] });
const detail = (check?: object) => ({
  role: "owner", schedule_text: null,
  task: { task_id: "t_1", title: "整理拜访记录", text: "整理本周拜访", expect: "客户名、金额、下一步", deadline: null, owner: "zhou@example.com",
          status: "open", created_at: "", recipients: {}, check },
  progress: { rows: [row("zhang@example.com", "南岭建设", "2026-11-07T10:00"), row("li@example.com", "东湖设计 20 万 下周签", "2026-11-07T11:00")],
              counts: { 已完成: 2 }, total: 2 },
});

describe("TaskProgress · 小鲶核对", () => {
  it("没核对过: 显示「要交」和核对按钮; 点了调核对", async () => {
    api.taskGet.mockResolvedValue(detail());
    api.taskCheck.mockResolvedValue({ check: { at: "", summary: "1/2 对上", people: {} }, checked: ["zhang@example.com", "li@example.com"] });
    render(<TaskProgress taskId="t_1" onClose={vi.fn()} />);
    expect(await screen.findByText("客户名、金额、下一步")).toBeTruthy();
    fireEvent.click(screen.getByText("🐟 小鲶核对结果"));
    expect(api.taskCheck).toHaveBeenCalledWith("t_1");
    expect(await screen.findByText("小鲶核对了 2 人: 1/2 对上")).toBeTruthy();
  });

  it("核对过: 缺什么逐条列; 「按小鲶的意见请他重做」框里先填好; 改了再交的提醒再核对", async () => {
    api.taskGet.mockResolvedValue(detail({ at: "2026-11-07T12:00", summary: "1/2 对上", people: {
      "zhang@example.com": { ok: false, gaps: ["没写金额", "没写下一步"], redo_note: "补上金额和下一步", result_at: "2026-11-07T09:00" },
      "li@example.com": { ok: true, gaps: [], redo_note: "", result_at: "2026-11-07T11:00" },
    } }));
    render(<TaskProgress taskId="t_1" onClose={vi.fn()} />);
    expect(await screen.findByText(/还缺 2 处/)).toBeTruthy();
    expect(screen.getByText("· 没写金额")).toBeTruthy();
    expect(screen.getByText(/核对后他又改了再交/)).toBeTruthy();   // 张三 10:00 交的, 核对时看到的是 09:00 那版
    expect(screen.getByText(/对上了要求/)).toBeTruthy();
    fireEvent.click(screen.getByText("按小鲶的意见请他重做"));
    expect((screen.getByPlaceholderText("说一下要怎么改 (会发给他)") as HTMLInputElement).value).toBe("补上金额和下一步");
    expect(screen.getByText("🐟 再核对一次")).toBeTruthy();
  });

  it("checkOf: 没交 / 没结果的不算", () => {
    expect(checkOf(undefined, row("a@x.com", "r", "t") as never)).toBeNull();
    expect(checkOf({ at: "", summary: "", people: { "a@x.com": { ok: true, gaps: [], redo_note: "" } } },
                   { ...row("a@x.com", "", "t"), result: null } as never)).toBeNull();
  });
});
