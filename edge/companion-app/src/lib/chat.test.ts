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

  // ── 7/30 推翻了一个 6/18 的决定, 记下原委 ──────────────────────────
  //
  // 这条原本是:
  //     it("private 但不是 main/vision/coder → 90s (默认快)", ...)
  //     expect(computeIdleTimeoutMs("catfish-private-random")).toBe(90_000);
  //
  // 它跟 computeIdleTimeoutMs 是同一个提交 (5b0f5e2, 6/18) 写的, 钉的是
  // "只有枚举的三个内网模型给 180s, 其它一律 90s"。
  //
  // 7/30 改成认 catfish-private- 前缀, 理由:
  //
  //   1. 这个前缀的含义就是**内网自建平台**。函数自己的注释写着那三个是
  //      "40K context 80-150s 单 call" —— 慢是因为跑在内网平台上, 不是因为
  //      叫 main/vision/coder。同平台上新加的模型同样慢。
  //
  //   2. 模型现在能在中央门户 /admin/models 界面上新增。客户加的内网模型
  //      不会恰好叫 main/vision/coder, 于是落到 90s。
  //
  //   3. 两种错的代价不对称:
  //        慢模型给 90s → 中途 abort + 白跑一次生成, 用户看到"跑一半停下来"
  //                       (P3.5.34 当初要修的正是这个症状)
  //        快模型给 180s → 真卡住时多等 90 秒才重试
  //      前者是实际发生过的 bug, 后者只是在已经出问题的场景里多等一会。
  //
  //   4. 全代码库其它地方判断内外网用的都是前缀 (PerfCard.tsx:262 /
  //      types/audit.ts:7 的 startsWith)。原来这里是第二套判定。
  //
  // 保留下面这条替代用例, 钉住"前缀之外的不算" —— 这是原来那条真正想守的
  // 边界 (别让 90s 分支被无限扩大), 只是边界的位置挪了。
  it("不带 catfish-private- 前缀的一律 90s", () => {
    expect(computeIdleTimeoutMs("catfish-private")).toBe(90_000); // 少个连字符
    expect(computeIdleTimeoutMs("private-main")).toBe(90_000); // 缺 catfish-
    expect(computeIdleTimeoutMs("x-catfish-private-main")).toBe(90_000); // 前面有东西
  });
});
