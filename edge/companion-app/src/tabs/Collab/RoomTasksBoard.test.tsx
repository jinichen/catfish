// @vitest-environment jsdom
/** 群里的「任务」面板 (11/07): 逾期标出来、按身份给按钮、默认只看进行中。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const taskRemind = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri_colleague_tasks", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri_colleague_tasks")>()), taskRemind }));

import type { RoomMember, RoomTaskRow } from "../../lib/tauri_colleague_rooms";
import RoomTasksBoard, { tasksButtonLabel, type RoomTasksData } from "./RoomTasksBoard";

afterEach(cleanup);
const ME = "zhou@example.com";
const M = (email: string, name: string): RoomMember => ({ email, name, state: "joined", owner: false, me: email === ME });
const members = [M(ME, "周总"), M("zhang@example.com", "张三"), M("li@example.com", "李四")];
const row = (o: Partial<RoomTaskRow>): RoomTaskRow => ({ task_id: "t_1", title: "整理拜访记录", owner: ME, deadline: "2026-11-07T12:00", schedule: null,
  at: "", msg_id: "m_1", seq: 7, people: [], late: 0, open: true, ...o });
const data: RoomTasksData = {
  open: 2, late: 1,
  tasks: [
    row({ late: 1, people: [{ email: "zhang@example.com", state: "accepted" }, { email: "li@example.com", state: "done" }] }),
    row({ task_id: "t_2", title: "周报", owner: "li@example.com", deadline: "2026-11-09T18:00", seq: 9,
          people: [{ email: ME, state: "waiting" }] }),
    row({ task_id: "t_3", title: "旧事", open: false, people: [{ email: "zhang@example.com", state: "done" }] }),
  ],
};

describe("RoomTasksBoard", () => {
  it("按钮上: 进行中几个、几个逾期", () => {
    expect(tasksButtonLabel(data)).toBe("📋 任务 2 · 1 逾期");
    expect(tasksButtonLabel({ tasks: [], open: 0, late: 0 })).toBe("📋 任务");
  });

  it("逾期的人标红; 我派的能催 / 看结果, 派给我的能去处理; 点标题跳到任务卡", () => {
    const onJump = vi.fn(), onWatch = vi.fn(), onWork = vi.fn();
    taskRemind.mockResolvedValue({ ok: true, reminded: ["zhang@example.com"], failed: [] });
    render(<RoomTasksBoard data={data} me={ME} members={members} onJump={onJump} onWatch={onWatch} onWork={onWork} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText("截止 11/7 12:00 · 1 人逾期")).toBeTruthy();
    expect((screen.getByText("张三 在做") as HTMLElement).style.color).toContain("status-error");
    expect(screen.queryByText("旧事")).toBeNull();
    fireEvent.click(screen.getByText("整理拜访记录"));
    expect(onJump).toHaveBeenCalledWith(7);
    fireEvent.click(screen.getByText("看结果"));
    expect(onWatch).toHaveBeenCalledWith("t_1");
    fireEvent.click(screen.getByText("去处理"));
    expect(onWork).toHaveBeenCalledWith("t_2");
    expect(screen.getAllByText("催一下")).toHaveLength(1);   // 别人派的不能催
    fireEvent.click(screen.getByText("催一下"));
    expect(taskRemind).toHaveBeenCalledWith("t_1");
    fireEvent.click(screen.getByText("也看已结束的 (1)"));
    expect(screen.getByText("旧事")).toBeTruthy();
  });

  it("空群: 说怎么派", () => {
    render(<RoomTasksBoard data={{ tasks: [], open: 0, late: 0 }} me={ME} members={members} onJump={vi.fn()} onWatch={vi.fn()}
                           onWork={vi.fn()} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByText(/还没派过任务/)).toBeTruthy();
  });
});
