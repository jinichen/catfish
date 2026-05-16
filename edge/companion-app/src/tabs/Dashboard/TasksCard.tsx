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

import { useEffect, useState } from "react";

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

  const load = async () => {
    try {
      const result = await toolBridgeCallTool("catfish_task_list", {});
      if (result.ok && result.result && typeof result.result === "object") {
        const tasksRaw = (result.result as { tasks?: unknown }).tasks;
        if (Array.isArray(tasksRaw)) {
          setTasks(tasksRaw as Task[]);
          setError(null);
        }
      } else {
        setError(result.error || "拉不到任务列表");
      }
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
  // (grid auto-fit + minmax 280px 父布局会自适应). 真有任务时 pop 出来.
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
