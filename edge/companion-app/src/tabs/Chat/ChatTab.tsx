/** 对话 tab 顶层 —— 左侧 SessionList sidebar | 右侧 ChatPanel。
 *
 * Plan C Week 3 (P0-3.1 + 3.2):
 *   - 左侧拉 ~/.hermes/state.db sessions, 区分 cli/companion 来源
 *   - 点列表项 → sessions_get 拉全部 messages → ChatStore.loadSession
 *   - "+ 新对话" → reset store, persistedSessionId 清空, 下次 send 自动新建
 *   - 流式输出中, sidebar 切换 / 新建按钮禁用 (避免条件竞争)
 */

import { useCallback, useEffect, useState } from "react";
import { useChat } from "../../hooks/useChat";
import { useChatStore } from "../../store/chat";
import { useCatalog } from "../../hooks/useCatalog";
import { getSession } from "../../lib/tauri";
import ChatPanel from "./ChatPanel";
import ChatModelPicker from "./ChatModelPicker";
import ChatSidebar from "./ChatSidebar";
import ContextCounter from "./ContextCounter";  // BL-CONTEXT-COUNTER (5/13)

export default function ChatTab() {
  const { catalog } = useCatalog();
  const defaultModel =
    catalog?.default || catalog?.models?.[0]?.id || "catfish-private-main";

  const {
    messages,
    isStreaming,
    streamingId,
    model,
    setModel,
    send,
    cancel,
    cancelAndSend,  // BL-COMPANION-UX1 (5/12): 一键停止+发新消息
    enqueue,        // BL-HERMES013-RED-1A (5/13 ACP /queue): 排队下一条
    reset,
  } = useChat(defaultModel);

  const persistedSessionId = useChatStore((s) => s.persistedSessionId);
  const loadSession = useChatStore((s) => s.loadSession);

  /** 父组件持有 sidebar 的 refresh key —— 发完一条消息后 bump 让左侧列表重拉 */
  const [refreshKey, setRefreshKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  /** BL-COMPANION-UX2 (5/12): streaming 中点列表切换 → 先 abort 当前 stream
   * 再 loadSession. 老行为是 sidebar 禁用切换, 体验差 (鸿波: "锁死").
   */
  const handleSelect = useCallback(
    async (id: string) => {
      if (id === persistedSessionId) return; // 点的就是当前
      if (isStreaming) {
        cancel();                             // abort 当前 stream
        await new Promise((r) => setTimeout(r, 200));  // 等 finally cleanup
      }
      try {
        const detail = await getSession(id);
        loadSession(detail);
        setLoadError(null);
      } catch (e) {
        console.error("[catfish chat] 切换会话失败:", e);
        setLoadError(`切换失败: ${e}`);
      }
    },
    [persistedSessionId, loadSession, isStreaming, cancel],
  );

  /** BL-COMPANION-UX2 (5/12): streaming 中也能点"+ 新对话" → 先 abort 再 reset. */
  const handleNew = useCallback(async () => {
    if (messages.length === 0 && !persistedSessionId) return;
    if (isStreaming) {
      cancel();
      await new Promise((r) => setTimeout(r, 200));
    }
    reset();
    setLoadError(null);
  }, [messages.length, persistedSessionId, reset, isStreaming, cancel]);

  /** 包一层 send: 完成后 bump refreshKey 让 sidebar 看到新会话 / 新 message_count */
  const handleSend = useCallback(
    async (text: string, attachments: import("../../types/chat").Attachment[] = []) => {
      await send(text, attachments);
      setRefreshKey((k) => k + 1);
    },
    [send],
  );

  // BL-COMPANION-UX1 (5/12): streaming 中员工想发新消息, 一键 abort+发.
  // 跟 handleSend 同款包装 (setRefreshKey 给 session 列表刷新).
  const handleCancelAndSend = useCallback(
    async (text: string, attachments: import("../../types/chat").Attachment[] = []) => {
      await cancelAndSend(text, attachments);
      setRefreshKey((k) => k + 1);
    },
    [cancelAndSend],
  );

  // 5/7 BL-D14: 当前选中的 session 也自动 polling — 5s 一次重拉 detail.
  // 让微信 / 飞书 / 企微进来的新消息自动 append 到 chat 面板.
  //
  // 关键约束 (防体验崩):
  //   1. streaming 中不 poll (会跟当前流式响应冲突)
  //   2. 没选中 session 不 poll
  //   3. message_count 没变就不重 load (避免 React 重渲染抖动)
  useEffect(() => {
    if (!persistedSessionId) return;
    if (isStreaming) return;
    const POLL_MS = 5000;
    let lastMsgCount = messages.length;
    const t = window.setInterval(async () => {
      if (isStreaming) return;
      try {
        const detail = await getSession(persistedSessionId);
        // detail.meta.messageCount 是 state.db 真实总数
        // 如果跟当前 messages 长度差了 (微信/飞书/企微 进来新消息), reload
        const dbCount = detail.meta?.messageCount ?? 0;
        if (dbCount > lastMsgCount) {
          lastMsgCount = dbCount;
          loadSession(detail);
          // 同时 bump refreshKey, sidebar 也跟着刷
          setRefreshKey((k) => k + 1);
        }
      } catch {
        // 网络抖 / state.db 锁 — silent, 下次 tick 再试
      }
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [persistedSessionId, isStreaming, messages.length, loadSession]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        height: "100%",
        minHeight: 0,
      }}
    >
      <ChatSidebar
        activeId={persistedSessionId}
        onSelect={handleSelect}
        onNew={handleNew}
        refreshKey={refreshKey}
        busy={isStreaming}
      />

      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "var(--space-3) var(--space-4)",
            borderBottom: "1px solid var(--catfish-border)",
            background: "var(--catfish-bg-elevated)",
            gap: "var(--space-3)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              fontSize: 13,
              fontWeight: 600,
              minWidth: 0,
            }}
          >
            {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 小尺寸正式头像 */}
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <img src="/catfish-avatar.svg" alt="" width={18} height={18} style={{ display: "block" }} />
              对话
            </span>
            {persistedSessionId && (
              <span
                title={persistedSessionId}
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                  fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 200,
                }}
              >
                · {persistedSessionId}
              </span>
            )}
            {messages.length > 0 && (
              <span
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                }}
              >
                · {messages.length} 条
              </span>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            {/* BL-CONTEXT-COUNTER (5/13): prompt_tokens / context_window 状态指示
                配合 5/13 早上加的 _is_context_overflowed 监控, 让员工自己看到
                当前会话烧到 context 多少, 接近上限主动 Cmd+N. 没数据时不渲染. */}
            <ContextCounter />
            <ChatModelPicker current={model} onChange={setModel} />
          </div>
        </header>

        {loadError && (
          <div
            style={{
              padding: "8px 12px",
              fontSize: 12,
              background: "rgba(220, 38, 38, 0.08)",
              color: "#dc2626",
              borderBottom: "1px solid var(--catfish-border)",
            }}
          >
            {loadError}
          </div>
        )}

        <div style={{ flex: 1, minHeight: 0 }}>
          <ChatPanel
            messages={messages}
            isStreaming={isStreaming}
            streamingId={streamingId}
            onSend={handleSend}
            onCancel={cancel}
            onCancelAndSend={handleCancelAndSend}
            onEnqueue={enqueue}
            onReset={reset}
          />
        </div>
      </div>
    </div>
  );
}
