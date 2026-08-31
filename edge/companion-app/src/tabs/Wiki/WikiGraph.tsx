/** BL-CATFISH-WIKI-MODE P3.3.5 (6/4) — sigma + graphology graph view.
 *
 * Obsidian graph view 风:
 *   - entities 墨青 / concepts 暖橙 / queries 灰青
 *   - 节点 size ~ inbound link count (degree centrality)
 *   - 点击 node → store.selectFile (跳 preview)
 *   - hover 显示 title
 *   - drag + zoom + pan (sigma default)
 *
 * graph build from frontmatter.related (wikilink edges, name → file title match).
 *
 * E5 (6/6 taste-skill 改造): COLOR 常量从 hardcoded 三原色 (#4a9eff/#ff9933/#5fc878)
 * 换 brand 一致 — entity = catfish-cyan 墨青, concept = catfish-orange 暖橙,
 * query = muted teal (跟 cyan 同系, low-saturation). selected 高亮也换 brand
 * cyan-bright (替 #ff3366 粉红). header 走 .wiki-graph__header className.
 */

import { useEffect, useRef, useMemo, useState } from "react";
import {
  ArrowsOut,
  CaretRight,
  CornersIn,
  CornersOut,
  Plus,
  ShareNetwork,
} from "@phosphor-icons/react";
import Graph from "graphology";
import Sigma from "sigma";
import { random } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";
import {
  buildWikiGraphModel,
  buildWikiGraphOverview,
  collectEgoPaths,
  DEFAULT_GRAPH_NEIGHBOR_LIMIT,
  GRAPH_NEIGHBOR_STEP,
  limitWikiGraphPaths,
} from "./wikiGraphModel";

// E5: brand 一致 3 色 (tokens.css 没暴露 hex 给 JS, 这里 mirror).
// 跟 .wiki-kind-badge--<kind> 视觉一致.
const COLOR = {
  entity: "#0E5F66",   // 墨青 (catfish-cyan)
  concept: "#4E8185",  // 柔和灰青，暖橙只留给待确认/异常
  query: "#6B8589",    // 灰青 (low-saturation, muted)
};
const COLOR_SELECTED = "#1A8A95"; // cyan-bright (替 #ff3366 粉红)

export default function WikiGraph({ onCollapse }: { onCollapse?: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const files = useWikiStore((s) => s.files);
  const graphFiles = useMemo(
    () => files.filter((file) => (file.ontology_status ?? "active") === "active"),
    [files],
  );
  const selectedPath = useWikiStore((s) => s.selectedPath);
  const selectFile = useWikiStore((s) => s.selectFile);
  const graphModel = useMemo(() => buildWikiGraphModel(graphFiles), [graphFiles]);
  const [neighborLimit, setNeighborLimit] = useState(DEFAULT_GRAPH_NEIGHBOR_LIMIT);
  const [expanded, setExpanded] = useState(false);
  // P3.5.111/112 (6/25 鸿波): 虚拟体系name.
  // 鸿波点 dangling 体系 → setVirtualSystem(name) → WikiGraph 虚拟显子树.
  // P3.5.112 优先级反: virtualSystemName > selectedPath — 点子项保留虚拟态.
  const virtualSystemName = useWikiStore((s) => s.virtualSystemName);

  // P3.5.108: 顶级体系判定 — kind=concept + (subtype="system" 优先, 或 title 含"体系"二字 fallback).
  //
  // P3.5.109 (6/25 鸿波 catch "+新建少了体系"): UI 加 "🌟 体系 (system)" 选项 →
  // concept_type 写 "system" (WikiCreateModal 真路径), 优先看 subtype === "system".
  // 老数据 fallback: 鸿波之前手动加的"企业资质知识体系" subtype 不是 system —
  // 仍走 title 含"体系" heuristic 兼容.
  //
  // 0 schema 改, 0 数据 backfill, 鸿波加新体系真无侵入.
  const isSystemConcept = (f: WikiFileInfo | undefined): boolean => {
    if (!f || f.kind !== "concept") return false;
    if (f.subtype === "system") return true; // P3.5.109 显式优先
    return f.title.includes("体系"); // 老数据 fallback
  };

  // P3.5.108: effectiveMode 真算 — auto 模式自动跟选中类型决定子图样式
  const selectedFile = useMemo(
    () => graphFiles.find((f) => f.rel_path === selectedPath),
    [graphFiles, selectedPath],
  );

  // P3.5.111/112: effectiveRoot 抽象真实 file root vs 虚拟体系 root.
  // rel_path === null 真虚拟** (dangling 体系名, 无真实文件) — subtree 算法
  // 只 BFS children, 不 add root 自己.
  //
  // P3.5.112 (6/25 鸿波 catch "点一次就不能点了") 优先级**反转**:
  //   virtualSystemName 优先 > selectedPath
  // 鸿波点虚拟体系 → 显该体系子树, 然后点子项**保留体系视图 (selectedPath 真
  // 只用于高亮 + preview, 不切子图). 清虚拟态走 setVirtualSystem(null) / 切别的
  // 体系 header / 强制全图.
  const effectiveRoot: { title: string; rel_path: string | null } | null = useMemo(() => {
    if (virtualSystemName) {
      return { title: virtualSystemName, rel_path: null };
    }
    if (selectedPath) {
      const f = graphFiles.find((x) => x.rel_path === selectedPath);
      return f ? { title: f.title, rel_path: f.rel_path } : null;
    }
    return null;
  }, [virtualSystemName, selectedPath, graphFiles]);

  // effectiveMode 真实生效真 mode: "full" / "ego" / "subtree" (B 方案体系子树)
  const effectiveMode: "full" | "ego" | "subtree" = useMemo(() => {
    if (expanded) return "full";
    if (!effectiveRoot) return "ego";
    if (effectiveRoot.rel_path === null) return "subtree";
    if (isSystemConcept(selectedFile)) return "subtree";
    return "ego";
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, effectiveRoot, selectedFile]);

  // P3.5.108/111: deps key 真精准 — full mode 真不依赖 selected (避免 sigma rebuild).
  // P3.5.111: 虚拟体系真 egoKey 含 virtualSystemName 虚拟切换也 rebuild.
  const egoKey =
    effectiveMode === "full" ? "" : selectedPath || virtualSystemName || "";

  useEffect(() => {
    setNeighborLimit(DEFAULT_GRAPH_NEIGHBOR_LIMIT);
  }, [egoKey]);

  useEffect(() => {
    if (!expanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [expanded]);

  // build graphology graph from the memoized relationship index
  const graphState = useMemo(() => {
    const g = new Graph({ multi: false, type: "directed" });

    let nodeFilter: Set<string> | null = null;
    if (effectiveMode === "ego" && selectedPath) {
      nodeFilter = collectEgoPaths(graphModel, selectedPath);
    } else if (effectiveMode === "ego") {
      // 强制子图但尚未选择节点时不能退回全图，否则界面显示“子图”却把
      // 所有节点一起画出来，用户会误以为本体突然出现了大量孤立项。
      nodeFilter = new Set();
    } else if (effectiveMode === "subtree" && effectiveRoot) {
      // P3.5.108 B 方案 + P3.5.111 虚拟体系支持: 体系子树严格 2 层 BFS 下挖
      // (体系 → 子 concept → entity). 不 follow 其他 edge — 避免跨体系泄漏.
      //
      // P3.5.111: 虚拟体系 (effectiveRoot.rel_path === null) — root 没真实文件
      // 不 add 进 set, 只 add children. 鸿波看到的"虚拟体系子树"就是这逻辑.
      const subtree = new Set<string>();
      if (effectiveRoot.rel_path) subtree.add(effectiveRoot.rel_path);
      const rootTitle = effectiveRoot.title.trim().toLowerCase();
      // 第 1 层: 找所有 concept 真 related[0] 指向 root (上位体系是 root)
      const layer1ConceptTitles = new Set<string>();
      for (const f of graphFiles) {
        if (f.kind !== "concept") continue;
        if (effectiveRoot.rel_path && f.rel_path === effectiveRoot.rel_path) continue;
        // P3.5.132 #5: related[0] 真 RelatedRef, 取 .name
        const parent = (f.related[0]?.name || "").trim().toLowerCase();
        if (parent === rootTitle) {
          subtree.add(f.rel_path);
          layer1ConceptTitles.add(f.title.trim().toLowerCase());
        }
      }
      // 第 2 层: 找所有 entity 真 related[0] 指向第 1 层任意 concept
      for (const f of graphFiles) {
        if (f.kind !== "entity") continue;
        const parent = (f.related[0]?.name || "").trim().toLowerCase();
        if (layer1ConceptTitles.has(parent)) {
          subtree.add(f.rel_path);
        }
      }
      // 兜底: 有 entity 直接指向 root 体系 (跨级关联), 也算
      for (const f of graphFiles) {
        if (f.kind !== "entity") continue;
        if (subtree.has(f.rel_path)) continue;
        const parent = (f.related[0]?.name || "").trim().toLowerCase();
        if (parent === rootTitle) {
          subtree.add(f.rel_path);
        }
      }
      nodeFilter = subtree;
    }
    const candidatePaths = nodeFilter ?? new Set(graphModel.fileByPath.keys());
    const availableNodeCount = candidatePaths.size;
    const visiblePaths = effectiveMode === "full"
      ? new Set(candidatePaths)
      : limitWikiGraphPaths(
          graphModel,
          candidatePaths,
          effectiveRoot?.rel_path ?? selectedPath,
          neighborLimit,
        );

    for (const path of visiblePaths) {
      const f = graphModel.fileByPath.get(path);
      if (!f) continue;
      const color = COLOR[f.kind] || "#888";
      g.addNode(f.rel_path, {
        label: f.title,
        kind: f.kind,
        slug: f.slug,
        color,
        size: 4,
      });
    }

    for (const relation of graphModel.relations) {
      if (!visiblePaths.has(relation.sourcePath) || !visiblePaths.has(relation.targetPath)) continue;
      const edgeKey = `${relation.sourcePath}→${relation.targetPath}`;
      if (g.hasEdge(edgeKey)) continue;
      g.addEdgeWithKey(edgeKey, relation.sourcePath, relation.targetPath, {
        size: 1,
        color: "rgba(120, 120, 120, 0.6)",
        rel: relation.relation,
      });
    }

    // 关系图只展示真正参与关系的节点。孤立条目仍保留在左侧知识库树中，
    // 但不应在关系图里伪装成一条“关系”。
    const isolatedNodes: string[] = [];
    g.forEachNode((node) => {
      if (g.degree(node) === 0) isolatedNodes.push(node);
    });
    for (const node of isolatedNodes) g.dropNode(node);

    // 3. size by degree — hub 略大但保紧凑 (4-8 范围, Obsidian 风格)
    g.forEachNode((node) => {
      const degree = g.degree(node);
      g.setNodeAttribute(node, "size", 4 + Math.min(degree * 0.4, 4));
    });

    // 4. positions — 真 random scale 小 + ForceAtlas2 适度 iter — 让节点紧凑成 cluster,
    //    不飞散到边. Obsidian graph view 同风格.
    random.assign(g, { scale: 100, center: 0 });
    if (g.order > 1) {
      forceAtlas2.assign(g, {
        iterations: 300,
        settings: {
          gravity: 1,
          scalingRatio: 5,
          slowDown: 2,
          barnesHutOptimize: true,
          strongGravityMode: true,
          linLogMode: false,
          outboundAttractionDistribution: false,
          edgeWeightInfluence: 1,
          adjustSizes: true,
        },
      });
    }

    return {
      graph: g,
      availableNodeCount: effectiveMode === "full" ? g.order : availableNodeCount,
      visiblePaths: new Set(g.nodes()),
    };
  }, [effectiveMode, effectiveRoot, graphFiles, graphModel, neighborLimit, selectedPath]);

  const { graph, availableNodeCount, visiblePaths } = graphState;

  // sigma 初始化 + update真
  useEffect(() => {
    if (!containerRef.current) return;

    // teardown 老 sigma
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    if (graph.order === 0) return;

    // P41 (6/5 鸿波): 暗色 mode `label color `runtime detect, 不再硬编码
    // #444 (暗背景下不可读).
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const labelColor = isDark ? "#e0e0e0" : "#444";
    const edgeColor = isDark ? "rgba(180, 180, 180, 0.4)" : "rgba(120, 120, 120, 0.6)";

    const sigma = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      // 默认一跳图只给中心和较重要邻居常驻标签；其余节点 hover 仍显示。
      // 全量图不再出现 200 个标签叠成一团。
      labelRenderedSizeThreshold: 4.6,
      labelFont: "ui-sans-serif, -apple-system, sans-serif",
      labelSize: 12,
      labelWeight: "500",
      labelColor: { color: labelColor },
      defaultEdgeColor: edgeColor,
      defaultEdgeType: "line",
      minCameraRatio: 0.05,
      maxCameraRatio: 10,
    });

    // hover: highlight 真节点 + 邻居, 其余 fade (跟 Obsidian graph 一致)
    let hoveredNode: string | null = null;
    const refreshFade = () => {
      const g = sigma.getGraph();
      const neighbors = new Set<string>();
      if (hoveredNode) {
        neighbors.add(hoveredNode);
        g.forEachNeighbor(hoveredNode, (n) => neighbors.add(n));
      }
      sigma.setSetting("nodeReducer", (node, data) => {
        if (!hoveredNode) return data;
        if (neighbors.has(node)) return data;
        return { ...data, color: "#e0e0e0", label: "" };
      });
      sigma.setSetting("edgeReducer", (edge, data) => {
        if (!hoveredNode) return data;
        const [src, dst] = g.extremities(edge);
        if (src === hoveredNode || dst === hoveredNode) {
          return { ...data, color: "rgba(80, 80, 80, 0.8)" };
        }
        return { ...data, color: "rgba(220, 220, 220, 0.2)" };
      });
    };

    sigma.on("enterNode", ({ node }) => {
      hoveredNode = node;
      refreshFade();
    });
    sigma.on("leaveNode", () => {
      hoveredNode = null;
      refreshFade();
    });

    sigma.on("clickNode", ({ node }) => {
      void selectFile(node);
    });

    sigmaRef.current = sigma;

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [graph, selectFile]);

  // highlight selected node — sigma.refresh 后真 update color**真
  useEffect(() => {
    if (!sigmaRef.current || !selectedPath) return;
    const sigma = sigmaRef.current;
    const g = sigma.getGraph();
    g.forEachNode((node) => {
      const original = COLOR[g.getNodeAttribute(node, "kind") as "entity" | "concept" | "query"] || "#888";
      g.setNodeAttribute(node, "color", node === selectedPath ? COLOR_SELECTED : original);
    });
    sigma.refresh();
  }, [selectedPath]);

  const hasGraph = graph.order > 0;
  const visibleNodeCount = graph.order;
  const overviewItems = useMemo(
    () => buildWikiGraphOverview(graphModel, selectedPath, visiblePaths),
    [graphModel, selectedPath, visiblePaths],
  );
  const canShowMore = effectiveMode !== "full" && visibleNodeCount < availableNodeCount;
  return (
    <div className={`wiki-graph${expanded ? " wiki-graph--expanded" : ""}`}>
      <div className="wiki-graph__header">
        <div className="wiki-graph__heading">
          <ShareNetwork size={21} aria-hidden="true" />
          <strong>关联图谱</strong>
          {hasGraph && (
            <span>
              {visibleNodeCount < availableNodeCount
                ? `${visibleNodeCount} / ${availableNodeCount}`
                : `${visibleNodeCount} 个节点`}
            </span>
          )}
        </div>
        <div className="wiki-graph__controls">
          {hasGraph && (
            <>
            <button
              type="button"
              onClick={() => setExpanded((current) => !current)}
              aria-label={expanded ? "退出全屏图谱" : "打开完整图谱"}
              title={expanded ? "退出全屏图谱" : "打开完整图谱"}
            >
              {expanded
                ? <CornersIn size={18} aria-hidden="true" />
                : <CornersOut size={18} aria-hidden="true" />}
            </button>
            </>
          )}
          {onCollapse && !expanded && (
            <button type="button" onClick={onCollapse} aria-label="收起图谱" title="收起图谱">
              <CaretRight size={18} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      <div className="wiki-graph__stage">
        {/* 这个节点必须永久稳定：Sigma 会直接管理它的 canvas 子节点。
            不能让 React 在空图/有图之间把它复用成带 React children 的空状态。 */}
        <div className="wiki-graph__canvas" ref={containerRef} />
        {hasGraph && (
          <button
            type="button"
            className="wiki-graph__fit"
            onClick={() => sigmaRef.current?.getCamera().animatedReset({ duration: 250 })}
            aria-label="适应画布"
            title="适应画布"
          >
            <ArrowsOut size={18} aria-hidden="true" />
          </button>
        )}
        {hasGraph && canShowMore && (
          <button
            type="button"
            className="wiki-graph__more"
            onClick={() => setNeighborLimit((current) => current + GRAPH_NEIGHBOR_STEP)}
          >
            <Plus size={17} aria-hidden="true" />再显示 20 个
          </button>
        )}
        {!hasGraph && (
          <div className="wiki-graph__empty">
            <ShareNetwork size={38} weight="duotone" aria-hidden="true" />
            <strong>
              {graphFiles.length === 0
                ? "暂无已确认关系"
                : selectedPath
                  ? "这项知识还没有已确认关系"
                  : "选择一项知识查看关系"}
            </strong>
            <span>
              {graphFiles.length === 0
                ? "完成左侧关系确认后，图谱会自动出现在这里。"
                : selectedPath
                  ? "可以回到关系整理，为它补充一条关键关系。"
                  : "选择一项知识后，这里会优先显示最重要的关联。"}
            </span>
          </div>
        )}
      </div>
      {hasGraph && (
        <div className="wiki-graph__overview">
          <div className="wiki-graph__overview-title">
            <span>关联概览</span>
            {canShowMore && <small>优先显示重要关系</small>}
          </div>
          {overviewItems.length > 0 ? overviewItems.map((item) => (
            <button type="button" key={item.targetPath} onClick={() => void selectFile(item.targetPath)}>
              <span>{item.relation}</span><strong>{item.title}</strong>
            </button>
          )) : (
            <div className="wiki-graph__overview-empty">这项知识还没有已确认关系。</div>
          )}
          <div className="wiki-graph__legend">
            <span><span className="wiki-graph__legend-dot" style={{ background: COLOR.entity }} />对象</span>
            <span><span className="wiki-graph__legend-dot" style={{ background: COLOR.concept }} />主题</span>
            <span><span className="wiki-graph__legend-dot" style={{ background: COLOR.query }} />记录</span>
          </div>
        </div>
      )}
    </div>
  );
}
