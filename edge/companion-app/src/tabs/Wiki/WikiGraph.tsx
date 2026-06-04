/** BL-CATFISH-WIKI-MODE P3.3.5 (6/4) — sigma + graphology graph view.
 *
 * 真**Obsidian graph view 类**:
 *   - entities 蓝 (#4a9eff) / concepts 橙 (#ff9933) / queries 绿 (#5fc878)
 *   - 节点 size ~ inbound link count (degree centrality)
 *   - 点击 node → store.selectFile (跳 preview)
 *   - hover 显示 title
 *   - drag + zoom + pan (sigma default)
 *
 * graph build from frontmatter.related (wikilink edges, name → file 真 title match).
 */

import { useEffect, useRef, useMemo } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import { random } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";

const COLOR = {
  entity: "#4a9eff",
  concept: "#ff9933",
  query: "#5fc878",
};

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

    const sigma = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 1,
      labelFont: "ui-sans-serif, -apple-system, sans-serif",
      labelSize: 12,
      labelWeight: "500",
      labelColor: { color: "#444" },
      defaultEdgeColor: "rgba(120, 120, 120, 0.6)",
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
      g.setNodeAttribute(node, "color", node === selectedPath ? "#ff3366" : original);
    });
    sigma.refresh();
  }, [selectedPath]);

  if (files.length === 0) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--catfish-text-muted)", fontSize: 12 }}>
        <div style={{ fontSize: 48, marginBottom: 8 }}>🕸️</div>
        <div>wiki/ 真空, 暂无图谱</div>
      </div>
    );
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          padding: "var(--space-2) var(--space-3)",
          borderBottom: "1px solid var(--catfish-border)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>关系图谱 ({files.length} nodes)</span>
        <span>
          <span style={{ color: COLOR.entity }}>● 实体</span>{" "}
          <span style={{ color: COLOR.concept }}>● 概念</span>{" "}
          <span style={{ color: COLOR.query }}>● 查询</span>
        </span>
      </div>
      <div ref={containerRef} style={{ flex: 1, background: "var(--catfish-bg)" }} />
    </div>
  );
}
