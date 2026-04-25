/** Hermes 会话列表 —— 标题 / 模型 / 启动时间 / token 总量 */

import { useSessions } from "../../hooks/useSessions";
import { formatRelativeTime, formatTokens } from "../../lib/format";
import type { SessionMeta } from "../../types/session";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** title 没设就 fallback 到 id 的简写 */
function displayLabel(s: SessionMeta): string {
  if (s.title && s.title.trim()) return s.title;
  // 例如 "20260422_190414_3bca84" → 取后 6 位作 id 简写
  return s.id.length > 8 ? `· ${s.id.slice(-6)}` : s.id;
}

export default function SessionList({ selectedId, onSelect }: Props) {
  const { sessions, loading, error } = useSessions();

  if (loading) return <div>加载中…</div>;
  if (error) return <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>;
  if (sessions.length === 0) return <div>(暂无会话)</div>;

  return (
    <ul
      style={{
        listStyle: "none",
        margin: 0,
        padding: 0,
        marginTop: "var(--space-3)",
        flex: 1,
        overflow: "auto",
      }}
    >
      {sessions.map((s) => {
        const active = s.id === selectedId;
        const isLive = !s.endedAt;
        return (
          <li
            key={s.id}
            onClick={() => onSelect(s.id)}
            style={{
              padding: "var(--space-3)",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              background: active
                ? "var(--catfish-cyan)"
                : "var(--catfish-bg-elevated)",
              color: active ? "white" : "var(--catfish-text)",
              marginBottom: "var(--space-1)",
              border: "1px solid var(--catfish-border)",
            }}
          >
            <div
              style={{
                fontWeight: 600,
                fontSize: 13,
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
              }}
            >
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                }}
                title={s.title ?? s.id}
              >
                {displayLabel(s)}
              </span>
              {isLive && (
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: active
                      ? "rgba(255,255,255,0.25)"
                      : "var(--status-ok)",
                    color: "white",
                    fontWeight: 500,
                  }}
                >
                  active
                </span>
              )}
            </div>
            <div
              style={{
                fontSize: 11,
                opacity: 0.8,
                fontFamily: "var(--font-mono)",
                marginTop: 2,
              }}
            >
              {s.model} · {s.messageCount} msg · {formatTokens(s.totalTokens)} tok
            </div>
            <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
              {formatRelativeTime(s.startedAt)}
              {s.endReason && !active && (
                <span style={{ opacity: 0.6, marginLeft: "var(--space-2)" }}>
                  · {s.endReason}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
