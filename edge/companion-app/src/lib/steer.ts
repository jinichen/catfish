/** BL-HERMES013-RED-1B (5/13 鸿波 ACP /steer 等价 — 路径 A 断流 + 续接) helper.
 *
 * 单独提一个文件是为了:
 *  1. 让 steer.test.ts 能干净 import (不拉 lib/chat.ts → lib/env.ts → import.meta.env
 *     污染, 那个东西 vite 才认识, tsx / node 直接跑会炸).
 *  2. 逻辑独立 — applySteerPrefix 是纯函数, 不依赖 streamChat / fetch / store.
 *
 * UI 用法见 hooks/useChat.ts steer() + tabs/Chat/ChatInput.tsx [🎯 改主意] 按钮.
 */

/** 给中途插话的 user msg 拼 STEER prefix.
 *  让 LLM 知道自己被用户打断了, 已生成内容只是半成品, 应该综合考虑两边继续.
 *  不破坏 UI 显示 — UI 那边读 m.content (干净的用户原话), 这里只是 wire 给 LLM.
 *
 *  steered.atContent: LLM 被打断时已生成的内容 (用户在 streaming 中按 [🎯 改主意]
 *      时, useChat 拿当前 streamingId 对应 assistant message 的 content). 取末尾
 *      200 字给 LLM 当上下文, 太长浪费 token, 太短 LLM 不知道在哪被打断.
 */
export function applySteerPrefix(
  content: string,
  steered: { atContent: string } | undefined,
): string {
  if (!steered) return content;
  const partial = steered.atContent.trim();
  // 取最后 200 字 (LLM 看到自己刚说的尾巴, 知道在哪被打断), 太长浪费 token.
  const tail = partial.slice(-200);
  const note = tail
    ? `(我打断你时你正说到: "...${tail}")`
    : "(我打断了你, 你还没开口)";
  return `[STEER · 用户中途插话] ${note} 现在改方向, 综合考虑两边继续:\n\n${content}`;
}
