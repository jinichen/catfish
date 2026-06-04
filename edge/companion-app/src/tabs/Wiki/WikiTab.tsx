/** BL-CATFISH-WIKI-MODE P3.3 (6/4) — Wiki tab 真**3 列 layout 架子**.
 *
 * 左 (320px): tree (entities / concepts / queries 3 group + 搜索 + filter)
 * 中 (flex 1): preview (Markdown render, wikilink clickable)
 * 右 (480px): graph (sigma + graphology, 真**Obsidian graph view 类**)
 *
 * P3.3.1 ship 架子, P3.3.2-11 真**填**真**真**真**3 列真**真**真**.
 *
 * 数据流: WikiStore (zustand) — selectedFile / files (从 wiki_list_files 真**load) /
 *         filter (搜索 / tag / type) / graph (build from wikilink edges)
 */

import WikiTree from "./WikiTree";
import WikiPreview from "./WikiPreview";
import WikiGraph from "./WikiGraph";

export default function WikiTab() {
  return (
    <div
      style={{
        display: "flex",
        height: "100%",
        background: "var(--catfish-bg)",
        overflow: "hidden",
      }}
    >
      {/* 左: tree + 搜索 + filter */}
      <div
        style={{
          width: 320,
          minWidth: 280,
          borderRight: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
          overflowY: "auto",
        }}
      >
        <WikiTree />
      </div>

      {/* 中: preview (Markdown) */}
      <div
        style={{
          flex: 1,
          minWidth: 400,
          overflowY: "auto",
          padding: "var(--space-4)",
        }}
      >
        <WikiPreview />
      </div>

      {/* 右: graph (sigma) */}
      <div
        style={{
          width: 480,
          minWidth: 360,
          borderLeft: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
          position: "relative",
        }}
      >
        <WikiGraph />
      </div>
    </div>
  );
}
