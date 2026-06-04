/** P3.3.1 真 preview stub — P3.3.4 真**真**填 react-markdown + wikilink clickable**. */

export default function WikiPreview() {
  return (
    <div style={{ color: "var(--catfish-text-muted)", fontSize: 13 }}>
      <h2 style={{ marginTop: 0 }}>📄 选 wiki 文件 preview</h2>
      <p>
        左边 tree 选一个 entity / concept / query, 真**这里**真**render Markdown content**.
      </p>
      <p>
        P3.3.4 ship 后, <code>[[wikilink]]</code> 真**会 clickable, 真**点击跳转到 target file**.
      </p>
      <h3>当前状态</h3>
      <ul>
        <li>✓ P3.3.1 真 3 列架子 ship</li>
        <li>⏳ P3.3.2 Tauri wiki_list_files / wiki_read_file (read API)</li>
        <li>⏳ P3.3.3 tree view 实际渲染</li>
        <li>⏳ P3.3.4 markdown preview (react-markdown + gray-matter)</li>
        <li>⏳ P3.3.5 sigma + graphology graph view</li>
        <li>⏳ P3.3.6-11 真**filter / + 新建 / dataview / broken link / re-ingest**</li>
      </ul>
    </div>
  );
}
