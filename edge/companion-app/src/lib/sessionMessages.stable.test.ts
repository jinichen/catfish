import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { keepStableIds } from "./sessionMessages";

const m = (id: string, role: string, content: string, extra: Partial<ChatMessage> = {}) =>
  ({ id, role, content, ...extra }) as ChatMessage;

describe("keepStableIds 答完重读同一个会话", () => {
  it("屏幕上已有的消息沿用 id, 库里补出来的后台动作才是新的", () => {
    const img = [{ kind: "image", name: "a.png" }] as never;
    const prev = [m("u1", "user", "打开文件", { attachments: img }), m("a1", "assistant", "在右边。")];
    const next = [
      m("db-1", "user", "打开文件"),
      m("db-2", "assistant", "", { tool_calls: [{ id: "t", name: "search_files", args: {} }] as never }),
      m("db-3", "tool", "结果"),
      m("db-4", "assistant", "在右边。\n"),
    ];
    const out = keepStableIds(prev, next);
    expect(out.map((x) => x.id)).toEqual(["u1", "db-2", "db-3", "a1"]);
    expect(out[0].attachments).toBe(img); // 截图缩略图不丢
    expect(out[1].tool_calls).toHaveLength(1);
  });

  it("两条一样的话不会认成同一条; 微信新进来的是新 id", () => {
    const prev = [m("u1", "user", "好"), m("a1", "assistant", "收到"), m("u2", "user", "好"), m("a2", "assistant", "收到")];
    const next = [m("db-1", "user", "好"), m("db-2", "assistant", "收到"), m("db-3", "user", "好"), m("db-4", "assistant", "收到"), m("db-5", "user", "再来一条")];
    expect(keepStableIds(prev, next).map((x) => x.id)).toEqual(["u1", "a1", "u2", "a2", "db-5"]);
  });

  it("最后一条回复正文略有出入也认 (不闪)", () => {
    const out = keepStableIds([m("u", "user", "问"), m("a", "assistant", "答 一")], [m("db-1", "user", "问"), m("db-2", "assistant", "答 一。")]);
    expect(out[1].id).toBe("a");
    expect(out[1].content).toBe("答 一。");
  });
});
