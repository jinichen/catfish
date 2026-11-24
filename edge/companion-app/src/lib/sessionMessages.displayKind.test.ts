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

import { chatMessageCount, loadSessionMessagesAsChat } from "./sessionMessages";

describe("hermes 的 session_meta 记账行", () => {
  const detail = {
    meta: { id: "s", messageCount: 3 },
    messages: [
      { id: 1, role: "user", content: "打开文件", timestamp: "2026-11-24T19:17:00Z" },
      { id: 2, role: "assistant", content: "在右边", timestamp: "2026-11-24T19:18:00Z" },
      { id: 3, role: "session_meta", content: "", timestamp: "2026-11-24T19:18:24Z" },
    ],
  } as never;
  it("不当成小鲶的回复画出来 (11/24 最后多一个空头像), 也不算进条数", () => {
    expect(loadSessionMessagesAsChat(detail).map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(chatMessageCount(detail)).toBe(2);
  });
});
