// @vitest-environment jsdom
// 11/17 出图 / 出视频模型: 按张 / 按秒计费、表单校验
import { describe, expect, it } from "vitest";

import { emptyModel, isMediaMode, modeUnit, validateModel, type ModelConfig } from "./model_config";
import { costRMB, setRuntimeModelMeta, totalCostRMB } from "./modelDisplay";

describe("出图 / 出视频的成本", () => {
  it("张数 / 秒数 × 单价, 跟 token 成本相加", () => {
    setRuntimeModelMeta([
      { id: "img", price_per_unit: 0.2 },
      { id: "t2v", price_per_unit: 0.6 },
      { id: "chat", price_per_1k_tokens: 0.002 },
    ]);
    expect(costRMB({ model: "img", total_tokens: 0, tokens_in: 0, tokens_out: 0, media_units: 3 })).toBeCloseTo(0.6);
    expect(costRMB({ model: "t2v", total_tokens: 0, tokens_in: 0, tokens_out: 0, media_units: 10 })).toBeCloseTo(6);
    expect(
      totalCostRMB([
        { model: "img", total_tokens: 0, tokens_in: 0, tokens_out: 0, media_units: 1 },
        { model: "chat", total_tokens: 1000, tokens_in: 1000, tokens_out: 0, media_units: 0 },
      ]),
    ).toBeCloseTo(0.202);
  });

  it("没配单价按 0 算, 不套 token 兜底价", () => {
    setRuntimeModelMeta([{ id: "img2" }]);
    expect(costRMB({ model: "img2", total_tokens: 0, tokens_in: 0, tokens_out: 0, media_units: 5 })).toBe(0);
  });
});

describe("表单校验", () => {
  const base = (): ModelConfig => ({
    ...emptyModel(),
    name: "wan-t2v",
    display_name: "万相文生视频",
    mode: "text_to_video",
    upstream: { model: "dashscope/wan2.6-t2v", provider: "dashscope" },
  });

  it("用途 / 单位", () => {
    expect(isMediaMode("text_to_video")).toBe(true);
    expect(isMediaMode("chat")).toBe(false);
    expect(modeUnit("image_to_video")).toBe("秒");
    expect(modeUnit("text_to_image")).toBe("张");
  });

  it("合法的视频模型过", () => {
    expect(validateModel({ ...base(), price_per_unit: 0.6, media: { min_duration: 2, max_duration: 15 } })).toEqual([]);
  });

  it("负单价、时长范围反了、非整数", () => {
    const errs = validateModel({ ...base(), price_per_unit: -1, media: { min_duration: 10, max_duration: 5, default_duration: 2.5 } });
    expect(errs).toContain("单价不能是负数");
    expect(errs).toContain("最短时长不能大于最长时长");
    expect(errs).toContain("默认时长要是正整数");
  });
});
