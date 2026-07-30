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

  // 7/30: 判定从"枚举三个后缀"改成"认 catfish-private- 前缀"。
  // 模型能在中央门户界面上新增之后, 客户加的内网模型不会叫 main/vision/coder,
  // 而它们同样慢 —— 落到 90s 会中途 abort, 正是本函数要修的那个症状。
  it("客户新加的内网模型也要 180s（不能只认写死的三个后缀）", () => {
    expect(computeIdleTimeoutMs("catfish-private-glm")).toBe(180_000);
    expect(computeIdleTimeoutMs("catfish-private-随便什么名字")).toBe(180_000);
  });

  it("公网模型不受影响 — 名字里含 private 也不算", () => {
    // 前缀判定要锚定开头, 不能是"包含" —— 否则公网模型叫
    // catfish-public-private-ish 之类会被误判成内网。
    expect(computeIdleTimeoutMs("catfish-public-private-ish")).toBe(90_000);
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
