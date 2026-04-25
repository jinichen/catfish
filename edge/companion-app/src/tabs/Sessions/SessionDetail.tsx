/** 单会话详情 —— 元信息 + 最后用户/助手消息 */

import { useSessionDetail } from "../../hooks/useSessions";
import { formatRelativeTime, formatTokens } from "../../lib/format";

interface Props {
  id: string | null;
}

export default function SessionDetail({ id }: Props) {
  const detail = useSessionDetail(id);

  if (!id)
    return (
      <div style={{ color: "var(--catfish-text-muted)" }}>
        从左侧选一个会话查看详情
      </div>
    );

  if (!detail) return <div>加载中…</div>;

  const { meta, lastUserMessage, lastAssistantMessage } = detail;
  const title = meta.title?.trim() || `会话 ${meta.id.slice(-6)}`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-4)",
      }}
    >
      <header
        style={{
          paddingBottom: "var(--space-3)",
          borderBottom: "1px solid var(--catfish-border)",
        }}
      >
        <h2 style={{ marginBottom: "var(--space-1)" }}>{title}</h2>
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {meta.id} · {meta.model}
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginTop: "var(--space-1)",
          }}
        >
          {meta.messageCount} 条消息 · {formatTokens(meta.totalTokens)} tok ·
          启动 {formatRelativeTime(meta.startedAt)}
          {meta.endReason && ` · 结束原因 ${meta.endReason}`}
        </div>
      </header>
      <Block label="最后用户消息" body={lastUserMessage ?? null} />
      <Block label="最后助手消息" body={lastAssistantMessage ?? null} />
    </div>
  );
}

function Block({ label, body }: { label: string; body: string | null }) {
  return (
    <section>
      <h4 style={{ marginBottom: "var(--space-2)" }}>{label}</h4>
      <pre
        style={{
          margin: 0,
          padding: "var(--space-3)",
          background: "var(--catfish-bg-elevated)",
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-sm)",
          whiteSpace: "pre-wrap",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          maxHeight: 240,
          overflow: "auto",
        }}
      >
        {body ?? "(无)"}
      </pre>
    </section>
  );
}
