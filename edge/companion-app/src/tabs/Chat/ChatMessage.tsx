/** 单条消息渲染 —— user / assistant / 错误状态 */

import { Markdown } from "../../lib/markdown";
import type { ChatMessage as Msg } from "../../types/chat";
import ChatToolCall from "./ChatToolCall";
import { extractFilePaths } from "../../lib/path_detect";
import { FilePillList } from "../../components/FilePill";
import { useAgentStore } from "../../store/agent";

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
  const hasAttachments = msg.attachments && msg.attachments.length > 0;
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
          display: "flex",
          flexDirection: "column",
          gap: hasAttachments && msg.content ? "var(--space-2)" : 0,
        }}
      >
        {/* 图片附件优先于文字, 视觉上更清楚 */}
        {hasAttachments && (
          <div
            style={{
              display: "flex",
              gap: "var(--space-2)",
              flexWrap: "wrap",
            }}
          >
            {msg.attachments!.map((att, i) =>
              att.kind === "image" ? (
                <img
                  key={i}
                  src={`data:${att.mimeType};base64,${att.base64}`}
                  alt={att.name}
                  style={{
                    maxWidth: 220,
                    maxHeight: 220,
                    borderRadius: "var(--radius-sm)",
                    display: "block",
                    objectFit: "contain",
                    background: "rgba(0,0,0,0.1)",
                  }}
                />
              ) : null,
            )}
          </div>
        )}
        {msg.content && <span>{msg.content}</span>}
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
  // BL-E11 后续: 头像 alt 用员工自定义名 (默认 "小鲶")
  const agentName = useAgentStore((s) => s.name);
  const isError = msg.status === "error";
  // 助手把生成的文件路径拼在了 markdown 里 (eg "已生成 /Users/.../report.docx").
  // 提一组 FilePill 出来 — 但只在内容稳定后(非流式)做, 否则路径还没写完就误识别.
  const assistantFilePaths =
    !showCaret && msg.content ? extractFilePaths(msg.content) : [];

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-3)",
        marginBottom: "var(--space-4)",
      }}
    >
      {/* 五一 sprint 5/3 BL-D11: 占位 🐟 emoji 换成正式头像 (avatar-circle.svg).
          头像本身是圆形带暖米底, 不再需要外层 background. width/height 固定 28x28.
          BL-E11 后续: alt 用员工自定义名 (老李/小赵/...) — 屏读员工的命名 */}
      <img
        src="/catfish-avatar.svg"
        alt={agentName}
        width={28}
        height={28}
        style={{
          flexShrink: 0,
          borderRadius: "50%",
          display: "block",
        }}
      />
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
        {/* 助手 message 里直接提到的文件路径 → pill —— skill 之后助手往往
            会写一句 "已生成 /Users/.../报告.docx", 这里让它点击可达. */}
        {assistantFilePaths.length > 0 && (
          <FilePillList paths={assistantFilePaths} />
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
