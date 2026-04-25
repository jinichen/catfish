/**
 * 状态点 —— ✓ 绿 / ⚠ 黄 / ✗ 红 / ○ 灰
 *
 * 用 CSS 变量驱动，避免组件里写死颜色。
 */

type StatusKind = "ok" | "warn" | "err" | "idle";

interface Props {
  status: StatusKind;
  label?: string;
  size?: number;
}

const COLOR_VAR: Record<StatusKind, string> = {
  ok: "var(--status-ok)",
  warn: "var(--status-warn)",
  err: "var(--status-err)",
  idle: "var(--status-idle)",
};

export default function StatusDot({ status, label, size = 8 }: Props) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-2)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          background: COLOR_VAR[status],
          flexShrink: 0,
        }}
      />
      {label && <span>{label}</span>}
    </span>
  );
}
