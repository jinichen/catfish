/** 10/22: hermes 把失败当正文返回时的识别。v0.22 英文模板 + 员工机 P62 中文模板都要认。 */
import { describe, expect, it } from "vitest";

import { isUpstreamErrorAsContent } from "./upstreamErrorGuard";

describe("isUpstreamErrorAsContent", () => {
  it.each([
    "API call failed after 3 retries: An error occurred during streaming",
    "custom didn't answer after 3 attempts — it looks temporarily unavailable.\n\nProvider said: x",
    "这次没能从模型拿到回复 (试了 3 次)。可以过一会儿再发一次, 或者换个模型。\n\n原因: 上游过载 (503)",
  ])("认出: %s", (text) => {
    expect(isUpstreamErrorAsContent(text)).toBe(true);
  });

  it("正常回答不误伤 (长文本即使提到这些词也不算)", () => {
    expect(isUpstreamErrorAsContent("明天的会议安排如下……")).toBe(false);
    expect(isUpstreamErrorAsContent("这次没能从模型拿到回复".padEnd(600, "。"))).toBe(false);
  });
});
