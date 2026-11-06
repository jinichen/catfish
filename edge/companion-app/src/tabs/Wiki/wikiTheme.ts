/**
 * 11/07: 知识体系里新加的弹窗 (类型定义 / 整理属性) 跟着亮 / 暗色走 —— 用 tokens.css 的变量, 不用 ModalShell 的 T (写死的浅色)。
 * 鸿波截图: 暗色模式下这几个还是白底黑字。
 */
export const V = {
  text: "var(--catfish-text)",
  muted: "var(--catfish-text-muted)",
  border: "var(--catfish-border)",
  bg: "var(--catfish-bg)",
  surface: "var(--catfish-bg-elevated)",
  accent: "var(--catfish-cyan)",
  accentSoft: "color-mix(in srgb, var(--catfish-cyan) 16%, transparent)",
  warn: "var(--catfish-orange)",
  error: "#e5484d",
  font: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', sans-serif",
};

/** 输入框 / 下拉 / 多行: 底色和字色都显式给, 不然暗色下 WebKit 默认白底 */
export const field = {
  padding: "6px 8px",
  border: `1px solid ${V.border}`,
  borderRadius: 6,
  fontSize: 13,
  boxSizing: "border-box",
  background: V.bg,
  color: V.text,
} as const;
