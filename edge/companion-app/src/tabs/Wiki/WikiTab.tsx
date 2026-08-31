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

import { useState } from "react";
import { ShareNetwork } from "@phosphor-icons/react";
import WikiPreview from "./WikiPreview";
import WikiGraph from "./WikiGraph";
import WikiOrganizer, { type WikiWorkspaceMode } from "./WikiOrganizer";
import WikiRelationshipWorkbench from "./WikiRelationshipWorkbench";

export default function WikiTab() {
  const [mode, setMode] = useState<WikiWorkspaceMode>("organize");
  const [graphVisible, setGraphVisible] = useState(true);
  // E4 (6/6 taste-skill 改造): 走 className `.wikitab*` (见 globals.css).
  // 老版 3 列 1px hairline border → bg shift (elevated/cream/elevated) + 内
  // box-shadow subtle 边界, 减视觉噪声, 跟 brand 墨青 hue tinted.
  return (
    <div className={`wikitab${graphVisible ? "" : " wikitab--graph-hidden"}`}>
      <aside className="wikitab__pane-tree">
        <WikiOrganizer mode={mode} onModeChange={setMode} />
      </aside>
      <main className="wikitab__pane-preview">
        {!graphVisible && (
          <button
            type="button"
            className="wikitab__graph-reopen"
            onClick={() => setGraphVisible(true)}
          >
            <ShareNetwork size={18} aria-hidden="true" />查看关联图谱
          </button>
        )}
        {mode === "organize" ? <WikiRelationshipWorkbench /> : <WikiPreview />}
      </main>
      {graphVisible && (
        <aside className="wikitab__pane-graph">
          <WikiGraph onCollapse={() => setGraphVisible(false)} />
        </aside>
      )}
    </div>
  );
}
