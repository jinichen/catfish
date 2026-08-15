/** WikiTree 的空状态 onboarding (P36, 6/5)。
 *
 * 8/15 从 WikiTree.tsx 搬出来。纯展示, 零 store 依赖, 只有一个
 * onCreateClick 回调 —— 拆分里风险最低的一块。
 */

/** P36 (6/5 鸿波) — 知识体系 tab 空状态 onboarding.
 *
 *  第一次开 Companion `员工` `wiki/ 空` `不知道怎么生`真. 列 3 路:
 *   1. chat 拖文件 → auto ingest → 24h 后 distill 生 entity/concept (P16)
 *   2. chat 聊天 → bg distill → 自动生 (P0/P1.1)
 *   3. + 新建 entity/concept → 手建 (P3.3.8, button 已在顶部)
 */
export function EmptyOnboarding({ onCreateClick }: { onCreateClick: () => void }) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "var(--space-4)",
        background: "var(--catfish-bg-elevated, rgba(0,0,0,0.03))",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        fontSize: 12,
        lineHeight: 1.6,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 10, fontSize: 13 }}>
        🐟 知识体系还是空的
      </div>
      <div style={{ color: "var(--catfish-text-muted)", marginBottom: 14 }}>
        3 种方式开始累积:
      </div>

      <Step n={1} title="拖文件进 chat" body={
        <>
          PDF / Word / Excel / 文本拖进 💬 对话框, 小鲶自动保存全文到 <code style={ocode}>~/.catfish/wiki/raw/sources/</code>, 24 小时内 distill 生成 entity / concept. 适合资料 / 文档入库.
        </>
      } />

      <Step n={2} title="跟小鲶聊" body={
        <>
          chat 时小鲶后台记日记 (<code style={ocode}>employee_journal.md</code>). 想沉淀对话进 wiki, 在消息右下角点 💾 存 wiki 手动存.
        </>
      } />

      <Step n={3} title="手动新建" body={
        <>
          <button
            onClick={onCreateClick}
            style={{
              background: "var(--catfish-teal, #0d9488)",
              border: "none",
              color: "#fff",
              borderRadius: 4,
              padding: "3px 10px",
              fontSize: 11,
              cursor: "pointer",
              marginRight: 6,
              fontWeight: 500,
            }}
          >
            + 新建
          </button>
          点这或顶部 + 新建 按钮, 直接建 entity / concept, 自己填 markdown body. 适合已知想立刻沉淀的知识点.
        </>
      } />

      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px dashed var(--catfish-border)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
        }}
      >
        💡 wiki 文件存在 <code style={ocode}>~/.catfish/wiki/</code>, 也能用
        <strong> Obsidian </strong> 直接打开当 vault. wikilink <code style={ocode}>[[name]]</code> 互联.
      </div>
    </div>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
      <div
        style={{
          flexShrink: 0,
          width: 22,
          height: 22,
          borderRadius: "50%",
          background: "var(--catfish-teal, #0d9488)",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {n}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
        <div style={{ color: "var(--catfish-text-muted)" }}>{body}</div>
      </div>
    </div>
  );
}

const ocode: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  background: "rgba(0,0,0,0.06)",
  padding: "1px 4px",
  borderRadius: 2,
};
