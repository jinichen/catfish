/** 外部图片自动显示 (11/14): 默认开; 疑似钓鱼 / 扫描结果没回来的先拦。 */
import { describe, expect, it } from "vitest";

import { autoShowRemote, getAutoLoadImages, looksPhishy, setAutoLoadImages } from "./emailRemoteImages";

const scan = (p: Record<string, unknown>) => ({ messageId: "m", scannedAt: "", flags: [], highestSeverity: "none", ...p }) as never;

describe("emailRemoteImages", () => {
  it("没设过 → 默认自动显示; 关了就是关了", () => {
    expect(getAutoLoadImages()).toBe(true);
    setAutoLoadImages(false);
    expect(getAutoLoadImages()).toBe(false);
    setAutoLoadImages(true);
  });

  it("疑似钓鱼: 报中 / 高, 或标过是钓鱼; 标过「不是钓鱼」的不算", () => {
    expect(looksPhishy(null)).toBe(false);
    expect(looksPhishy(scan({ highestSeverity: "low" }))).toBe(false);
    expect(looksPhishy(scan({ highestSeverity: "medium" }))).toBe(true);
    expect(looksPhishy(scan({ highestSeverity: "high" }))).toBe(true);
    expect(looksPhishy(scan({ userMark: "phishing" }))).toBe(true);
    expect(looksPhishy(scan({ highestSeverity: "high", userMark: "safe" }))).toBe(false);
  });

  it("自动显示要: 开关开着 + 扫描结果回来了 + 不是疑似钓鱼", () => {
    expect(autoShowRemote(true, true, null)).toBe(true);
    expect(autoShowRemote(true, false, null)).toBe(false);
    expect(autoShowRemote(false, true, null)).toBe(false);
    expect(autoShowRemote(true, true, scan({ highestSeverity: "high" }))).toBe(false);
  });
});
