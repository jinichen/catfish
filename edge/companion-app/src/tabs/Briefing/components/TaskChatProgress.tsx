import { useEffect, useState } from "react";
import type { TaskChatStatus } from "../../../hooks/useTaskChat";

// 首段响应超过几秒并不代表异常，尤其是早安分析需要较长上下文时。
// 只有等待明显偏长时才补充说明，避免正常等待被渲染成告警。
const FIRST_OUTPUT_HINT_MS = 30_000;

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function phaseLabel(status: TaskChatStatus): string {
  switch (status.phase) {
    case "preparing":
      return "准备会话";
    case "loading_tools":
      return "准备执行环境";
    case "waiting_model":
      return "等待模型响应";
    case "generating":
      return "正在生成回答";
    case "running_tool":
      return "正在执行动作";
    case "error":
      return "回答失败";
    case "cancelled":
      return "已停止";
  }
}

export default function TaskChatProgress({
  status,
  onCancel,
  onRetry,
}: {
  status: TaskChatStatus | null;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!status) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  if (!status) return null;

  const active = status.phase !== "error" && status.phase !== "cancelled";
  const elapsed = formatElapsed(now - status.startedAt);
  const waitingForFirstOutput =
    status.phase === "waiting_model" && now - status.startedAt >= FIRST_OUTPUT_HINT_MS;

  return (
    <div className={`briefing-2col__task-progress${active ? " briefing-2col__task-progress--active" : ""}`} role="status" aria-live="polite">
      <div className="briefing-2col__task-progress-main">
        {active && <span className="briefing-2col__task-progress-spinner" aria-hidden="true" />}
        <span className="briefing-2col__task-progress-title">{phaseLabel(status)}</span>
        <span className="briefing-2col__task-progress-elapsed">已用时 {elapsed}</span>
      </div>
      <div className="briefing-2col__task-progress-detail">
        {status.error ?? status.detail}
      </div>
      {waitingForFirstOutput && (
        <div className="briefing-2col__task-progress-hint">
          首段内容仍在准备中，模型正在处理较长的分析任务；你可以继续等待，或点击“停止生成”。
        </div>
      )}
      <div className="briefing-2col__task-progress-actions">
        {active ? (
          <button type="button" onClick={onCancel} className="briefing-2col__task-stop-btn">
            停止生成
          </button>
        ) : (
          <button type="button" onClick={onRetry} className="briefing-2col__task-retry-btn">
            重新发送
          </button>
        )}
      </div>
    </div>
  );
}
