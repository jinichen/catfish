/** Dashboard TasksCard — BL-A2.4 (5/7 ship).
 *
 * 显示当前后台任务 (running / completed / failed) 列表. 5 秒自动刷新.
 *
 * 数据来源: tool-bridge `catfish_task_list` 工具.
 * 鸿波 5/7 拍板: "BL-A2.4 也要一次性做, 不留 5/22".
 *
 * 暂不支持暂停 / 取消 (那是 P1 复杂度, 留 5/22 后):
 *   - 目前只展示 + 5s 刷新
 *   - 失败任务点击查看 error
 *   - 完成任务 24h 后自动从 list 消失 (task_manager TTL)
 */

import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { toolBridgeCallTool } from "../../lib/tauri";

interface Task {
  task_id: string;
  kind: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed";
  started_at?: number;
  finished_at?: number | null;
  elapsed_s?: number;
  error?: string | null;
  result_preview?: string | null;  // BL-LONG-RUNNING-V1: 来自 tasks.jsonl
  source?: "live" | "history";     // 标识来自 in-memory 还是 jsonl
}

const REFRESH_MS = 5000;  // 5 秒刷新, 跟 tasks 状态变化相对快

const STATUS_LABEL: Record<Task["status"], string> = {
  pending: "等待",
  running: "运行中",
  completed: "完成",
  failed: "失败",
};

const STATUS_COLOR: Record<Task["status"], string> = {
  pending: "#9ca3af",     // 灰
  running: "#3b82f6",     // 蓝
  completed: "#10b981",   // 绿
  failed: "#ef4444",      // 红
};

export default function TasksCard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // BL-LONG-RUNNING-V1 (5/30): 跟踪上一轮 status, 看到 running → completed/failed
  // 触发 macOS notification. 避免每次 polling 都通知 (只通知状态变化的那一刻).
  // Ref 不放 state 防 re-render race.
  const prevStatusByIdRef = useRef<Map<string, Task["status"]>>(new Map());

  const load = async () => {
    try {
      // 并行拉: in-memory active + jsonl 历史
      const [liveResult, historyRaw] = await Promise.all([
        toolBridgeCallTool("catfish_task_list", {}),
        invoke<Array<{
          task_id: string;
          kind: string;
          label: string;
          status: string;
          started_at: number;
          finished_at: number | null;
          elapsed_s: number | null;
          error: string | null;
          result_preview: string | null;
        }>>("tasks_history_read", {
          input: { hoursBack: 72, limit: 50 },
        }).catch(() => []),
      ]);

      // in-memory active (含 running/pending + 24h 内 completed/failed)
      const live: Task[] = [];
      if (liveResult.ok && liveResult.result && typeof liveResult.result === "object") {
        const tasksRaw = (liveResult.result as { tasks?: unknown }).tasks;
        if (Array.isArray(tasksRaw)) {
          for (const t of tasksRaw as Task[]) {
            live.push({ ...t, source: "live" });
          }
        }
      } else {
        setError(liveResult.error || "拉不到任务列表");
      }

      // jsonl 历史 (24h+ 老 completed/failed)
      const history: Task[] = (historyRaw || []).map((h) => ({
        task_id: h.task_id,
        kind: h.kind,
        label: h.label,
        status: h.status as Task["status"],
        started_at: h.started_at,
        finished_at: h.finished_at,
        elapsed_s: h.elapsed_s ?? undefined,
        error: h.error,
        result_preview: h.result_preview,
        source: "history",
      }));

      // 合并去重: 同 task_id 优先 in-memory (status 更新)
      const seen = new Set(live.map((t) => t.task_id));
      const merged = [...live, ...history.filter((h) => !seen.has(h.task_id))];

      // BL-LONG-RUNNING-V1: 检测状态变化, 发 macOS notification
      const prev = prevStatusByIdRef.current;
      const next = new Map<string, Task["status"]>();
      for (const t of merged) {
        next.set(t.task_id, t.status);
        const old = prev.get(t.task_id);
        if (
          old &&
          (old === "running" || old === "pending") &&
          (t.status === "completed" || t.status === "failed")
        ) {
          // 任务刚完成 — 发系统通知
          void notifyTaskDone(t);
        }
      }
      prevStatusByIdRef.current = next;

      setTasks(merged);
      if (live.length || history.length) setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  // 排序: running > pending > completed > failed (按状态 + 最新启动)
  const sortedTasks = [...tasks].sort((a, b) => {
    const order: Record<Task["status"], number> = {
      running: 0, pending: 1, completed: 2, failed: 3,
    };
    if (order[a.status] !== order[b.status]) {
      return order[a.status] - order[b.status];
    }
    return (b.started_at || 0) - (a.started_at || 0);
  });

  // BL-TASKS-CARD-HIDE-WHEN-EMPTY (5/16): 99% 时间无任务, 空卡占地不值.
  // 无任务 + 不 loading + 不 error → 整张卡不渲染, ProactiveCard 自动占满
  // BL-LONG-RUNNING-V1 (5/30): tasks 现在含历史 (jsonl) — 一旦员工跑过任何任务,
  // 卡片会一直显示, 不再隐藏. 实际隐藏条件是真零任务过 (新 mac / 没用过).
  if (!loading && !error && tasks.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        // BL-FIX15 (5/8): 撑满 grid cell + 去 marginBottom (跟 ProactiveCard 对齐)
        // 之前的 marginBottom 是早期没 grid 时残留, 现在 grid 自带 gap 不需要
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>
          📋 后台任务
        </h3>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--catfish-text-muted)" }}>
          {tasks.length === 0 ? "无任务" : `${tasks.length} 项`}
        </span>
      </div>

      {loading && tasks.length === 0 && (
        <div style={{ color: "var(--catfish-text-muted)", fontSize: "var(--text-sm)" }}>
          加载中...
        </div>
      )}

      {error && (
        <div style={{ color: "var(--catfish-status-error)", fontSize: "var(--text-sm)" }}>
          {error}
        </div>
      )}

      {!loading && !error && tasks.length === 0 && (
        <div style={{ color: "var(--catfish-text-muted)", fontSize: "var(--text-sm)" }}>
          没有后台任务. 鲶鱼接到长任务时这里会显示进度.
        </div>
      )}

      {sortedTasks.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {sortedTasks.map((task) => (
            <TaskRow key={task.task_id} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: Task }) {
  const [showError, setShowError] = useState(false);

  const elapsedStr = task.elapsed_s !== undefined
    ? task.elapsed_s < 60
      ? `${task.elapsed_s.toFixed(0)}s`
      : `${(task.elapsed_s / 60).toFixed(1)}min`
    : "-";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-2)",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        fontSize: "var(--text-sm)",
      }}
    >
      <span
        style={{
          display: "inline-block",
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: STATUS_COLOR[task.status],
          flexShrink: 0,
        }}
        aria-label={STATUS_LABEL[task.status]}
      />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {task.label || task.kind}
      </span>
      <span
        style={{
          fontSize: "var(--text-xs)",
          color: "var(--catfish-text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {STATUS_LABEL[task.status]} · {elapsedStr}
      </span>
      {task.status === "failed" && task.error && (
        <button
          onClick={() => setShowError(!showError)}
          style={{
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            padding: "2px 6px",
            fontSize: "var(--text-xs)",
            color: "var(--catfish-status-error)",
            cursor: "pointer",
          }}
        >
          {showError ? "收" : "看"}
        </button>
      )}
      {showError && task.error && (
        <div
          style={{
            position: "absolute",
            background: "var(--catfish-bg-elevated)",
            border: "1px solid var(--catfish-status-error)",
            borderRadius: "var(--radius-sm)",
            padding: "var(--space-2)",
            fontSize: "var(--text-xs)",
            maxWidth: "400px",
            zIndex: 10,
          }}
        >
          {task.error}
        </div>
      )}
    </div>
  );
}

/** BL-LONG-RUNNING-V1 (5/30): 任务从 running/pending → completed/failed 时发
 *  macOS native notification, 即使 Companion 不在前台 / 锁屏也能弹.
 *
 *  走 commands/system.rs:notify Tauri command (osascript display notification).
 *  失败仅 console.warn, 不阻塞 chat / dashboard 主流程.
 */
async function notifyTaskDone(task: Task) {
  const label = task.label || task.kind || "后台任务";
  if (task.status === "completed") {
    const preview = task.result_preview ? ` · ${task.result_preview.slice(0, 80)}` : "";
    const elapsed = task.elapsed_s
      ? task.elapsed_s < 60
        ? ` (${task.elapsed_s.toFixed(0)}s)`
        : ` (${(task.elapsed_s / 60).toFixed(1)}min)`
      : "";
    try {
      await invoke("notify", {
        title: `✅ ${label} 完成${elapsed}`,
        body: preview || "鲶鱼后台任务执行完毕, 打开 Companion 查看结果.",
      });
    } catch (e) {
      console.warn("[BL-LONG-RUNNING-V1] notify (completed) 失败:", e);
    }
  } else if (task.status === "failed") {
    const errPreview = task.error ? task.error.slice(0, 100) : "(无错误信息)";
    try {
      await invoke("notify", {
        title: `❌ ${label} 失败`,
        body: errPreview,
      });
    } catch (e) {
      console.warn("[BL-LONG-RUNNING-V1] notify (failed) 失败:", e);
    }
  }
}
