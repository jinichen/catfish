/** 主动提醒取「员工最后一句话」要跳过 hermes 的内部消息 (10/25)。
 *
 * 鸿波截图: 「640 分钟没动静了。你刚说"[CONTEXT COMPACTION — REFERENCE ONLY] Ea...", 现在还在弄吗?」——
 * hermes 压缩上下文后, 摘要和占位都以 role=user 存进历史, 被当成了员工最后说的话。
 * 标记文字照抄 hermes-agent agent/context_compressor.py。
 */
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { detectFocusReturn, detectSilence } from "./triggers";

const NOW = new Date(2026, 9, 25, 10, 0);
const at = (minsAgo: number) => new Date(NOW.getTime() - minsAgo * 60_000).toISOString();
const msg = (role: ChatMessage["role"], content: string, minsAgo: number, displayKind?: string): ChatMessage =>
  ({ id: `${role}-${minsAgo}`, role, content, ts: at(minsAgo), displayKind }) as ChatMessage;

const SUMMARY = "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below. 员工去弄材料";
const PLACEHOLDER =
  "Continue from the compressed conversation context above. This marker exists because no human user turn was available.";

describe("detectSilence · 跳过压缩摘要 / 占位", () => {
  it("压缩后最后一条是摘要 + 占位: 不拿它们当员工的话 (员工最后说话后小鲶已回过, 不催)", () => {
    const messages = [
      msg("user", "我去弄一下申报材料", 700),
      msg("assistant", "好的", 699),
      msg("user", SUMMARY, 650),
      msg("user", PLACEHOLDER, 640),
    ];
    expect(detectSilence({ messages, now: NOW })).toBeNull();
    // 截图那次: 最后一条就是摘要 (摘要正文里有「弄」, 老逻辑会引用它)
    expect(detectSilence({ messages: messages.slice(0, 3), now: NOW })).toBeNull();
  });

  it("打了 compaction 标记的摘要也跳过; 员工真说的话照常引用", () => {
    const messages = [msg("user", "我去弄一下申报材料", 640), msg("user", SUMMARY, 600, "compaction")];
    const r = detectSilence({ messages, now: NOW });
    expect(r?.message).toContain('你刚说"我去弄一下申报材料"');
    expect(r?.message).not.toContain("CONTEXT");
    expect(r?.context.last_user_text).toBe("我去弄一下申报材料");
  });

  it("引用的是气泡里那段话, 不带附件材料", () => {
    const content = "帮我看看这个方案\n\n=== 附件: 方案.pdf (pdf · 2 页) ===\n正文……";
    const r = detectSilence({ messages: [msg("user", content, 60)], now: NOW });
    expect(r?.context.last_user_text).toBe("帮我看看这个方案");
  });
});

describe("detectFocusReturn · 跳过压缩摘要 / 占位", () => {
  it("引用员工最后真说的话, 不是摘要", () => {
    const messages = [
      msg("user", "我去弄一下申报材料", 200),
      msg("assistant", "好的", 199),
      msg("user", SUMMARY, 150),
      msg("user", PLACEHOLDER, 140),
    ];
    const r = detectFocusReturn({
      lastFocusLeftTs: NOW.getTime() - 130 * 60_000,
      lastFocusReturnTs: NOW.getTime() - 10_000,
      now: NOW,
      messages,
    });
    expect(r?.message).toContain('刚才你说"我去弄一下申报材料"');
    expect(r?.message).not.toContain("CONTEXT");
  });

  it("只有摘要 / 占位, 没有员工的话 → 不出声", () => {
    const r = detectFocusReturn({
      lastFocusLeftTs: NOW.getTime() - 130 * 60_000,
      lastFocusReturnTs: NOW.getTime() - 10_000,
      now: NOW,
      messages: [msg("user", SUMMARY, 150), msg("user", PLACEHOLDER, 140)],
    });
    expect(r).toBeNull();
  });
});
