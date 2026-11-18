import { describe, expect, it } from "vitest";

import { voiceMissing, voiceTag } from "./voiceSupport";

describe("🎤 能转哪些语言 (按实际装的组件)", () => {
  it("英语 / 中文看会议组件, 其他语言看高精度组件", () => {
    const none = { base: false, hq: false }, base = { base: true, hq: false }, both = { base: true, hq: true };
    expect(voiceMissing("en", none)).toMatch(/装会议组件/);
    expect(voiceMissing("en", base)).toBeNull();
    expect(voiceMissing("zh", base)).toBeNull();
    expect(voiceMissing("ja", base)).toMatch(/高精度转写组件/);
    expect(voiceMissing("ja", none)).toMatch(/先在「会议」页装会议组件, 再装「高精度转写组件」/);
    expect(voiceMissing("ja", both)).toBeNull();
    expect(voiceMissing("ja", null)).toBeNull();            // 还没读到状态不乱提示
    expect(voiceTag("en", none)).toBe(" (🎤 要会议组件)");
    expect(voiceTag("ko", base)).toBe(" (🎤 要高精度组件)");
    expect(voiceTag("ko", both)).toBe("");
  });
});
