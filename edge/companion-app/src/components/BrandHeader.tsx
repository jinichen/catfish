/** 顶部品牌栏 —— 🐟 鲶鱼 Companion + 副标题 */

export default function BrandHeader() {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        padding: "var(--space-3) var(--space-6)",
        borderBottom: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg-elevated)",
      }}
    >
      <span style={{ fontSize: 20 }}>🐟</span>
      <strong style={{ fontSize: 15, color: "var(--catfish-cyan-dim)" }}>
        鲶鱼 Companion
      </strong>
      <span
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginLeft: "auto",
        }}
      >
        v0.1.0
      </span>
    </header>
  );
}
