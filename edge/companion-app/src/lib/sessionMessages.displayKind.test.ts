import { describe, expect, it } from "vitest";

import { dbMessageToChat } from "./sessionMessages";

/** 10/16: hermes 自己打的显示标记要原样带到 ChatMessage, 组件按它先于 role 决定怎么显示。 */
describe("dbMessageToChat · displayKind 透传", () => {
  const base = { id: 7, role: "assistant", content: "[PRIOR CONTEXT — for reference only] …", timestamp: "2026-10-16T08:00:00Z" };

  it("压缩摘要 / 隐藏 / 系统说明 标记原样带过去 (assistant 身份的摘要也一样)", () => {
    expect(dbMessageToChat({ ...base, displayKind: "compaction" }).displayKind).toBe("compaction");
    expect(dbMessageToChat({ ...base, displayKind: "hidden" }).displayKind).toBe("hidden");
    expect(dbMessageToChat({ ...base, role: "user", displayKind: "internal_notification" }).displayKind).toBe("internal_notification");
  });

  it("老库没标 → undefined, 不编造", () => {
    const m = dbMessageToChat(base);
    expect(m.displayKind).toBeUndefined();
    expect(m.content).toBe(base.content);  // 正文不动, 由 userBubbleText 退路去认
  });
});
