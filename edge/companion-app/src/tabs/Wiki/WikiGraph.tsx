/** P3.3.1 真 graph stub — P3.3.5 真**真**填 sigma + graphology**.
 *
 * 跟 Obsidian graph view 真同 capability:
 *   - entities 蓝色 / concepts 橙色 / queries 绿色 / tags 真**蓝绿**真**真
 *   - 节点 size 跟 inbound link count 真**真**比例**
 *   - 点击 node → setSelectedFile (store)
 *   - zoom + pan + drag
 *   - filter sync (跟 tree 真 filter 联动)
 */

export default function WikiGraph() {
  return (
    <div
      style={{
        padding: "var(--space-3)",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--catfish-text-muted)",
        fontSize: 12,
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 48, marginBottom: "var(--space-3)" }}>🕸️</div>
      <div style={{ fontWeight: 600, marginBottom: "var(--space-2)" }}>
        关系图谱 (P3.3.5 待 ship)
      </div>
      <div>sigma.js + graphology 真**渲染 wikilink 关系网**真</div>
      <div style={{ marginTop: "var(--space-3)", fontSize: 11 }}>
        package.json 已 install 真**真**真 sigma + graphology (5/29 audit 时确认)
      </div>
    </div>
  );
}
