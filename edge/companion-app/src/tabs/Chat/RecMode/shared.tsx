/** RecMode shared utilities — T token / ModalShell / OverlayShell / btnStyle / TabButton.
 *
 * 抽自 RecModeButton.tsx (5/20 拆分 1217 行规则).
 * 跨 SetupModal / RecordingOverlay / ErrorBanner / PreviewBanner 共用.
 */

// 5/14 鸿波 "你现在故意怠工" 反馈后: 全硬 hex 替 var, 不依赖 mac
// dark/light theme 渲染. 这套 token 是 macOS Sonoma 14 设计语言.
export const T = {
  cyan: "#06B6D4",          // catfish 主色, 实际可见 cyan 不是水绿
  cyanHover: "#0891B2",
  text: "#1d1d1f",          // macOS body primary
  textSecondary: "#6e6e73", // macOS body secondary
  textTertiary: "#86868b",
  bgWhite: "#FFFFFF",
  bgSurface: "#fbfbfd",     // 模态卡片背景, 比纯白多一点深度
  bgInput: "#ffffff",
  border: "#D2D2D7",        // macOS hairline 标准
  borderFocus: "#06B6D4",
  errorRed: "#ff3b30",      // macOS 系统错误红
  shadow: "0 16px 48px rgba(0,0,0,0.16), 0 4px 12px rgba(0,0,0,0.06)",
  closeBg: "#e5e5e7",
  closeBgHover: "#d2d2d7",
  systemFont: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', sans-serif",
};

export function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "8px 16px",
        background: active ? T.bgWhite : "transparent",
        border: "none",
        borderBottom: active ? `2px solid ${T.cyan}` : "2px solid transparent",
        color: active ? T.text : T.textSecondary,
        fontSize: 13,
        fontWeight: active ? 600 : 500,
        cursor: "pointer",
        fontFamily: T.systemFont,
      }}
    >
      {children}
    </button>
  );
}

export function ModalShell({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: T.bgSurface,
          borderRadius: 12,
          padding: "28px 32px",
          maxWidth: 600,
          width: "92%",
          maxHeight: "85vh",
          overflowY: "auto",
          boxShadow: T.shadow,
        }}
      >
        {children}
      </div>
    </div>
  );
}

export function OverlayShell({ children, tone }: { children: React.ReactNode; tone?: "error" }) {
  return (
    <div
      style={{
        position: "fixed",
        bottom: 24,
        right: 24,
        background: T.bgSurface,
        border: `1px solid ${tone === "error" ? T.errorRed : T.border}`,
        borderRadius: 12,
        padding: "16px 20px",
        maxWidth: 380,
        boxShadow: T.shadow,
        zIndex: 999,
        fontFamily: T.systemFont,
      }}
    >
      {children}
    </div>
  );
}

export function btnStyle(kind: "primary" | "secondary", disabled?: boolean): React.CSSProperties {
  if (kind === "primary") {
    return {
      padding: "8px 16px",
      background: disabled ? T.textTertiary : `linear-gradient(135deg, ${T.cyan}, ${T.cyanHover})`,
      color: T.bgWhite,
      border: "none",
      borderRadius: 8,
      fontSize: 13,
      fontWeight: 600,
      cursor: disabled ? "wait" : "pointer",
      opacity: disabled ? 0.6 : 1,
      fontFamily: T.systemFont,
    };
  }
  return {
    padding: "8px 16px",
    background: "transparent",
    color: T.text,
    border: `1px solid ${T.border}`,
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 500,
    cursor: disabled ? "wait" : "pointer",
    opacity: disabled ? 0.6 : 1,
    fontFamily: T.systemFont,
  };
}
