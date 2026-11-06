/** 浅色模态框外壳 + 配色 token —— 知识库「建议关联」弹窗 (WikiLinkSuggestModal) 在用。
 *
 * 10/20: 原来在 tabs/Chat/RecMode/shared.tsx, 是录屏学习那几个弹窗的公共件。
 * 录屏学习 (RecMode) 和「让 AI 学」拿掉后只剩这两样有人用, 挪到这里;
 * OverlayShell / btnStyle / TabButton 只有录屏学习用, 一起删了。
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

/** maxWidth: 默认 600。11/07: 宽内容 (类型定义页 / 整理属性表) 原来塞进 600 的框里, 只能左右拖着看 —— 要宽的自己传。 */
/** themed: 跟着亮 / 暗色 (tokens.css 变量); 不传还是原来写死的浅色, 老弹窗里的字色都按浅底写的, 不能一起改 */
export function ModalShell({ children, onClose, maxWidth = 600, themed = false }: { children: React.ReactNode; onClose: () => void; maxWidth?: number; themed?: boolean }) {
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
          background: themed ? "var(--catfish-bg-elevated)" : T.bgSurface,
          color: themed ? "var(--catfish-text)" : undefined,
          border: themed ? "1px solid var(--catfish-border)" : undefined,
          borderRadius: 12,
          padding: "28px 32px",
          maxWidth,
          width: "92%",
          boxSizing: "border-box",
          overflowX: "hidden",
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
