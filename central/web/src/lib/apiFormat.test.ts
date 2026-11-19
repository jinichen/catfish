import { describe, expect, it } from "vitest";

import { apiFormatOf, formatMismatch, withApiFormat } from "./apiFormat";

describe("上游接口格式", () => {
  it("按前缀认格式, 换格式只换前缀", () => {
    expect(apiFormatOf("openai/qwen3.8-flash")).toBe("openai");
    expect(apiFormatOf("anthropic/qwen3.8-flash")).toBe("anthropic");
    expect(apiFormatOf("gemini/gemini-2.5-pro")).toBe("other");
    expect(withApiFormat("openai/qwen3.8-flash", "anthropic")).toBe("anthropic/qwen3.8-flash");
    expect(withApiFormat("qwen-plus", "openai")).toBe("openai/qwen-plus");
    expect(withApiFormat("gemini/x", "other")).toBe("gemini/x");
  });

  it("格式和供应商地址对不上时提醒 (百炼两种地址实测)", () => {
    const oa = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1";
    const an = "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic";
    expect(formatMismatch("anthropic/qwen3.8-flash", oa)).toMatch(/另建一个供应商/);
    expect(formatMismatch("anthropic/qwen3.8-flash", an)).toBeNull();
    expect(formatMismatch("openai/qwen3.8-flash", an)).toMatch(/Anthropic 兼容/);
    expect(formatMismatch("openai/qwen3.8-flash", oa)).toBeNull();
    expect(formatMismatch("anthropic/x", "https://api.example.com/anthropic/v1/messages")).toMatch(/自己补/);
    expect(formatMismatch("anthropic/x", "${INTERNAL_LLM_BASE}")).toBeNull();
    expect(formatMismatch("anthropic/x", null)).toBeNull();
  });
});
