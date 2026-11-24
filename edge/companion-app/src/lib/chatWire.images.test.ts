import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../types/chat";
import { WIRE_IMAGE_BUDGET_CHARS, WIRE_IMAGE_MAX_COUNT, toWire } from "./chatWire";

const img = (name: string, size: number) => ({
  kind: "image" as const, mimeType: "image/png", name, sizeBytes: size, base64: "A".repeat(size),
  keptPath: `/u/${name}`,
});
const user = (id: string, n: number, size: number): ChatMessage =>
  ({ id, role: "user", content: `题 ${id}`, attachments: Array.from({ length: n }, (_, j) => img(`${id}-${j}.png`, size)) }) as ChatMessage;
const reply = (id: string): ChatMessage => ({ id, role: "assistant", content: "答" }) as ChatMessage;
const wireSize = (ms: ChatMessage[]) => JSON.stringify(toWire(ms)).length;

describe("toWire 只带最近的图 (考试截图把请求撑过 10MB 的事)", () => {
  it("40 轮各 1 张 400KB 截图: 请求不超过预算, 最新一张一定在, 旧的变成带路径的文字", () => {
    const ms: ChatMessage[] = [];
    for (let i = 0; i < 40; i++) ms.push(user(`q${i}`, 1, 400_000), reply(`a${i}`));
    ms.push({ id: "last", role: "user", content: "这题呢" } as ChatMessage);
    const wire = toWire(ms);
    expect(wireSize(ms)).toBeLessThan(WIRE_IMAGE_BUDGET_CHARS + 200_000);
    const multipart = wire.filter((w) => Array.isArray(w.content));
    expect(multipart.length).toBe(Math.min(WIRE_IMAGE_MAX_COUNT, Math.floor(WIRE_IMAGE_BUDGET_CHARS / 400_000)));
    expect(Array.isArray(wire[wire.length - 3].content)).toBe(true); // q39 最新那张
    expect(wire[0].content).toContain("[图片「q0-0.png」前面已经发过");
    expect(wire[0].content).toContain("/u/q0-0.png");
  });

  it("最新一条消息的图就算超预算也全带上", () => {
    const ms = [user("old", 1, 100), reply("a"), user("now", 3, WIRE_IMAGE_BUDGET_CHARS)];
    const wire = toWire(ms);
    const last = wire[2].content as Array<{ type: string }>;
    expect(last.filter((p) => p.type === "image_url")).toHaveLength(3);
    expect(typeof wire[0].content).toBe("string");
  });

  it("图少的时候跟以前一样全带", () => {
    const ms = [user("a", 2, 1000), reply("x"), user("b", 1, 1000)];
    expect(toWire(ms).filter((w) => Array.isArray(w.content))).toHaveLength(2);
  });
});
