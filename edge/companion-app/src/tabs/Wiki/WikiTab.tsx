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

import { useEffect, useRef, useState } from "react";
import { ShareNetwork } from "@phosphor-icons/react";
import WikiPreview from "./WikiPreview";
import WikiGraph from "./WikiGraph";
import WikiOrganizer, { type WikiWorkspaceMode } from "./WikiOrganizer";
import WikiRelationshipWorkbench from "./WikiRelationshipWorkbench";
import { useWikiStore } from "../../store/wiki";
import WikiHubCard from "../Dashboard/WikiHubCard";

export default function WikiTab() {
  const [mode, setMode] = useState<WikiWorkspaceMode>("organize");
  // 11/12: 待整理的关系有几条 (null = 还没读完)。0 条时: 一进来就开「全部知识」(员工自己点过分页就不再自动换),
  // 关系整理那页右边也不写「从左侧选一项」。
  const [taskCount, setTaskCount] = useState<number | null>(null);
  const userPicked = useRef(false);
  const pickMode = (m: WikiWorkspaceMode) => { userPicked.current = true; setMode(m); };
  useEffect(() => {
    if (taskCount === 0 && !userPicked.current && mode === "organize") setMode("browse");
  }, [taskCount, mode]);
  // 9/24: 关系整理按任务 id 选中 (一个名字指向不明是一条任务, 不属于某个文件)
  const [taskId, setTaskId] = useState<string | null>(null);
  const refreshQuiet = useWikiStore((state) => state.refreshQuiet);
  // 10/23: 盘上的知识库会被插件后台改 (蒸馏新卡、核对、清冲突) —— 窗口回到前台、每分钟静默重读一次
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") void refreshQuiet(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    const timer = window.setInterval(refresh, 60_000);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.clearInterval(timer);
    };
  }, [refreshQuiet]);
  // 阅读优先：进入知识库先把正文铺开，图谱按需打开，不抢占首屏空间。
  const [graphVisible, setGraphVisible] = useState(false);
  // 「同事分享」那页不画关联图谱 (图谱是你本机知识的)
  const showGraph = graphVisible && mode !== "shared";
  // E4 (6/6 taste-skill 改造): 走 className `.wikitab*` (见 globals.css).
  // 老版 3 列 1px hairline border → bg shift (elevated/cream/elevated) + 内
  // box-shadow subtle 边界, 减视觉噪声, 跟 brand 墨青 hue tinted.
  return (
    <div className={`wikitab${showGraph ? "" : " wikitab--graph-hidden"}`}>
      <aside className="wikitab__pane-tree">
        <WikiOrganizer
          mode={mode}
          onModeChange={pickMode}
          onTaskCount={setTaskCount}
          taskId={taskId}
          onSelectTask={setTaskId}
        />
      </aside>
      <main className="wikitab__pane-preview">
        {!graphVisible && mode !== "shared" && (
          <button
            type="button"
            className="wikitab__graph-reopen"
            onClick={() => setGraphVisible(true)}
          >
            <ShareNetwork size={18} aria-hidden="true" />查看关联图谱
          </button>
        )}
        {mode === "shared" ? (
          <div className="wikitab__shared"><WikiHubCard /></div>
        ) : mode === "organize" ? (
          <WikiRelationshipWorkbench taskId={taskId} onSelectTask={setTaskId} hasTasks={taskCount !== 0}
                                     onBrowse={() => pickMode("browse")} />
        ) : (
          <WikiPreview onOpenTask={(id) => { setTaskId(id); pickMode("organize"); }} />
        )}
      </main>
      {showGraph && (
        <aside className="wikitab__pane-graph">
          <WikiGraph onCollapse={() => setGraphVisible(false)} />
        </aside>
      )}
    </div>
  );
}
