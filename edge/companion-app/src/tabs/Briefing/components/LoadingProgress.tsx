/** P3.5.32.7 (6/18 鸿波 catch '一直处于未知状态焦虑') — advisor 进度展现.
 *
 * # 鸿波诉求 verbatim
 *
 * '我觉得没必要把 profile 和 chat 解耦, 关键是优化这个流程或者给客户展现过程,
 * 这样就不会觉得一直处于未知的状态, 造成客户的焦虑'
 *
 * # audit (不瞎猜)
 *
 * 老 AdvisorView phase 4 态合并成 1 行 placeholder:
 *   - profile_loading / booting → '准备中...'
 *   - data_loading → '正在拉取邮件 / 日历 / TODO / 历史...'
 *   - llm_running → '鲶鱼正在综合判断 (LLM 调用 + 起草草稿)...'
 *
 * 问题: 鸿波 picker = catfish-private-main (P3.5.29 catalog.default), gateway log
 * 实测 profile 单 LLM 116s, advisor agent loop 5-10 min real. 5-10 分钟显一行
 * 不变文案 → 员工焦虑, 不知道在哪一步.
 *
 * # fix — 3 件事
 *
 * 1. **分阶段细化** — profile_loading / data_loading / llm_running 各显独立内容
 * 2. **elapsed timer** — 1s tick, '已等 1:23' 让员工知道时间
 * 3. **预估时间 model-aware** — catfish-private-main 慢 (5-10 min), deepseek-flash 快
 *    (2-3 min). 提前告诉员工预期, 减少焦虑.
 * 4. **数据 count 展示** — data_loading 完后显 '已识别: 48 邮件 / 3 日历 / 12 TODO',
 *    让员工知道数据有多大, advisor 在处理什么.
 * 5. **cancel button** — '点这里用上次结果' (走 stale cache fallback), 不想等就跳.
 */

import { useEffect, useState } from "react";

import { useAgentActivity } from "../../../hooks/useAgentActivity";
import { describeTurn } from "../../../lib/agentActivity";

interface Counts {
  emails: number;
  events: number;
  todos: number;
}

interface Props {
  phase: "profile_loading" | "data_loading" | "llm_running";
  /** phase 开始时间 (Date.now()), AdvisorView 在 setPhase 时同步 reset. */
  startedAt: number;
  /** 当前 picker model, 决定预估时间显啥. */
  model: string;
  /** data_loading 完成后传入 — 显已识别数据量. */
  counts?: Counts;
  /** 有 stale cache 时显 cancel button. 点 → 跳 LLM 用上次结果. */
  onCancel?: () => void;
}

/** Model-aware 预估时间. catfish-private-main 慢 (40K context 70-100s/call), agent
 * loop 多轮累积. deepseek-flash 快 (~5s/call), agent loop 2-3 min. */
function estimateTime(model: string, phase: Props["phase"]): string {
  const isSlow = model.includes("private-main") || model.includes("private-vision");
  if (phase === "profile_loading") {
    return isSlow ? "通常 1-2 分钟" : "通常 5-10 秒";
  }
  if (phase === "data_loading") {
    return "通常 2-5 秒";
  }
  // llm_running
  return isSlow ? "通常 5-10 分钟 (内网模型慢)" : "通常 2-3 分钟";
}

function formatElapsed(ms: number): string {
  const sec = Math.floor(ms / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function LoadingProgress({
  phase,
  startedAt,
  model,
  counts,
  onCancel,
}: Props) {
  // 1s tick 更新 elapsed
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const elapsedMs = now - startedAt;
  const elapsed = formatElapsed(elapsedMs);

  // 8/9 P44: 只在 llm_running 期间探 —— 另两个阶段还没有 agent turn 在跑,
  // 探了必然是 no_running_turn, 白打网络。
  const { turn } = useAgentActivity(phase === "llm_running");
  const activityLine = describeTurn(turn);

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
      title: "综合判断 + 起草草稿",
      subtitle: "识别主菜 · 起草回复口径 · 跑合规扫描",
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
          <span>📅 {counts.events} 日历</span>
          <span>✓ {counts.todos} TODO</span>
        </div>
      )}

      {/* elapsed + 预估 */}
      <div
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginBottom: activityLine ? 4 : 12,
          fontFamily: "var(--font-mono, monospace)",
        }}
      >
        已等 {elapsed} · {estimateTime(model, phase)}
      </div>

      {/* 8/9 P44: hermes 真实进度。
          上面那行是**我们猜的** (按 model 名硬编码的区间) + 一个墙上时钟;
          这行是 hermes 自己报的 —— 第几轮、在用哪个工具、多久没动。
          探不到就整行不显示, 退回原来的盲等展示, 不编"正在思考…"糊弄。 */}
      {activityLine && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: 12,
            fontFamily: "var(--font-mono, monospace)",
            opacity: 0.85,
          }}
        >
          ⚙ {activityLine}
        </div>
      )}

      {/* cancel button — 点了用上次结果 */}
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
          不等了 · 用上次结果
        </button>
      )}
    </div>
  );
}
