/** 通用 Card / Row 组件 (跟 Companion Dashboard 卡同视觉).
 *
 * BL-ARCH1 5/10. 复用 IdentityCard 那种简单 box, 不引重组件库.
 */

import type { ReactNode } from "react";

export function Card({
  title,
  children,
  action,
}: {
  // BL-ARCH1 P3 (5/10): 放宽 string → ReactNode, 标题里能塞 logo / icon SVG.
  title: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0 }}>{title}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Row({
  label,
  value,
  sub,
  mono,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: "var(--space-2)",
        fontSize: 13,
        gap: "var(--space-3)",
      }}
    >
      <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>{label}</span>
      <span
        style={{
          textAlign: "right",
          minWidth: 0,
          fontFamily: mono ? "var(--font-mono)" : "inherit",
        }}
      >
        <div>{value}</div>
        {sub && (
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              marginTop: 2,
              wordBreak: "break-all",
            }}
          >
            {sub}
          </div>
        )}
      </span>
    </div>
  );
}
