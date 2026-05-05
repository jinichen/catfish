/** 对话 tab 左侧 sidebar —— 会话列表 + 新建按钮。
 *
 * 数据源:
 *   - `sessions_list` Tauri command 拉 ~/.hermes/state.db 的 sessions 表
 *   - 区分 cli / companion source 加 badge
 *   - 已激活的会话项 highlight (背景 + 左竖线)
 *
 * 交互:
 *   - 点列表项 → onSelect(id) → ChatTab 调 sessions_get + chat store loadSession
 *   - 点 "+ 新对话" → onNew() → ChatTab 调 reset()
 *
 * 性能:
 *   - 列表只显示 100 条 (Rust 端 LIMIT 100)
 *   - 不实时订阅 db 变更, 父组件提供 refresh 时机 (新发消息 / 新建 session)
 *
 * 样式独立:
 *   - 不引外部 CSS module, 避免又加配置项
 *   - 暗色 / 亮色靠 CSS vars 自动适配
 */

import { useEffect, useState, useCallback } from "react";
import { listSessions, countSessions, openTerminal } from "../../lib/tauri";
import type { SessionMeta } from "../../types/session";

interface Props {
  /** 当前激活的会话 id (来自 ChatStore.persistedSessionId), null 表示新对话 */
  activeId: string | null;
  /** 点列表项 */
  onSelect: (id: string) => void;
  /** 点 "+ 新对话" */
  onNew: () => void;
  /** 父组件可以在新发消息后主动 bump 这个值, 强制重拉列表 */
  refreshKey?: number;
  /** 流式中禁用切换 / 新建, 防异步条件竞争 */
  busy?: boolean;
}

export default function ChatSidebar({
  activeId,
  onSelect,
  onNew,
  refreshKey = 0,
  busy = false,
}: Props) {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  // 5/5 鸿波: sessions 列表受 MAX_SESSIONS=100 限制, totalCount 是 state.db 真实总数
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      // 并发拉, count 失败也不阻塞 list
      const [list, count] = await Promise.all([
        listSessions(),
        countSessions().catch(() => null),
      ]);
      setSessions(list);
      setTotalCount(count);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    refresh().finally(() => {
      if (cancelled) return;
    });
    return () => {
      cancelled = true;
    };
  }, [refresh, refreshKey]);

  return (
    <aside
      style={{
        width: 240,
        minWidth: 200,
        maxWidth: 320,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg)",
      }}
    >
      <header
        style={{
          padding: "var(--space-3) var(--space-3)",
          borderBottom: "1px solid var(--catfish-border)",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--catfish-text-muted)",
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          title={
            totalCount !== null && totalCount > sessions.length
              ? `state.db 共 ${totalCount} 条, 此处展示 ${sessions.length} 条 (安全上限 10000)`
              : `共 ${sessions.length} 条会话`
          }
        >
          会话 · {sessions.length}
          {totalCount !== null && totalCount > sessions.length && (
            <span style={{ color: "var(--catfish-text-muted)", fontWeight: 400 }}>
              {" "}/ 共 {totalCount}
            </span>
          )}
        </span>
        <button
          onClick={() => refresh()}
          title="刷新"
          style={{
            background: "transparent",
            border: 0,
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
            fontSize: 12,
            padding: 2,
          }}
        >
          ↻
        </button>
      </header>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "var(--space-2) 0",
        }}
      >
        {loading && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "var(--catfish-text-muted)",
              fontSize: 12,
            }}
          >
            加载中…
          </div>
        )}
        {error && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "#dc2626",
              fontSize: 12,
            }}
          >
            读 state.db 失败: {error}
          </div>
        )}
        {!loading && !error && sessions.length === 0 && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "var(--catfish-text-muted)",
              fontSize: 12,
              lineHeight: 1.5,
            }}
          >
            还没会话 —— 起个新对话试试。
          </div>
        )}
        {!loading &&
          !error &&
          sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              disabled={busy && s.id !== activeId}
              onClick={() => !busy && onSelect(s.id)}
            />
          ))}
      </div>

      <footer
        style={{
          padding: "var(--space-2)",
          borderTop: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        <button
          onClick={() => !busy && onNew()}
          disabled={busy}
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            cursor: busy ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 500,
            opacity: busy ? 0.5 : 1,
          }}
        >
          + 新对话
        </button>
        <button
          onClick={() => void openTerminal().catch(() => {})}
          title="在终端打开鲶鱼 CLI (跟 Companion 共享同一对话历史)"
          style={{
            width: "100%",
            padding: "6px 12px",
            border: "1px dashed var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: "transparent",
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
            fontSize: 11,
          }}
        >
          ⌘ 在终端开鲶鱼
        </button>
      </footer>
    </aside>
  );
}

function SessionRow({
  session,
  active,
  disabled,
  onClick,
}: {
  session: SessionMeta;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const title = session.title?.trim() || `(${session.id.slice(0, 17)})`;
  const subtitle = formatRelativeTime(session.startedAt);

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      onClick={disabled ? undefined : onClick}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        position: "relative",
        padding: "8px 12px 8px 14px",
        cursor: disabled ? "not-allowed" : "pointer",
        background: active
          ? "var(--catfish-bg-elevated)"
          : "transparent",
        borderLeft: active
          ? "3px solid var(--catfish-accent, #2563eb)"
          : "3px solid transparent",
        opacity: disabled ? 0.5 : 1,
        userSelect: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 13,
          fontWeight: active ? 600 : 500,
          color: "var(--catfish-text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <SourceBadge source={session.source} />
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
          {title}
        </span>
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginTop: 2,
          display: "flex",
          gap: 8,
        }}
      >
        <span>{subtitle}</span>
        <span>·</span>
        <span>{session.messageCount} 条</span>
      </div>
    </div>
  );
}

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  const isCompanion = source === "companion";
  return (
    <span
      title={isCompanion ? "Companion 起的对话" : "终端起的对话"}
      style={{
        display: "inline-block",
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: isCompanion
          ? "var(--catfish-accent, #2563eb)"
          : "var(--catfish-text-muted)",
        flexShrink: 0,
      }}
    />
  );
}

/** 简单的相对时间 —— 几分钟 / 几小时 / 几天前 */
function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (!t) return "";
  const diff = Date.now() - t;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  // > 1 周直接显示日期
  return new Date(t).toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}
