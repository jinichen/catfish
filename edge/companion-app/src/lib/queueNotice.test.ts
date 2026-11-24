import { describe, expect, it } from "vitest";
import { queueText, turnsAhead } from "./queueNotice";

describe("排队提示 (11/24 是不是又卡住了)", () => {
  it("自己是最后进队的: 前面 = 在跑的 + 排在前面的", () => {
    expect(turnsAhead({ started: 1, queued: 1 })).toBe(1);
    expect(turnsAhead({ started: 1, queued: 3 })).toBe(3);
  });
  it("没在排队 (自己就是在跑的那轮 / 服务没登记) 不改口", () => {
    expect(turnsAhead({ started: 1, queued: 0 })).toBe(0);
    expect(turnsAhead({ started: 0, queued: 0 })).toBe(0);
  });
  it("说人话", () => {
    expect(queueText(1)).toContain("上一条还在处理");
    expect(queueText(3)).toContain("前面还有 3 条");
  });
});
