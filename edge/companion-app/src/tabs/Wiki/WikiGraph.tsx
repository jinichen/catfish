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

    // 1. node — 每 file 1 node
    for (const f of files) {
      const color = COLOR[f.kind] || "#888";
      g.addNode(f.rel_path, {
        label: f.title,
        kind: f.kind,
        slug: f.slug,
        color,
        size: 4, // 基础大小, 下面按 degree 调
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
            size: 0.5,
            color: "#cccccc88",
          });
        } catch {
          /* duplicate / 真**真**真**真 silent skip */
        }
      }
    }

    // 3. size by degree (inbound + outbound) — hub 节点真大
    g.forEachNode((node, attrs) => {
      const degree = g.degree(node);
      g.setNodeAttribute(node, "size", 3 + Math.min(degree * 1.5, 12));
    });

    // 4. positions — 简单**circular layout** (P3.3.5 mvp; future ForceAtlas2)
    const nodes = g.nodes();
    const N = nodes.length;
    const radius = Math.max(200, N * 8);
    nodes.forEach((node, i) => {
      const angle = (i / N) * Math.PI * 2;
      g.setNodeAttribute(node, "x", Math.cos(angle) * radius);
      g.setNodeAttribute(node, "y", Math.sin(angle) * radius);
    });

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
      labelRenderedSizeThreshold: 6,
      labelFont: "ui-sans-serif, -apple-system, sans-serif",
      labelSize: 11,
      defaultEdgeColor: "#cccccc88",
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
