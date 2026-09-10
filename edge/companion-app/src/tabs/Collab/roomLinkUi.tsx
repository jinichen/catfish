/**
 * 横向协同两张卡 (RoomLinkPendingCard / AskColleagueCard) 共用的小零件。
 *
 * 项目里没有 .btn / .input 这类全局 class, Dashboard 各卡都是 inline style
 * (AgentPrefsCard 的保存按钮 / 输入框是样板)。P49 那张卡最初写了 className="btn"
 * 结果按钮没样式 —— 9/10 抽到这里, 两张卡一起改。
 */
import type { CSSProperties, ReactNode } from "react";

export const cardStyle: CSSProperties = {
  background: "var(--catfish-bg-elevated)",
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md)",
  padding: "var(--space-4)",
  height: "100%",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-3)",
};

export const inputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  fontSize: 13,
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--catfish-bg)",
  color: "var(--catfish-text)",
  boxSizing: "border-box",
  fontFamily: "inherit",
};

export const itemStyle: CSSProperties = {
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "var(--space-3)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
};

export function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        color: "var(--status-error, #dc2626)",
        background: "rgba(220, 38, 38, 0.06)",
        padding: "4px 8px",
        borderRadius: 4,
      }}
    >
      {children}
    </div>
  );
}

export function Btn({
  kind,
  disabled,
  onClick,
  children,
}: {
  kind: "primary" | "ghost";
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const primary = kind === "primary";
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{
        padding: "5px 12px",
        fontSize: 12,
        borderRadius: "var(--radius-sm)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.6 : 1,
        background: primary ? "var(--catfish-cyan)" : "transparent",
        color: primary ? "white" : "var(--catfish-text)",
        border: primary ? "none" : "1px solid var(--catfish-border)",
      }}
    >
      {children}
    </button>
  );
}
