import { describe, expect, it } from "vitest";
import { asksEveryTime, everyTimeReason, EVERY_TIME_MARK } from "./approvalScope";

describe("approvalScope", () => {
  it("插件标了「每次都要确认」的审批, 不给始终允许", () => {
    const d = `${EVERY_TIME_MARK}: 这段代码会删除文件。删除这类操作每次都要你同意, 「始终允许」管不到它。`;
    expect(asksEveryTime(d)).toBe(true);
    expect(everyTimeReason(d)).toBe("这段代码会删除文件。删除这类操作每次都要你同意, 「始终允许」管不到它。");
  });

  it("老写法 (带 U+FE0F) 和 hermes v0.23 显成的转义都认", () => {
    for (const mark of ["⚠\uFE0F 每次都要确认", "⚠\\ufe0f 每次都要确认"]) {
      const d = `${mark}: 这条命令会删除文件。`;
      expect(asksEveryTime(d)).toBe(true);
      expect(everyTimeReason(d)).toBe("这条命令会删除文件。");
    }
  });

  it("普通审批照常", () => {
    expect(asksEveryTime("execute_code script execution. The script can spawn subprocesses")).toBe(false);
    expect(asksEveryTime(undefined)).toBe(false);
    expect(everyTimeReason("x")).toBe("");
  });
});
