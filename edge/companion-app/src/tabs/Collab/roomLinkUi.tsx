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

/** 11/24: 协同页各卡片统一的标题行 —— 标题 (+ 一行说明) 在左, 计数 / 按钮在右, 上下对齐方式一致 */
export function CardHead({ title, sub, subTitle, aside }: { title: ReactNode; sub?: ReactNode; subTitle?: string; aside?: ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--space-3)", flexShrink: 0 }}>
      <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600, lineHeight: "24px" }}>{title}</h3>
        {sub && <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--catfish-text-muted)" }} title={subTitle}>{sub}</div>}
      </div>
      {aside && <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minHeight: 24, flexShrink: 0 }}>{aside}</div>}
    </div>
  );
}

/** 11/24: 筛选 chip (要处理的 / 全部): 选中 = 浅青底 + 青字, 不用实色青; 高 24 */
export function chipStyle(on: boolean): CSSProperties {
  return {
    border: `1px solid ${on ? "var(--catfish-cyan)" : "var(--catfish-border)"}`, cursor: "pointer", font: "inherit", fontSize: 12,
    minHeight: 24, padding: "2px 12px", borderRadius: 12, whiteSpace: "nowrap", flex: "none",
    background: on ? "var(--catfish-cyan-tint)" : "transparent", color: on ? "var(--catfish-cyan)" : "var(--catfish-text)",
  };
}

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
  /** accent = 青色描边 (11/24: 逐条的「同意 / 放行」等, 一屏可能好几个, 不用实色) */
  kind: "primary" | "accent" | "ghost";
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const primary = kind === "primary";
  const accent = kind === "accent";
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{
        padding: "5px 12px",
        fontSize: 12,
        // 11/24: 卡片标题行挤的时候按钮被压成一字一行 (「发起填表」竖成 4 行) —— 按钮不换行、不被压缩
        whiteSpace: "nowrap",
        flex: "none",
        borderRadius: "var(--radius-sm)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.6 : 1,
        background: primary ? "var(--catfish-cyan)" : "transparent",
        color: primary ? "white" : accent ? "var(--catfish-cyan)" : "var(--catfish-text)",
        border: primary ? "none" : accent ? "1px solid var(--catfish-cyan)" : "1px solid var(--catfish-border)",
      }}
    >
      {children}
    </button>
  );
}
