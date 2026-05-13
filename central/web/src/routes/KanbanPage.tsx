/** /kanban — Multi-Agent Kanban 单员工任务看板 (BL-HERMES013-RED-2 5/13).
 *
 * scope 1 (鸿波 5/13 22:35 拍板): 本地 catfish_run_task + a2a 收件聚合.
 * 跨员工跨设备 = scope 2, BL-RBAC sprint 后做.
 *
 * 5 列 (左→右): 待开始 / 跑中 / 等待 / 完成 / 失败.
 *
 * 数据每 5s 自动刷新 + 顶部手动刷新按钮.
 *
 * UI 决策:
 * - 不做 drag-and-drop. 状态由 task_manager / a2a runtime 控, 用户拖也改不了.
 * - 每列上方显数量徽章, 总顶部 hours_back 滑块 (24/48/168 三档预设).
 * - 卡片 click → 弹详情 modal (右侧 drawer 之类的, MVP 先 alert 占位).
 */

import { useEffect, useState } from "react";

import { Card } from "../components/Card";
import {
  fetchMyTasks,
  KANBAN_COLUMNS,
  type TaskCard,
  type TaskStatus,
  type TasksMeResponse,
} from "../lib/tasks";

const HOURS_OPTIONS: { value: number; label: string }[] = [
  { value: 24, label: "24h" },
  { value: 48, label: "48h" },
  { value: 168, label: "7 天" },
];

const REFRESH_INTERVAL_MS = 5000;

export function KanbanPage() {
  const [data, setData] = useState<TasksMeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hoursBack, setHoursBack] = useState<number>(48);
  const [loading, setLoading] = useState<boolean>(true);
  const [lastFetchAt, setLastFetchAt] = useState<number>(0);

  // 拉数据 — mount + hoursBack 变 + 5s 自动
  useEffect(() => {
    let cancelled = false;
    async function load(silent: boolean) {
      if (!silent) setLoading(true);
      try {
        const out = await fetchMyTasks(hoursBack, 300);
        if (cancelled) return;
        setData(out);
        setError(null);
        setLastFetchAt(Date.now());
      } catch (e) {
        if (cancelled) return;
        setError(String(e));
      } finally {
        if (!cancelled && !silent) setLoading(false);
      }
    }
    void load(false);
    const t = setInterval(() => void load(true), REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [hoursBack]);

  // 5 列分组 (按 status). 没数据时 column 也渲染空, 避免布局抖动.
  const cardsByStatus = group(data?.cards ?? []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title="多 agent 看板 — 我的任务">
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-3)",
          color: "var(--text-muted)",
          fontSize: 13,
        }}>
          <span>来源:</span>
          <span style={{ fontFamily: "monospace" }}>
            🤖 后台任务 (catfish_run_task) + 📨 a2a 求助收件
          </span>
          <span style={{ flex: 1 }} />
          <label>时间窗:</label>
          <select
            value={hoursBack}
            onChange={(e) => setHoursBack(Number(e.target.value))}
            style={{
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              padding: "var(--space-1) var(--space-2)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text)",
            }}
          >
            {HOURS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <span title="自动 5 秒刷新">
            {lastFetchAt > 0
              ? `· 刷新: ${formatSince(lastFetchAt)}`
              : "· 加载中…"}
          </span>
        </div>
        {error && (
          <div style={{
            color: "var(--status-err, #f87171)",
            background: "var(--status-err-dim, rgba(248, 113, 113, 0.08))",
            padding: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
            marginTop: "var(--space-3)",
            fontSize: 13,
          }}>
            ⚠ 拉数据失败: {error} (可能 catfish-gateway 没起 / 没登录)
          </div>
        )}
      </Card>

      {/* 5 列 Kanban — flex 横向, 每列固定 min-width 让卡片不挤. 横向溢出滚动. */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-3)",
          overflowX: "auto",
          paddingBottom: "var(--space-2)",
          minHeight: 400,
        }}
      >
        {KANBAN_COLUMNS.map((col) => (
          <KanbanColumn
            key={col.status}
            status={col.status}
            label={col.label}
            emoji={col.emoji}
            cards={cardsByStatus[col.status] ?? []}
            loading={loading && data === null}
          />
        ))}
      </div>

      {/* scope 1 提示, 让看到的人知道这版能力边界 */}
      <Card title="说明 — scope 1 限制 (5/13 BL-HERMES013-RED-2)">
        <div style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.6 }}>
          这是<strong>单员工</strong> Kanban — 只看你本机 ~/.catfish/tasks.jsonl
          (后台任务) + ~/.catfish/a2a_notifications.jsonl (你帮过谁) 两个数据源聚合.
          <br />
          <strong>跨员工跨设备</strong> Kanban (manager 看本部门 / admin 看全公司)
          需要 task_manager 改 SQLite 中心 DB 持久化, 排到 BL-RBAC sprint 之后
          (5/22+, 跟 hermes 0.13 自带 Multi-Agent Kanban API 一起接).
          <br />
          "跑中" 列只在当前 catfish 进程活, jsonl 只在任务结束 (completed / failed) 时写一行,
          重启后跑中状态丢. 这是已知 scope 1 限制.
        </div>
      </Card>
    </div>
  );
}

function group(cards: TaskCard[]): Record<TaskStatus, TaskCard[]> {
  const out: Record<TaskStatus, TaskCard[]> = {
    pending: [],
    running: [],
    waiting: [],
    completed: [],
    failed: [],
  };
  for (const c of cards) {
    if (c.status in out) {
      out[c.status].push(c);
    }
  }
  return out;
}

function KanbanColumn({
  label,
  emoji,
  cards,
  loading,
}: {
  status: TaskStatus;  // 当前用作 key, 未来给 column-specific styling 留口子
  label: string;
  emoji: string;
  cards: TaskCard[];
  loading: boolean;
}) {
  return (
    <div
      style={{
        flex: "0 0 280px",
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        fontSize: 13,
        fontWeight: 600,
        marginBottom: "var(--space-2)",
        paddingBottom: "var(--space-2)",
        borderBottom: "1px solid var(--border)",
      }}>
        <span>
          <span style={{ marginRight: 4 }}>{emoji}</span>
          {label}
        </span>
        <span style={{
          background: "var(--bg)",
          color: "var(--text-muted)",
          padding: "2px 8px",
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 400,
        }}>
          {cards.length}
        </span>
      </div>
      {loading ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", padding: "var(--space-4)" }}>
          加载中…
        </div>
      ) : cards.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", padding: "var(--space-4)", opacity: 0.6 }}>
          (空)
        </div>
      ) : (
        cards.map((c) => <KanbanCard key={c.id} card={c} />)
      )}
    </div>
  );
}

function KanbanCard({ card }: { card: TaskCard }) {
  const sourceColor = card.source === "background" ? "var(--catfish-cyan, #06b6d4)" : "var(--status-warn, #f59e0b)";
  const sourceLabel = card.source === "background" ? "🤖" : "📨";
  return (
    <div
      style={{
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderLeft: `3px solid ${sourceColor}`,
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-2)",
        fontSize: 12,
        cursor: "pointer",
      }}
      onClick={() => {
        // MVP: 先 alert 占位, 后续做右侧 drawer 详情
        alert(
          `${card.source} · ${card.kind}\n\n${card.title}\n\n` +
          (card.preview ? `预览: ${card.preview}\n\n` : "") +
          (card.error ? `错误: ${card.error}\n\n` : "") +
          (card.from_sub ? `来自: ${card.from_sub}\n\n` : "") +
          `开始: ${card.started_at || "?"}\n` +
          (card.finished_at ? `完成: ${card.finished_at}\n` : "") +
          (card.elapsed_s != null ? `耗时: ${card.elapsed_s.toFixed(1)}s` : "")
        );
      }}
      title={card.preview || card.error || ""}
    >
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        marginBottom: 4,
      }}>
        <span title={card.source}>{sourceLabel}</span>
        <span style={{
          color: "var(--text-muted)",
          fontSize: 10,
          fontFamily: "monospace",
        }}>
          {card.kind}
        </span>
      </div>
      <div style={{
        fontWeight: 500,
        marginBottom: 4,
        overflow: "hidden",
        display: "-webkit-box",
        WebkitLineClamp: 2,
        WebkitBoxOrient: "vertical",
      }}>
        {card.title}
      </div>
      {card.from_sub && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
          ← {card.from_sub}
        </div>
      )}
      {card.preview && card.status !== "waiting" && (
        <div style={{
          fontSize: 11,
          color: "var(--text-muted)",
          opacity: 0.8,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {card.preview}
        </div>
      )}
      {card.error && (
        <div style={{
          fontSize: 11,
          color: "var(--status-err, #f87171)",
          marginTop: 4,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          ⚠ {card.error}
        </div>
      )}
      <div style={{
        marginTop: 4,
        fontSize: 10,
        color: "var(--text-muted)",
        opacity: 0.6,
      }}>
        {card.started_at ? formatTimeShort(card.started_at) : ""}
        {card.elapsed_s != null && ` · ${formatElapsed(card.elapsed_s)}`}
      </div>
    </div>
  );
}

function formatSince(ts: number): string {
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 5) return "刚刚";
  if (sec < 60) return `${sec}s 前`;
  return `${Math.floor(sec / 60)}m 前`;
}

function formatTimeShort(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`;
  if (sec < 3600) return `${(sec / 60).toFixed(0)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}
