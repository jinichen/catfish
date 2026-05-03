/** 聊天主面板 —— 消息流 + 输入框,自动滚到底部 */

import { useEffect, useRef } from "react";
import ChatMessage from "./ChatMessage";
import ChatInput from "./ChatInput";
import type { Attachment, ChatMessage as Msg } from "../../types/chat";

interface Props {
  messages: Msg[];
  isStreaming: boolean;
  /** 当前正在流式输出的 assistant 消息 id —— 用来决定哪条显示光标 */
  streamingId: string | null;
  onSend: (text: string, attachments: Attachment[]) => void;
  onCancel: () => void;
  onReset: () => void;
}

export default function ChatPanel({
  messages,
  isStreaming,
  streamingId,
  onSend,
  onCancel,
  onReset,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息或 streaming token 来,自动滚到底
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // 用户手动往上滚后,不强制拉回(给阅读历史的体验);只在底部附近时跟随
    const threshold = 60;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    if (atBottom || messages[messages.length - 1]?.role === "user") {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const isEmpty = messages.length === 0;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--catfish-bg)",
      }}
    >
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--space-6) var(--space-6) var(--space-3)",
        }}
      >
        {isEmpty && <EmptyState />}
        {messages.map((m) => (
          <ChatMessage
            key={m.id}
            msg={m}
            // 只对**当前正在流式的那条 assistant** 显示光标。
            // 双重判断：全局 isStreaming + msg.id 跟当前流的 id 匹配。
            // 这避免了"历史 assistant 消息也显示光标"的 bug。
            showCaret={isStreaming && m.id === streamingId}
          />
        ))}
      </div>
      <ChatInput
        isStreaming={isStreaming}
        onSend={onSend}
        onCancel={onCancel}
        onReset={onReset}
      />
    </div>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "60%",
        textAlign: "center",
        color: "var(--catfish-text-muted)",
      }}
    >
      {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 正式吉祥物 (空对话状态最显眼, 用最大的 mascot) */}
      <img
        src="/catfish-mascot.svg"
        alt="小鲶"
        width={120}
        height={120}
        style={{ marginBottom: "var(--space-3)" }}
      />
      <div style={{ fontSize: 18, color: "var(--catfish-text)", marginBottom: 6 }}>
        我是小鲶
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.6, maxWidth: 360 }}>
        你的鲶鱼平台数字副手。
        <br />
        问我任何事 — 我会用工具帮你查、做、写。
      </div>
    </div>
  );
}
