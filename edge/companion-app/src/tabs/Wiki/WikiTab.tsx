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
import WikiHierarchyView from "./WikiHierarchyView";
import { useWikiStore } from "../../store/wiki";

export default function WikiTab() {
  const [mode, setMode] = useState<WikiWorkspaceMode>("organize");
  // 9/24: 关系整理按任务 id 选中 (一个名字指向不明是一条任务, 不属于某个文件)
  const [taskId, setTaskId] = useState<string | null>(null);
  // 10/23「层级」: 选中的顶层; 点树上的节点 → 切到「全部知识」打开那个条目
  const [hierarchyRoot, setHierarchyRoot] = useState<string | null>(null);
  const selectFile = useWikiStore((state) => state.selectFile);
  const openFromHierarchy = (path: string) => {
    setMode("browse");
    void selectFile(path);
  };
  // 阅读优先：进入知识库先把正文铺开，图谱按需打开，不抢占首屏空间。
  const [graphVisible, setGraphVisible] = useState(false);
  // E4 (6/6 taste-skill 改造): 走 className `.wikitab*` (见 globals.css).
  // 老版 3 列 1px hairline border → bg shift (elevated/cream/elevated) + 内
  // box-shadow subtle 边界, 减视觉噪声, 跟 brand 墨青 hue tinted.
  return (
    <div className={`wikitab${graphVisible ? "" : " wikitab--graph-hidden"}`}>
      <aside className="wikitab__pane-tree">
        <WikiOrganizer
          mode={mode}
          onModeChange={setMode}
          taskId={taskId}
          onSelectTask={setTaskId}
          hierarchyRoot={hierarchyRoot}
          onHierarchyRoot={setHierarchyRoot}
        />
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
        {mode === "organize" ? (
          <WikiRelationshipWorkbench taskId={taskId} onSelectTask={setTaskId} />
        ) : mode === "hierarchy" ? (
          <WikiHierarchyView root={hierarchyRoot} onOpen={openFromHierarchy} />
        ) : (
          <WikiPreview />
        )}
      </main>
      {graphVisible && (
        <aside className="wikitab__pane-graph">
          <WikiGraph onCollapse={() => setGraphVisible(false)} />
        </aside>
      )}
    </div>
  );
}
