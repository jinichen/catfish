/** 单条消息渲染 —— user / assistant / 错误状态 */

import { Markdown } from "../../lib/markdown";
import type { ChatMessage as Msg } from "../../types/chat";
import ChatToolCall from "./ChatToolCall";

interface Props {
  msg: Msg;
  /** 是否在这条消息末尾显示流式光标 —— 由父组件计算
   *  (只对"最后一条助手且全局 isStreaming"为 true) */
  showCaret?: boolean;
}

export default function ChatMessage({ msg, showCaret = false }: Props) {
  if (msg.role === "user") {
    return <UserBubble msg={msg} />;
  }
  if (msg.role === "assistant") {
    return <AssistantBubble msg={msg} showCaret={showCaret} />;
  }
  // system 不渲染(gateway 自动注入,前端看不到);
  // tool 角色消息也不直接渲染 —— 它的内容已通过 ChatToolCall 在
  // 上一条 assistant 消息里展示了
  return null;
}

function UserBubble({ msg }: { msg: Msg }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        marginBottom: "var(--space-4)",
      }}
    >
      <div
        style={{
          background: "var(--catfish-cyan)",
          color: "white",
          padding: "var(--space-3) var(--space-4)",
          borderRadius: "var(--radius-md)",
          maxWidth: "75%",
          fontSize: 14,
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {msg.content}
      </div>
    </div>
  );
}

function AssistantBubble({
  msg,
  showCaret,
}: {
  msg: Msg;
  showCaret: boolean;
}) {
  const isError = msg.status === "error";

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-3)",
        marginBottom: "var(--space-4)",
      }}
    >
      <div
        style={{
          flexShrink: 0,
          width: 28,
          height: 28,
          borderRadius: "50%",
          background: "var(--catfish-cyan-dim)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 14,
          color: "white",
        }}
        aria-hidden
      >
        🐟
      </div>
      <div
        style={{
          flex: 1,
          fontSize: 14,
          lineHeight: 1.6,
          color: "var(--catfish-text)",
          minWidth: 0, // 防 markdown 撑开
        }}
      >
        {msg.content && <Markdown text={msg.content} />}
        {!msg.content && showCaret && (
          <span style={{ color: "var(--catfish-text-muted)" }}>…</span>
        )}
        {/* tool calls 列表 —— 每个一行,折叠式 */}
        {msg.tool_calls && msg.tool_calls.length > 0 && (
          <div style={{ marginTop: msg.content ? "var(--space-2)" : 0 }}>
            {msg.tool_calls.map((tc) => (
              <ChatToolCall key={tc.id} call={tc} />
            ))}
          </div>
        )}
        {showCaret && (
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 14,
              marginLeft: 2,
              background: "var(--catfish-cyan-dim)",
              animation: "catfish-blink 1s infinite",
              verticalAlign: "text-bottom",
            }}
          />
        )}
        {isError && (
          <div
            style={{
              marginTop: "var(--space-2)",
              padding: "var(--space-2) var(--space-3)",
              background: "var(--catfish-bg)",
              border: "1px solid var(--status-err)",
              borderRadius: "var(--radius-sm)",
              fontSize: 12,
              color: "var(--status-err)",
              fontFamily: "var(--font-mono)",
              wordBreak: "break-word",
            }}
          >
            ✗ {friendlyError(msg.error)}
          </div>
        )}
      </div>
    </div>
  );
}

/** 把 LiteLLM / Vertex 那种长 traceback 错误压成员工能看的短文案 */
function friendlyError(err: string | undefined): string {
  if (!err) return "未知错误";
  if (err.includes("UNAVAILABLE") || err.includes("503")) {
    return "模型服务器临时高峰 (503),稍后重试或换个模型";
  }
  if (err.includes("Connection error") || err.includes("ServerDisconnected")) {
    return "上游连接失败 — 检查 VPN / Clash / 内网,或换模型";
  }
  if (err.includes("rate limit") || err.includes("429")) {
    return "调用频率超限 (429),稍后重试";
  }
  if (err.includes("401") || err.includes("Unauthorized")) {
    return "鉴权失败 (401),检查 dev token";
  }
  if (err.includes("timeout") || err.includes("timed out")) {
    return "请求超时";
  }
  // 其他错误截短
  return err.length > 200 ? err.slice(0, 200) + "…" : err;
}
