/** Shows the current preparation phase and measured elapsed time, not an ETA. */
import { useEffect, useState } from "react";


interface Counts {
  emails: number;
  events: number;
  todos: number;
}

interface Props {
  phase: "profile_loading" | "data_loading" | "llm_running";
  /** phase 开始时间 (Date.now()), AdvisorView 在 setPhase 时同步 reset. */
  startedAt: number;
  /** Retained for caller compatibility; never used to invent an ETA. */
  model: string;
  /** data_loading 完成后传入 — 显已识别数据量. */
  counts?: Counts;
  /** Explicit cancellation callback. */
  onCancel?: () => void;
  cancelLabel?: string;
}


function formatElapsed(ms: number): string {
  const sec = Math.floor(Math.max(0, ms) / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function LoadingProgress({
  phase,
  startedAt,
  counts,
  onCancel,
  cancelLabel = "停止本次分析",
}: Props) {
  // 1s tick 更新 elapsed
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const elapsedMs = now - startedAt;
  const elapsed = formatElapsed(elapsedMs);


  // phase meta
  const meta = {
    profile_loading: {
      emoji: "📊",
      title: "分析你的工作模式",
      subtitle: "看历史邮件 / 关键人 / 项目, 知道你常处理什么",
    },
    data_loading: {
      emoji: "📂",
      title: "拉今日数据",
      subtitle: "邮件 / 日历 / TODO / 工作上下文",
    },
    llm_running: {
      emoji: "🧠",
      title: "核对来源 · 整理任务",
      subtitle: "先识别任务与优先级；草稿和扫描进入具体任务后按需执行",
    },
  }[phase];

  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "32px 24px",
        background: "var(--catfish-bg)",
        border: "1px dashed var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        textAlign: "center",
      }}
    >
      {/* 头: emoji + 标题 */}
      <div
        style={{
          fontSize: 32,
          marginBottom: 8,
        }}
      >
        {meta.emoji}
      </div>
      <div
        style={{
          fontSize: 16,
          fontWeight: 600,
          color: "var(--catfish-text)",
          marginBottom: 4,
        }}
      >
        {meta.title}
      </div>
      <div
        style={{
          fontSize: 13,
          color: "var(--catfish-text-muted)",
          marginBottom: 16,
          lineHeight: 1.5,
        }}
      >
        {meta.subtitle}
      </div>

      {/* 已识别数据 count (data_loading 完后, llm_running 时显) */}
      {phase === "llm_running" && counts && (
        <div
          style={{
            display: "inline-flex",
            gap: 14,
            padding: "8px 16px",
            background: "var(--catfish-bg-elevated)",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: 16,
          }}
        >
          <span>📧 {counts.emails} 邮件</span>
          <span>📅 {counts.events} 条本周日程</span>
          <span>☑️ {counts.todos} 条当前待办</span>
        </div>
      )}

      {/* Actual elapsed time only. */}
      <div
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginBottom: 12,
          fontFamily: "var(--font-mono, monospace)",
        }}
      >
        已等待 {elapsed}
      </div>

      {/* Explicitly stop this analysis. */}
      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          style={{
            fontSize: 12,
            padding: "6px 14px",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
          }}
        >
          {cancelLabel}
        </button>
      )}
    </div>
  );
}
