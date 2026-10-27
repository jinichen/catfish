import { describe, expect, it } from "vitest";

import { parseStepLimit, stepLimitText } from "./agentLoopLimit";
import { translateHermesStatus } from "./hermesStatusZh";

// hermes agent/turn_finalizer.py 发的原文
const RAW = "⚠️ Iteration budget exhausted (60/60) — asking model to summarise";

describe("hermes 步数上限 (10/27)", () => {
  it("认出状态里的步数", () => {
    expect(parseStepLimit(RAW)).toEqual({ used: 60, max: 60 });
    expect(parseStepLimit("Compacting context…")).toBeNull();
    expect(parseStepLimit(undefined)).toBeNull();
  });

  it("提示说清楚是到了上限, 要点继续", () => {
    const text = stepLimitText({ used: 60, max: 60 });
    expect(text).toContain("60 步");
    expect(text).toContain("继续");
  });

  it("状态条显示中文, 不是「小鲶还在处理…」", () => {
    const view = translateHermesStatus(RAW);
    expect(view.text).toContain("60 步");
    expect(view.raw).toBe(RAW);
  });
});
