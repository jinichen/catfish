// @vitest-environment jsdom
/** 派任务的「什么时候做」(11/4): 周期必须定结束; 定时要选时间; 马上做 = null。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TaskSchedulePicker, { scheduleText } from "./TaskSchedulePicker";

afterEach(cleanup);

describe("TaskSchedulePicker", () => {
  it("马上 / 定时 / 周期", () => {
    const onChange = vi.fn();
    render(<TaskSchedulePicker onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith({ schedule: null, problem: null });

    fireEvent.click(screen.getByLabelText("定时做一次"));
    expect(onChange.mock.lastCall?.[0].problem).toContain("哪天几点");
    fireEvent.change(screen.getByLabelText("哪天几点做"), { target: { value: "2026-11-20T09:30" } });
    expect(onChange).toHaveBeenLastCalledWith({ schedule: { kind: "once", at: "2026-11-20T09:30" }, problem: null });

    fireEvent.click(screen.getByLabelText("周期做"));
    expect(onChange.mock.lastCall?.[0].problem).toContain("要定结束");
    fireEvent.change(screen.getByLabelText("一共几次"), { target: { value: "4" } });
    expect(onChange).toHaveBeenLastCalledWith({ schedule: { kind: "cron", expr: "0 9 * * 1", until: null, times: 4 }, problem: null });
  });

  it("说明文字", () => {
    expect(scheduleText(null)).toBe("马上做");
    expect(scheduleText({ kind: "cron", expr: "30 8 * * 1-5", until: "2026-12-31", times: null })).toBe("工作日 08:30, 到 2026-12-31 为止");
  });
});
