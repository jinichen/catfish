/** 对话 tab 顶层 —— 左侧 SessionList sidebar | 右侧 ChatPanel。
 *
 * Plan C Week 3 (P0-3.1 + 3.2):
 *   - 左侧拉 ~/.hermes/state.db sessions, 区分 cli/companion 来源
 *   - 点列表项 → sessions_get 拉全部 messages → ChatStore.loadSession
 *   - "+ 新对话" → reset store, persistedSessionId 清空, 下次 send 自动新建
 *   - 流式输出中, sidebar 切换 / 新建按钮禁用 (避免条件竞争)
 */

import { useCallback, useState } from "react";
import { useChat } from "../../hooks/useChat";
import { useChatStore } from "../../store/chat";
import { useCatalog } from "../../hooks/useCatalog";
import { getSession } from "../../lib/tauri";
import ChatPanel from "./ChatPanel";
import ChatModelPicker from "./ChatModelPicker";
import ChatSidebar from "./ChatSidebar";

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
    reset,
  } = useChat(defaultModel);

  const persistedSessionId = useChatStore((s) => s.persistedSessionId);
  const loadSession = useChatStore((s) => s.loadSession);

  /** 父组件持有 sidebar 的 refresh key —— 发完一条消息后 bump 让左侧列表重拉 */
  const [refreshKey, setRefreshKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  const handleSelect = useCallback(
    async (id: string) => {
      if (id === persistedSessionId) return; // 点的就是当前
      try {
        const detail = await getSession(id);
        loadSession(detail);
        setLoadError(null);
      } catch (e) {
        console.error("[catfish chat] 切换会话失败:", e);
        setLoadError(`切换失败: ${e}`);
      }
    },
    [persistedSessionId, loadSession],
  );

  const handleNew = useCallback(() => {
    if (messages.length === 0 && !persistedSessionId) return;
    reset();
    setLoadError(null);
  }, [messages.length, persistedSessionId, reset]);

  /** 包一层 send: 完成后 bump refreshKey 让 sidebar 看到新会话 / 新 message_count */
  const handleSend = useCallback(
    async (text: string, attachments: import("../../types/chat").Attachment[] = []) => {
      await send(text, attachments);
      setRefreshKey((k) => k + 1);
    },
    [send],
  );

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

          <ChatModelPicker current={model} onChange={setModel} />
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
            onReset={reset}
          />
        </div>
      </div>
    </div>
  );
}
