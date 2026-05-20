/** 早安播报 4 行总览的单行组件 — 抽自 BriefingCard.tsx (5/20 拆分).
 *
 * 一行 = [icon] [label] [summary] [optional hint] [optional 跳转按钮].
 * 主组件 BriefingCard 用 BriefingRow 渲染 4 行 (邮件 / 日历 / 工作计划 / 优先建议).
 */

export interface BriefingRowProps {
  icon: string;
  label: string;
  summary: string;
  muted?: boolean;
  hint?: string;
  actionable?: boolean;
  onClick?: () => void;
  actionLabel?: string;
}

export default function BriefingRow(props: BriefingRowProps) {
  const { icon, label, summary, muted, hint, actionable, onClick, actionLabel } = props;
  return (
    <li
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "6px 8px",
        background: muted ? "transparent" : "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        border: muted ? "1px dashed var(--catfish-border)" : "1px solid var(--catfish-border)",
        fontSize: 12,
        opacity: muted ? 0.7 : 1,
      }}
    >
      <span style={{ fontSize: 14 }}>{icon}</span>
      <span style={{ minWidth: 60, color: "var(--catfish-text-muted)" }}>{label}</span>
      <span style={{ flex: 1, color: "var(--catfish-text)" }}>
        {summary}
        {hint && (
          <span
            style={{
              marginLeft: 6,
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              opacity: 0.7,
            }}
          >
            ({hint})
          </span>
        )}
      </span>
      {actionable && onClick && (
        <button
          type="button"
          onClick={onClick}
          style={{
            background: "var(--catfish-cyan)",
            color: "#fff",
            border: "none",
            borderRadius: "var(--radius-sm)",
            padding: "3px 10px",
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 500,
            fontFamily: "inherit",
          }}
        >
          {actionLabel ?? "→"}
        </button>
      )}
    </li>
  );
}
