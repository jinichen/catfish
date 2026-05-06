/** 主窗监听 chat store 状态变化, emit 事件给桌宠副窗.
 *
 * BL-E27 一次到位 (5/5 凌晨): 桌宠 4 状态联动 LLM 真实状态.
 *   - 没在流 + 最近一条不是刚完成 = idle
 *   - 在流 + assistant 还在生成内容 = thinking
 *   - 在流 + 有 tool_calls 进行中 = running
 *   - 刚完成 (isStreaming false 后 2 秒内) = done → 桌宠跳一下
 *
 * 桌宠 (pet.tsx) listen 'catfish:agent_status' 事件, 切 CSS 动画.
 *
 * 本 hook 在 App.tsx mount 一次即可, 全局生效.
 */

import { useEffect, useRef } from "react";

import { petEmitStatus } from "../lib/tauri";
import { useChatStore } from "../store/chat";

type AgentStatus = "idle" | "thinking" | "running" | "done";

export function usePetStatusBroadcast(): void {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);

  // 上次 emit 的状态, 防同状态反复发
  const lastStatusRef = useRef<AgentStatus | null>(null);
  const wasStreamingRef = useRef<boolean>(false);

  useEffect(() => {
    let nextStatus: AgentStatus = "idle";

    if (isStreaming) {
      // 看最新 assistant 是不是有进行中的 tool_calls
      const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
      const hasRunningTool = lastAssistant?.tool_calls?.some(
        (tc) => tc.status === "running" || tc.status === "pending",
      );
      nextStatus = hasRunningTool ? "running" : "thinking";
    } else if (wasStreamingRef.current) {
      // 刚从 streaming → 非 streaming, 切到 done. (桌宠那边收到 done 自动 2s 后回 idle)
      nextStatus = "done";
    } else {
      nextStatus = "idle";
    }

    wasStreamingRef.current = isStreaming;

    if (lastStatusRef.current !== nextStatus) {
      lastStatusRef.current = nextStatus;
      // 5/6: 走 Rust 命令 emit_to 桌宠 (frontend emitTo 不可靠)
      petEmitStatus(nextStatus).catch((e) =>
        console.warn("pet_emit_status failed:", e),
      );
    }
  }, [messages, isStreaming]);
}
