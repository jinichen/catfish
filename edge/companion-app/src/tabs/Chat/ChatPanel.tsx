/** 聊天主面板 —— 消息流 + 输入框,自动滚到底部 */

import { useEffect, useRef, useState } from "react";
import ChatMessage from "./ChatMessage";
import ChatInput from "./ChatInput";
import type { Attachment, ChatMessage as Msg } from "../../types/chat";
import { useAgentStore } from "../../store/agent";
import { toolBridgeChatApproval } from "../../lib/tauri";

interface PendingApproval {
  approval_session_key: string;
  command: string;
  description: string;
  pattern_key: string;
}

interface Props {
  messages: Msg[];
  isStreaming: boolean;
  /** 当前正在流式输出的 assistant 消息 id —— 用来决定哪条显示光标 */
  streamingId: string | null;
  onSend: (text: string, attachments: Attachment[]) => void;
  onCancel: () => void;
  /** BL-COMPANION-UX1 (5/12): streaming 中一键 abort + 发新消息 */
  onCancelAndSend: (text: string, attachments: Attachment[]) => void;
  /** BL-HERMES013-RED-1A (5/13 ACP /queue): streaming 中排队下一条 */
  onEnqueue: (text: string) => void;
  /** BL-HERMES013-RED-1B (5/13 ACP /steer): streaming 中中途插话改方向
   *  (路径 A 断流 + 续接 — abort 当前 SSE 后用 partial content 续 send) */
  onSteer: (text: string) => void;
  onReset: () => void;
}

export default function ChatPanel({
  messages,
  isStreaming,
  streamingId,
  onSend,
  onCancel,
  onCancelAndSend,
  onEnqueue,
  onSteer,
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

  // P44.3 (6/6 鸿波 marathon): floating approval banner — 在 LLM stream 期间立即弹.
  // 背景: hermes _gateway_approval 阻塞等 decision 时, chat completions stream
  // 没 finalize → ChatToolCall 卡片不渲染 → P27 inline button 不出. hermes 60s
  // 后 timeout 解 block 太晚, button 失效. 这里 listen catfish:approval-pending
  // event (P15 _approval_notify 发的 SSE event), 立即弹 banner 给员工点.
  const [pending, setPending] = useState<PendingApproval | null>(null);
  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<PendingApproval>;
      if (ev.detail?.approval_session_key) {
        setPending(ev.detail);
      }
    };
    window.addEventListener("catfish:approval-pending", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-pending", handler as EventListener);
  }, []);

  const handleApproval = async (choice: "once" | "session" | "always" | "deny") => {
    if (!pending) return;
    try {
      await toolBridgeChatApproval(pending.approval_session_key, choice);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[P44.3] chat_approval RPC 失败:", err);
    }
    setPending(null);
  };

  // P27 (6/5 鸿波): 监听 ChatToolCall approval inline button 触发的 send event.
  // ChatToolCall → window.dispatchEvent('catfish:approval-send', detail.text)
  // → 这里 listen, 直接调 onSend 走跟员工手动发同款 path (走 hermes /approve handler
  // + P14 中文 alias monkey-patch 都能 work, 不需要新 endpoint).
  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<{ text: string }>;
      const text = ev.detail?.text;
      if (text && typeof text === "string") {
        onSend(text, []);
      }
    };
    window.addEventListener("catfish:approval-send", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-send", handler as EventListener);
  }, [onSend]);

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
            // BL-TASK-ASSESS-3-UI (5/15): 嘴炮断言 [⏩ 催它继续] 按钮 → 发"继续",
            // 走跟用户手动发完全一样的 onSend 路径, 不走 gateway 重试.
            onNudge={() => onSend("继续", [])}
          />
        ))}
      </div>
      {pending && (
        <div
          style={{
            padding: "10px 14px",
            margin: "0 var(--space-3) 8px",
            borderRadius: 8,
            background: "var(--catfish-bg-elevated, #1a1f2e)",
            border: "1px solid var(--catfish-cyan-dim, #0891b2)",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
            ⚠️ hermes 等批准: <code style={{ background: "transparent" }}>{pending.pattern_key}</code>
          </div>
          <div
            style={{
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              color: "var(--catfish-text)",
              background: "var(--catfish-bg)",
              padding: "6px 8px",
              borderRadius: 4,
              maxHeight: 80,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {pending.command}
          </div>
          <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
            <button
              onClick={() => void handleApproval("once")}
              style={btnStyle("var(--status-ok, #16a34a)")}
            >
              ✓ 批准
            </button>
            <button
              onClick={() => void handleApproval("session")}
              style={btnStyle("var(--catfish-cyan-dim, #0891b2)")}
            >
              ✓ 本会话批准
            </button>
            <button
              onClick={() => void handleApproval("always")}
              style={btnStyle("var(--catfish-cyan-dim, #0891b2)")}
            >
              ✓ 始终批准
            </button>
            <button
              onClick={() => void handleApproval("deny")}
              style={btnStyle("var(--status-err, #dc2626)")}
            >
              ✗ 拒绝
            </button>
          </div>
        </div>
      )}
      <ChatInput
        isStreaming={isStreaming}
        onSend={onSend}
        onCancel={onCancel}
        onCancelAndSend={onCancelAndSend}
        onEnqueue={onEnqueue}
        onSteer={onSteer}
        onReset={onReset}
      />
    </div>
  );
}

function btnStyle(color: string): React.CSSProperties {
  return {
    background: "transparent",
    border: `1px solid ${color}`,
    color,
    borderRadius: 4,
    padding: "4px 10px",
    fontSize: 12,
    cursor: "pointer",
    fontWeight: 500,
  };
}

function EmptyState() {
  // BL-E11 命名权: 空状态 "我是小鲶" → 用员工自定义的名字 (默认 "小鲶")
  const agentName = useAgentStore((s) => s.name);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        // BL-EMPTY-STATE-CENTER (5/16): 原 60% 让内容居中在前 60% 区域 = 视觉
        // 30% 位置偏上. 改 100% 让内容真居中 (视觉 50%), 跟下方输入框形成
        // 自然眼神动线 (居中 mascot → 向下扫到输入框).
        height: "100%",
        textAlign: "center",
        color: "var(--catfish-text-muted)",
      }}
    >
      {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 正式吉祥物 (空对话状态最显眼, 用最大的 mascot) */}
      <img
        src="/catfish-mascot.svg"
        alt={agentName}
        width={120}
        height={120}
        style={{ marginBottom: "var(--space-3)" }}
      />
      <div style={{ fontSize: 18, color: "var(--catfish-text)", marginBottom: 6 }}>
        我是{agentName}
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.6, maxWidth: 360 }}>
        你的鲶鱼平台数字副手。
        <br />
        问我任何事 — 我会用工具帮你查、做、写。
      </div>
    </div>
  );
}
