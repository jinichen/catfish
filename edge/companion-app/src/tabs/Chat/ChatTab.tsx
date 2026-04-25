/** 对话 tab 顶层 —— 头部(标题 + 模型选择 + 新对话) + 主面板 */

import { useChat } from "../../hooks/useChat";
import ChatPanel from "./ChatPanel";
import ChatModelPicker from "./ChatModelPicker";
import { useCatalog } from "../../hooks/useCatalog";

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

  // catalog 加载后, 如果当前 model 还是初始 fallback, 校正成 catalog default
  // (不放 useEffect 里, 简单起见 mount 后用户手动选即可)

  return (
    <div
      style={{
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
          }}
        >
          <span>🐟 对话</span>
          {messages.length > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                fontWeight: 400,
              }}
            >
              · {messages.length} 条消息
            </span>
          )}
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
          }}
        >
          <ChatModelPicker current={model} onChange={setModel} />
          <button
            onClick={() => {
              if (messages.length === 0) return;
              if (confirm("清空当前对话开始新对话？")) reset();
            }}
            disabled={messages.length === 0 || isStreaming}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              background: "transparent",
              color: "var(--catfish-text-muted)",
              cursor:
                messages.length === 0 || isStreaming ? "default" : "pointer",
            }}
          >
            + 新对话
          </button>
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0 }}>
        <ChatPanel
          messages={messages}
          isStreaming={isStreaming}
          streamingId={streamingId}
          onSend={send}
          onCancel={cancel}
          onReset={reset}
        />
      </div>
    </div>
  );
}
