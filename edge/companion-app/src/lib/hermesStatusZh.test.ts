/** hermes 状态提示的中文 (10/15)。原文抄自 hermes v0.22 源码。 */
import { describe, expect, it } from "vitest";

import { translateHermesStatus as t } from "./hermesStatusZh";

describe("hermes 状态中文", () => {
  it("压缩上下文: 开始 / 还在压 翻成同一句 (计时不归零), 带说明", () => {
    const a = t("🗜️ Compacting context — summarizing earlier conversation so I can continue...");
    const b = t("🗜️ Compacting context — still summarizing earlier conversation so I can continue...");
    expect(a.text).toBe(b.text);
    expect(a.text).toContain("整理成摘要");
    expect(a.hint).toContain("不会丢");
    expect(t("✓ Context compaction complete — continuing turn...").text).toContain("整理完了");
  });

  it("重试带上秒数和次数", () => {
    expect(t("⏳ Retrying in 4.6s (attempt 2/5)...").text).toBe("模型那边没响应, 5 秒后自动重试 (第 2/5 次)");
    expect(t("⏳ Provider temporarily unavailable — retrying automatically in 30s (cycle 1/3)").text)
      .toBe("模型服务暂时不可用, 30 秒后自动重试 (第 1/3 轮)");
  });

  it("等上一个任务 / 换备用模型 / 加载模型", () => {
    expect(t("⏳ Still waiting for the other Hermes process on this session (42s)...").text).toContain("已等 42 秒");
    expect(t("↻ Switched to fallback: qwen3-max (provider custom)").text).toBe("主模型没响应, 换用备用模型");
    expect(t("⏳ loading qwen3 into memory — 40%").text).toBe("正在加载模型 40%");
  });

  it("认不出的英文不直接给人看, 原文留着; 中文原样", () => {
    const v = t("Some brand new upstream status line");
    expect(v.text).toBe("小鲶还在处理…");
    expect(v.raw).toBe("Some brand new upstream status line");
    expect(t("🤔 正在思考…").text).toBe("🤔 正在思考…");
  });
});
