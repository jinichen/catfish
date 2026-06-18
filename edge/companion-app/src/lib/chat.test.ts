/** P3.5.34 (6/18 鸿波 catch '对话框跑一半停下来') — 修-A idle timeout 公式单测.
 *
 * chat.ts streamChat 是高度集成的 async 函数 (依赖 fetch / SSE / Tauri Rust IPC /
 * 多层 callback), 整体单测难度高. 这里只测**可纯函数化的核心决策**:
 * model-aware idle timeout 计算 (computeIdleTimeoutMs).
 *
 * 端到端 retry 行为靠生产 audit + e2e 验, 不在此 file 范围.
 */
import { describe, it, expect } from "vitest";

import { computeIdleTimeoutMs } from "./chat";

describe("P3.5.34 修-A: computeIdleTimeoutMs", () => {
  it("catfish-private-main → 180s (内网慢)", () => {
    expect(computeIdleTimeoutMs("catfish-private-main")).toBe(180_000);
  });

  it("catfish-private-vision → 180s", () => {
    expect(computeIdleTimeoutMs("catfish-private-vision")).toBe(180_000);
  });

  it("catfish-private-coder → 180s", () => {
    expect(computeIdleTimeoutMs("catfish-private-coder")).toBe(180_000);
  });

  it("大小写不敏感 — CATFISH-PRIVATE-MAIN 也 180s", () => {
    expect(computeIdleTimeoutMs("CATFISH-PRIVATE-MAIN")).toBe(180_000);
  });

  it("deepseek 公网 → 90s (快)", () => {
    expect(computeIdleTimeoutMs("catfish-public-deepseek-flash")).toBe(90_000);
  });

  it("qwen 公网 → 90s", () => {
    expect(computeIdleTimeoutMs("catfish-public-qwen-flash")).toBe(90_000);
  });

  it("gemini → 90s", () => {
    expect(computeIdleTimeoutMs("gemini-3.0-pro-preview")).toBe(90_000);
  });

  it("空字符串 fallback → 90s", () => {
    expect(computeIdleTimeoutMs("")).toBe(90_000);
  });

  it("private 但不是 main/vision/coder → 90s (默认快)", () => {
    expect(computeIdleTimeoutMs("catfish-private-random")).toBe(90_000);
  });
});
