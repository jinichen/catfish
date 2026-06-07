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
  //
  // E2 (6/6 taste-skill 改造): UI 按 redesign-existing-projects skill 重写.
  // 5 大类全过 (typography / color / layout / interactivity / content), 7 步
  // fix priority 走了 1-5 步. CSS class 走 globals.css `.approval-banner*`
  // (内联 style 没法加 :hover :active :focus). 新增 loading state +
  // 60s 倒计时 (hermes config approvals.timeout 是 60).
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [submittingChoice, setSubmittingChoice] = useState<
    "once" | "session" | "always" | "deny" | null
  >(null);
  const [secondsLeft, setSecondsLeft] = useState(60);

  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<PendingApproval>;
      if (ev.detail?.approval_session_key) {
        setPending(ev.detail);
        setSecondsLeft(60);  // 重置倒计时
      }
    };
    window.addEventListener("catfish:approval-pending", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-pending", handler as EventListener);
  }, []);

  // 60s countdown — hermes _gateway_approval 默认 timeout 60s,
  // 超时 hermes 自己 fallback 返回 "BLOCKED: timed out", banner 没意义自动消失.
  useEffect(() => {
    if (!pending) return;
    const tick = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(tick);
          setPending(null);  // timeout 自动清, 防 stale banner
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [pending]);

  const handleApproval = async (choice: "once" | "session" | "always" | "deny") => {
    if (!pending || submittingChoice) return;
    setSubmittingChoice(choice);
    try {
      await toolBridgeChatApproval(pending.approval_session_key, choice);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[P44.3] chat_approval RPC 失败:", err);
    }
    setSubmittingChoice(null);
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
        <div className="approval-banner">
          <div className="approval-banner__header">
            <span>hermes 等批准</span>
            <code className="pattern">{pending.pattern_key}</code>
            <span className="countdown" aria-live="polite">
              {secondsLeft}s
            </span>
          </div>
          <div className="approval-banner__code">{pending.command}</div>
          <div className="approval-banner__actions">
            <button
              className="approval-banner__btn-primary"
              onClick={() => void handleApproval("once")}
              disabled={submittingChoice !== null}
              autoFocus
            >
              {submittingChoice === "once" && <span className="approval-banner__spinner" />}
              批准
            </button>
            <button
              className="approval-banner__btn-link"
              onClick={() => void handleApproval("session")}
              disabled={submittingChoice !== null}
            >
              {submittingChoice === "session" && <span className="approval-banner__spinner" />}
              本会话允许
            </button>
            <button
              className="approval-banner__btn-link"
              onClick={() => void handleApproval("always")}
              disabled={submittingChoice !== null}
            >
              {submittingChoice === "always" && <span className="approval-banner__spinner" />}
              始终允许
            </button>
            <button
              className="approval-banner__btn-deny"
              onClick={() => void handleApproval("deny")}
              disabled={submittingChoice !== null}
            >
              {submittingChoice === "deny" && <span className="approval-banner__spinner" />}
              拒绝
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
