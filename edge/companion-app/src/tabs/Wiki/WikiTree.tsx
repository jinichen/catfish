/** P3.3.1 真 tree view stub — P3.3.3 真**真**填**真**真**真**真**真**真**3 group + 搜索 + filter**. */

export default function WikiTree() {
  return (
    <div style={{ padding: "var(--space-3)" }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>
        🧠 知识体系
      </h3>
      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: "var(--space-2)" }}>
        P3.3.1 架子 ship 完成. tree view (P3.3.3) / 搜索 (P3.3.6) / + 新建 (P3.3.8) /
        预制 query (P3.3.9) 待 ship.
      </p>
      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: "var(--space-3)" }}>
        当前 wiki 真**真**目录**: <code>~/.catfish/wiki/</code>
      </p>
      <ul style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 20 }}>
        <li>📁 entities/ (人 / 机构 / 系统 / 资质 / 项目)</li>
        <li>📁 concepts/ (流程 / 规则 / 原则 / 标准)</li>
        <li>📁 queries/ (chat 真 Q&A 存盘)</li>
      </ul>
    </div>
  );
}
