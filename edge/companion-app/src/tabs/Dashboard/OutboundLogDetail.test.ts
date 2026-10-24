import { describe, expect, it } from "vitest";

import { messageText, newPartOfChat } from "./OutboundLogDetail";

describe("外发记录: 这次新发的", () => {
  it("最后一条助手回复之后的消息才是这次新发的", () => {
    const body = {
      messages: [
        { role: "system", content: "系统" },
        { role: "user", content: "第一句" },
        { role: "assistant", content: "回答" },
        { role: "user", content: "第二句" },
        { role: "tool", content: "工具结果" },
      ],
    };
    expect(newPartOfChat(body)?.map((m) => m.content)).toEqual(["第二句", "工具结果"]);
  });
  it("最后一条就是助手 → 退回最后一条; 不是对话格式 → null", () => {
    expect(newPartOfChat({ messages: [{ role: "assistant", content: "x" }] })?.length).toBe(1);
    expect(newPartOfChat({ input: "向量" })).toBeNull();
    expect(newPartOfChat("text")).toBeNull();
  });
  it("多段内容: 文字原样, 图片标出来", () => {
    expect(messageText([{ type: "text", text: "看图" }, { type: "image_url", image_url: { url: "data:..." } }])).toBe(
      "看图\n[图片]",
    );
  });
});
