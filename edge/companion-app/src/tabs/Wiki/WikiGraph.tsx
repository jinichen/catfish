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

import { useEffect, useRef, useMemo } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import { random } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";

// E5: brand 一致 3 色 (tokens.css 没暴露 hex 给 JS, 这里 mirror).
// 跟 .wiki-kind-badge--<kind> 视觉一致.
const COLOR = {
  entity: "#0E5F66",   // 墨青 (catfish-cyan)
  concept: "#F47B3D",  // 暖橙 (catfish-orange)
  query: "#6B8589",    // 灰青 (low-saturation, muted)
};
const COLOR_SELECTED = "#1A8A95"; // cyan-bright (替 #ff3366 粉红)

export default function WikiGraph() {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const files = useWikiStore((s) => s.files);
  const selectedPath = useWikiStore((s) => s.selectedPath);
  const selectFile = useWikiStore((s) => s.selectFile);

  // build graphology graph from files + related wikilinks
  const graph = useMemo(() => {
    const g = new Graph({ multi: false, type: "directed" });

    // 1. node — 每 file 1 node (size 4-8 范围, 跟 Obsidian 一致小巧)
    for (const f of files) {
      const color = COLOR[f.kind] || "#888";
      g.addNode(f.rel_path, {
        label: f.title,
        kind: f.kind,
        slug: f.slug,
        color,
        size: 4,
      });
    }

    // 2. edge — frontmatter.related 真**`[[name]]`** 抽 → 找 target 真 rel_path
    //    helper: 真**name → file** 真 lookup (title / slug 模糊 match)
    function findTarget(name: string): WikiFileInfo | null {
      const lower = name.toLowerCase().trim();
      return (
        files.find((f) => f.title.toLowerCase() === lower) ||
        files.find((f) => f.slug.toLowerCase() === lower) ||
        files.find((f) => f.title.toLowerCase().includes(lower)) ||
        null
      );
    }

    for (const f of files) {
      for (const r of f.related) {
        const target = findTarget(r);
        if (!target) continue; // dangling, skip
        if (target.rel_path === f.rel_path) continue; // self-link
        // edge key 真**`<src>→<dst>`** 防重复
        const edgeKey = `${f.rel_path}→${target.rel_path}`;
        if (g.hasEdge(edgeKey)) continue;
        try {
          g.addEdgeWithKey(edgeKey, f.rel_path, target.rel_path, {
            size: 1,
            color: "rgba(120, 120, 120, 0.6)",
          });
        } catch {
          /* duplicate / 真**真**真**真 silent skip */
        }
      }
    }

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

    return g;
  }, [files]);

  // sigma 初始化 + 真**update**真
  useEffect(() => {
    if (!containerRef.current) return;

    // teardown 老 sigma
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    if (graph.order === 0) return;

    // P41 (6/5 鸿波): 暗色 mode 真**`label color **真**`runtime detect**, 不再硬编码
    // #444 (暗背景下不可读).
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const labelColor = isDark ? "#e0e0e0" : "#444";
    const edgeColor = isDark ? "rgba(180, 180, 180, 0.4)" : "rgba(120, 120, 120, 0.6)";

    const sigma = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 1,
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

  // highlight selected node — 真**sigma.refresh 后**真 update color**真
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

  if (files.length === 0) {
    return (
      <div className="wiki-graph__empty">
        <div className="wiki-graph__empty-icon">○</div>
        <div>wiki/ 暂无图谱</div>
      </div>
    );
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="wiki-graph__header">
        <span>关系图谱 · {files.length} 节点</span>
        <span className="wiki-graph__legend">
          <span>
            <span
              className="wiki-graph__legend-dot"
              style={{ background: COLOR.entity }}
            />
            实体
          </span>
          <span>
            <span
              className="wiki-graph__legend-dot"
              style={{ background: COLOR.concept }}
            />
            概念
          </span>
          <span>
            <span
              className="wiki-graph__legend-dot"
              style={{ background: COLOR.query }}
            />
            查询
          </span>
        </span>
      </div>
      <div ref={containerRef} style={{ flex: 1, background: "var(--catfish-bg)" }} />
    </div>
  );
}
