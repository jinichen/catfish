/** BL-CATFISH-WIKI-MODE P3.3 (6/4) — Wiki tab 3 列 layout 架子.
 *
 * 左 (320px): tree (entities / concepts / queries 3 group + 搜索 + filter)
 * 中 (flex 1): preview (Markdown render, wikilink clickable)
 * 右 (480px): graph (sigma + graphology, Obsidian graph view 类)
 *
 * P3.3.1 ship 架子, P3.3.2-11 填 3 列.
 *
 * 数据流: WikiStore (zustand) — selectedFile / files (从 wiki_list_files load) /
 *         filter (搜索 / tag / type) / graph (build from wikilink edges)
 */

import WikiTree from "./WikiTree";
import WikiPreview from "./WikiPreview";
import WikiGraph from "./WikiGraph";

export default function WikiTab() {
  // E4 (6/6 taste-skill 改造): 走 className `.wikitab*` (见 globals.css).
  // 老版 3 列 1px hairline border → bg shift (elevated/cream/elevated) + 内
  // box-shadow subtle 边界, 减视觉噪声, 跟 brand 墨青 hue tinted.
  return (
    <div className="wikitab">
      <aside className="wikitab__pane-tree">
        <WikiTree />
      </aside>
      <main className="wikitab__pane-preview">
        <WikiPreview />
      </main>
      <aside className="wikitab__pane-graph">
        <WikiGraph />
      </aside>
    </div>
  );
}
