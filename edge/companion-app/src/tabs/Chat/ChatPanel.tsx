/** 聊天主面板 —— 消息流 + 输入框,自动滚到底部 */

import { useEffect, useRef } from "react";
import ChatMessage from "./ChatMessage";
import { groupToolSteps } from "../../lib/chatToolSteps";
import ChatInput from "./ChatInput";
import type { Attachment, ChatMessage as Msg } from "../../types/chat";
import { useAgentStore } from "../../store/agent";
import { useChatStore } from "../../store/chat";

import LifecycleStatus from "./LifecycleStatus";
import { ApprovalBanner, useApprovalBanner } from "../../components/ApprovalBanner";


interface Props {
  messages: Msg[];
  isStreaming: boolean;
  /** 当前正在流式输出的 assistant 消息 id —— 用来决定哪条显示光标 */
  streamingId: string | null;
  onSend: (text: string, attachments: Attachment[]) => void;
  /** 8/3: 按了停止但流还没停 (等 tool 返回) — 让按钮如实说话 */
  isCancelling?: boolean;
  onCancel: () => void;
  /** BL-HERMES013-RED-1A (5/13 ACP /queue): streaming 中排队下一条 */
  onEnqueue: (text: string, attachments: Attachment[]) => void;
  /** BL-COMPANION-RESEND (7/23 达华 POC 催): user msg hover → 🔄 重发 · 触发这个 */
  onResendFromUserMsg: (id: string) => void;
  /** BL-COMPANION-EDIT (7/23 P1): user msg hover → ✏️ 编辑 · confirm 后带 newContent 触发 */
  onEditAndResendUserMsg: (id: string, newContent: string) => void;
  // P3.5.20.1 (6/17): onSteer prop 砍 — steer 整链退役.
  onReset: () => void;
}

export default function ChatPanel({
  messages,
  isStreaming,
  streamingId,
  onSend,
  isCancelling,
  onCancel,
  onEnqueue,
  onResendFromUserMsg,
  onEditAndResendUserMsg,
  onReset,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // P3.5.18 Phase 2 (6/17 鸿波): hermes preflight 自动压缩 inline status. plugin P19 桥
  // status_callback → SSE. lib/chat.ts onLifecycle → useChat setLifecycleStatus.
  const lifecycleStatus = useChatStore((s) => s.lifecycleStatus);
  const transport = useChatStore((s) => s.transport);
  // P3.5.79+ (7/22 鸿波 catch "切会话滚位置错"): 追踪 session 首次渲染, 只 force-scroll 1
  // 次到底. 跟下面 messages 智能滚 effect 分工:
  //   - session 首次渲染 (切进) → 强制到底 (无论用户上一 session 在哪个位置)
  //   - 同 session 后续 messages (streaming / 新发送) → 走智能 logic (near bottom 才跟随)
  // 注: ChatState 里字段叫 persistedSessionId (老 session=真 uid / 新 session=null),
  //     不是 sessionId (那是 SessionAttachment 上的). 军规: 拿字段前先 read 全 interface.
  const persistedSessionId = useChatStore((s) => s.persistedSessionId);
  const scrolledSessionRef = useRef<string | null>(null);

  // 新消息或 streaming token 来 · 自动滚到底 (2 分支)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    // 分支 1 · session 切换 · 首次消息 render 后强制滚到底 (P3.5.79+ 7/22)
    // 老 bug: 只有 [messages] deps · 切 session 时 scrollTop 保留上一 session 的位置 ·
    // scrollHeight 是新 session 内容 · atBottom 十有八九 false · 且新 session 最后条通常
    // 是 assistant 不是 user · 两条件都不满足 → 不 scroll → 用户看到会话中间/顶部.
    // 修法: 追 scrolledSessionRef · 只在 persistedSessionId 变 + messages 已 populate
    // 时 force 一次. empty session 不设 ref · 等 messages 加载完后再 force.
    if (scrolledSessionRef.current !== persistedSessionId && messages.length > 0) {
      scrolledSessionRef.current = persistedSessionId;
      el.scrollTop = el.scrollHeight;
      return;
    }

    // 分支 2 · 同 session 内新消息/streaming · 智能滚 (原逻辑, 保阅读历史体验)
    // 用户手动往上滚后不强制拉回, 只在底部附近时跟随
    const threshold = 60;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    if (atBottom || messages[messages.length - 1]?.role === "user") {
      el.scrollTop = el.scrollHeight;
    }
  }, [persistedSessionId, messages]);

  // 等批准横幅 (10/16 抽成 components/ApprovalBanner, 跟早安卡片共用)
  const approval = useApprovalBanner();

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
    <div className="chat-panel">
      <div
        ref={scrollRef}
        className="chat-panel__thread"
      >
        <div className="chat-panel__content">
          {isEmpty && <EmptyState />}
          {/* 连续的纯工具调用消息合并成一组 "后台动作" (lib/chatToolSteps.ts) */}
          {groupToolSteps(messages).map(({ msg: m, memberIds }, i, all) => (
            <ChatMessage
              key={m.id}
              msg={m}
              isLast={i === all.length - 1}
              showCaret={isStreaming && !!streamingId && memberIds.includes(streamingId)}
              onResend={onResendFromUserMsg}
              onEditAndResend={onEditAndResendUserMsg}
              isStreaming={isStreaming}
            />
          ))}
        {/* P3.5.18 Phase 2 (6/17 鸿波): hermes preflight 自动压缩 inline 状态.
            stream done 后 useChat 清 lifecycleStatus = null → 自动消失.
            只 streaming 中 + lifecycleStatus 非 null 才显. 不打扰 message 流. */}
          {isStreaming && lifecycleStatus && <LifecycleStatus raw={lifecycleStatus} />}
          {/* 8/13: 降级提示。hermes 不可达时 chat.ts 会自动回落直连网关 ——
              能聊天, 但没有 agent loop / 工具 / 记忆。以前这一切是**静默**的,
              员工只会觉得"小鲶今天变笨了"。降级本身没问题, 不告诉人才有问题。
              transport 由 sendMessage 的 onTransportResolved 回报, 发过一条
              消息之后才有值 (null = 还不知道, 不显)。 */}
          {transport === "gateway" && (
            <div className="chat-panel__degraded" role="status">
              <span>
                简化模式 — 暂时用不了工具和记忆, 只能纯对话。
                检查小鲶助手服务是否在运行。
              </span>
            </div>
          )}
        </div>
      </div>
      <ApprovalBanner {...approval} />
      <ChatInput
        isStreaming={isStreaming}
        onSend={onSend}
        isCancelling={isCancelling}
        onCancel={onCancel}
        onEnqueue={onEnqueue}
        onReset={onReset}
      />
    </div>
  );
}

function EmptyState() {
  // BL-E11 命名权: 空状态 "我是小鲶" → 用员工自定义的名字 (默认 "小鲶")
  const agentName = useAgentStore((s) => s.name);
  return (
    <div className="chat-empty-state">
      {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 正式吉祥物 (空对话状态最显眼, 用最大的 mascot) */}
      <img
        src="/catfish-mascot.svg"
        alt={agentName}
        width={132}
        height={132}
      />
      <div className="chat-empty-state__title">我是{agentName}</div>
      <div className="chat-empty-state__copy">
        你的鲶鱼平台数字副手。
        <br />
        问我任何事 — 我会用工具帮你查、做、写。
      </div>
    </div>
  );
}
