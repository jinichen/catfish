import { describe, expect, it } from "vitest";
import { autoContinueDecision, AUTO_CONTINUE_EXHAUSTED_TEXT, MAX_AUTO_CONTINUES } from "./auto_continue";

const base = { on: true, used: 0, aborted: false };

describe("autoContinueDecision", () => {
  it("网关工具上限叫停 / 输出截断 → 接力", () => {
    expect(autoContinueDecision({ ...base, loopLimit: true })).toBe("continue");
    expect(autoContinueDecision({ ...base, truncated: true })).toBe("continue");
  });
  it("正常停下不接力 —— 做完了的任务不追发「继续」", () => {
    expect(autoContinueDecision({ ...base })).toBe("stop");
    expect(autoContinueDecision({ ...base, loopLimit: false, truncated: false })).toBe("stop");
  });
  it("开关关着 / 员工点了停止 → 停", () => {
    expect(autoContinueDecision({ ...base, on: false, loopLimit: true })).toBe("stop");
    expect(autoContinueDecision({ ...base, aborted: true, truncated: true })).toBe("stop");
  });
  it("接满 3 次还被叫停 → 说一声再停", () => {
    expect(autoContinueDecision({ ...base, loopLimit: true, used: MAX_AUTO_CONTINUES - 1 })).toBe("continue");
    expect(autoContinueDecision({ ...base, loopLimit: true, used: MAX_AUTO_CONTINUES })).toBe("exhausted");
    expect(AUTO_CONTINUE_EXHAUSTED_TEXT).toContain(`${MAX_AUTO_CONTINUES} 次`);
  });
});
