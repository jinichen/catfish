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
  // P3.5.111/112 (6/25 鸿波): 虚拟体系真**name**.
  // 鸿波点 dangling 体系 → 真**setVirtualSystem(name)** → WikiGraph 真**虚拟显**子树.
  // P3.5.112 真**优先级反**: virtualSystemName > selectedPath — 真**点子项保留虚拟态**.
  const virtualSystemName = useWikiStore((s) => s.virtualSystemName);

  // P3.5.107 B (6/25): ego-graph filter — 子图过滤
  // P3.5.108 (6/25 鸿波 "选体系看全图, 概念看子图"): 真扩 3 档 viewMode:
  //   - "auto" (默认/新加): 自动跟选中类型 — 选体系 → 体系子树 (B 方案 2 层 BFS),
  //     选子级 concept / entity / query → 1-hop ego, 不选 → 全图
  //   - "full": 强制全图 (鸿波手动覆盖路径, 跟 P3.5.107 B 真同)
  //   - "ego":  强制 1-hop 子图 (鸿波手动覆盖, 跟 P3.5.107 B 真同)
  // localStorage 老值 "ego" / "full" 真兼容 (不丢用户原配置), 新默认 "auto".
  type ViewMode = "auto" | "full" | "ego";
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try {
      const v = localStorage.getItem("wiki_graph_view_mode");
      if (v === "ego" || v === "full" || v === "auto") return v;
      return "auto"; // P3.5.108 新默认
    } catch {
      return "auto";
    }
  });
  const cycleViewMode = () => {
    // P3.5.108: 3 态循环 auto → full → ego → auto
    setViewMode((m) => {
      const next: ViewMode = m === "auto" ? "full" : m === "full" ? "ego" : "auto";
      try {
        localStorage.setItem("wiki_graph_view_mode", next);
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  // P3.5.108: 真**顶级体系判定** — kind=concept + (subtype="system" 优先, 或 title 含"体系"二字 fallback).
  //
  // P3.5.109 (6/25 鸿波 catch "+新建少了体系"): UI 加 "🌟 体系 (system)" 选项 →
  // 真**concept_type 写 "system"** (WikiCreateModal 真路径), 真**优先看 subtype === "system"**.
  // 真**老数据 fallback**: 鸿波之前手动加的"企业资质知识体系" 真**subtype 不是 system** —
  // 仍走 title 含"体系" heuristic 真**兼容**.
  //
  // 真**0 schema 改, 0 数据 backfill, 鸿波加新体系真无侵入**.
  const isSystemConcept = (f: WikiFileInfo | undefined): boolean => {
    if (!f || f.kind !== "concept") return false;
    if (f.subtype === "system") return true; // P3.5.109 真**显式**优先
    return f.title.includes("体系"); // 真**老数据 fallback**
  };

  // P3.5.108: effectiveMode 真算 — auto 模式真**自动**跟选中类型决定子图样式
  const selectedFile = useMemo(
    () => files.find((f) => f.rel_path === selectedPath),
    [files, selectedPath],
  );

  // P3.5.111/112: effectiveRoot 真**抽象**真实 file root vs 虚拟体系 root.
  // 真**rel_path === null 真**虚拟** (dangling 体系名, 无真实文件) — subtree 算法
  // 真**只 BFS children**, 真**不 add root 自己**.
  //
  // P3.5.112 (6/25 鸿波 catch "点一次就不能点了") 优先级**反转**:
  //   virtualSystemName 优先 > selectedPath
  // 真**鸿波点虚拟体系 → 显该体系子树, 然后**点子项**真**保留体系视图** (selectedPath 真
  // 只用于高亮 + preview, 不切子图). 真**清虚拟态**走 setVirtualSystem(null) / 切别的
  // 体系 header / 强制全图.
  const effectiveRoot: { title: string; rel_path: string | null } | null = useMemo(() => {
    if (virtualSystemName) {
      return { title: virtualSystemName, rel_path: null };
    }
    if (selectedPath) {
      const f = files.find((x) => x.rel_path === selectedPath);
      return f ? { title: f.title, rel_path: f.rel_path } : null;
    }
    return null;
  }, [virtualSystemName, selectedPath, files]);

  // effectiveMode 真**真实生效**真 mode: "full" / "ego" / "subtree" (B 方案体系子树)
  const effectiveMode: "full" | "ego" | "subtree" = useMemo(() => {
    if (viewMode === "full") return "full";
    if (viewMode === "ego") return effectiveRoot ? "ego" : "full"; // ego 真无选中 fallback full
    // viewMode === "auto"
    if (!effectiveRoot) return "full";
    // P3.5.111: 虚拟体系 (无 rel_path) 真**强制 subtree** — 鸿波想看整片体系图
    if (effectiveRoot.rel_path === null) return "subtree";
    if (isSystemConcept(selectedFile)) return "subtree";
    return "ego";
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, effectiveRoot, selectedFile]);

  // P3.5.108/111: deps key 真精准 — full mode 真不依赖 selected (避免 sigma rebuild).
  // P3.5.111: 虚拟体系真 egoKey 含 virtualSystemName 真**虚拟切换也 rebuild**.
  const egoKey =
    effectiveMode === "full" ? "" : selectedPath || virtualSystemName || "";

  // build graphology graph from files + related wikilinks
  const graph = useMemo(() => {
    const g = new Graph({ multi: false, type: "directed" });

    // P3.5.107 B: 真**ego mode 真子集** — 先算 1-hop neighborhood set, 真**只 add 这些 node**.
    // helper: name → file lookup (复用下面的逻辑顺序)
    const findTargetForFilter = (name: string): WikiFileInfo | null => {
      const lower = name.toLowerCase().trim();
      return (
        files.find((f) => f.title.toLowerCase() === lower) ||
        files.find((f) => f.slug.toLowerCase() === lower) ||
        files.find((f) => f.title.toLowerCase().includes(lower)) ||
        null
      );
    };

    let nodeFilter: Set<string> | null = null;
    // P3.5.111: ego 真**仅真实 selectedPath 才走** (虚拟体系真**走 subtree**, 不走 ego)
    if (effectiveMode === "ego" && selectedPath) {
      // 1-hop ego: selected + 直接邻居 (out related + in 反向 related)
      const ego = new Set<string>([selectedPath]);
      const selFile = files.find((f) => f.rel_path === selectedPath);
      // out-edges: selected 自己的 related
      if (selFile) {
        for (const r of selFile.related) {
          const target = findTargetForFilter(r);
          if (target) ego.add(target.rel_path);
        }
      }
      // in-edges: 谁的 related 真包含 selected (反向扫所有 file)
      for (const f of files) {
        if (f.rel_path === selectedPath) continue;
        for (const r of f.related) {
          const target = findTargetForFilter(r);
          if (target && target.rel_path === selectedPath) {
            ego.add(f.rel_path);
            break;
          }
        }
      }
      nodeFilter = ego;
    } else if (effectiveMode === "subtree" && effectiveRoot) {
      // P3.5.108 B 方案 + P3.5.111 虚拟体系支持: 体系子树严格 2 层 BFS 下挖
      // (体系 → 子 concept → entity). 真**不 follow** 其他 edge — 避免跨体系泄漏.
      //
      // P3.5.111: 真**虚拟体系 (effectiveRoot.rel_path === null)** — root 真**没真实文件**
      // 不 add 进 set, 真**只 add children**. 真**鸿波看到的"虚拟体系子树"就是这逻辑**.
      const subtree = new Set<string>();
      if (effectiveRoot.rel_path) subtree.add(effectiveRoot.rel_path);
      const rootTitle = effectiveRoot.title.trim().toLowerCase();
      // 第 1 层: 找所有 concept 真 related[0] 真**指向 root** (上位体系是 root)
      const layer1ConceptTitles = new Set<string>();
      for (const f of files) {
        if (f.kind !== "concept") continue;
        if (effectiveRoot.rel_path && f.rel_path === effectiveRoot.rel_path) continue;
        const parent = (f.related[0] || "").trim().toLowerCase();
        if (parent === rootTitle) {
          subtree.add(f.rel_path);
          layer1ConceptTitles.add(f.title.trim().toLowerCase());
        }
      }
      // 第 2 层: 找所有 entity 真 related[0] 真**指向第 1 层任意 concept**
      for (const f of files) {
        if (f.kind !== "entity") continue;
        const parent = (f.related[0] || "").trim().toLowerCase();
        if (layer1ConceptTitles.has(parent)) {
          subtree.add(f.rel_path);
        }
      }
      // 真**也兜底**: 真有 entity 真**直接**指向 root 体系 (跨级关联), 也算
      for (const f of files) {
        if (f.kind !== "entity") continue;
        if (subtree.has(f.rel_path)) continue;
        const parent = (f.related[0] || "").trim().toLowerCase();
        if (parent === rootTitle) {
          subtree.add(f.rel_path);
        }
      }
      nodeFilter = subtree;
    }
    // effectiveMode === "full" — nodeFilter 真 null, 真全 build (现行为)

    // 1. node — 每 file 1 node (size 4-8 范围, 跟 Obsidian 一致小巧)
    for (const f of files) {
      if (nodeFilter && !nodeFilter.has(f.rel_path)) continue;
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
      if (nodeFilter && !nodeFilter.has(f.rel_path)) continue;
      for (const r of f.related) {
        const target = findTarget(r);
        if (!target) continue; // dangling, skip
        if (target.rel_path === f.rel_path) continue; // self-link
        if (nodeFilter && !nodeFilter.has(target.rel_path)) continue; // ego 真**只内部 edge**
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

    // P3.5.42.12 砍 (基于错假设): 我以为 subtype = concept name, 实际 LLM prompt
    // 写的是 enum (person/org/system/cert/project), findTarget('cert') 永远找不
    // 到同名 concept (concepts 是中文具体名), 那段 implicit edge 路径根本没生效.
    // 真因在 wiki_read.rs: parse_related 只读 frontmatter, 不扫 body 的 wikilink.
    // LLM 实际把"信息安全与安防类"这种 concept name 写在 entity body 里成
    // `[[...]]` 形态. P3.5.42.13 修后端扫 body 合并进 related, 这边自动有 edge.

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
    // P3.5.108: deps 真**effectiveMode + egoKey** — full mode 切 selectedPath 真不 rebuild
    // (egoKey 真在 full 时空字串, 不变), ego/subtree mode 切 selectedPath 真 rebuild
  }, [files, effectiveMode, egoKey]);

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

  // P3.5.108: 真显当前节点数 + 真生效 mode 提示 (auto 真**显推断真子标签**)
  // P3.5.112 (6/25 鸿波 catch "不要橙色提示混乱"): modeLabel 不再区分虚拟/真实 — 视觉一致
  const visibleNodeCount = graph.order;
  const modeLabel = (() => {
    if (viewMode === "full") return { icon: "🌐", text: "全图", tip: "强制全图: 显示全部 wiki 节点 (点切到 🔍 子图)" };
    if (viewMode === "ego") return { icon: "🔍", text: "子图", tip: "强制子图: 1-hop 邻居 (点切回 ✨ 自动)" };
    // auto 真**显**子标签 — P3.5.112: 虚拟态跟真实态视觉一致, 不区分
    if (effectiveMode === "subtree") return { icon: "✨", text: "自动·体系", tip: "自动: 选中体系真显子树 (点切到 🌐 强制全图)" };
    if (effectiveMode === "ego") return { icon: "✨", text: "自动·子图", tip: "自动: 选中概念/实体真显 1-hop (点切到 🌐 强制全图)" };
    return { icon: "✨", text: "自动·全图", tip: "自动: 未选中真显全图 (点切到 🌐 强制全图)" };
  })();
  // ego 真但没选中真 fallback full — 提示用户先在左边点
  const isManualEgoButNoSelection = viewMode === "ego" && !selectedPath && !virtualSystemName;
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="wiki-graph__header">
        <span>
          关系图谱 · {visibleNodeCount} 节点
        </span>
        {/* P3.5.108 (6/25 鸿波 "选体系看全图, 概念看子图"): 真 3 态 toggle */}
        <button
          type="button"
          onClick={cycleViewMode}
          title={modeLabel.tip}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            marginLeft: 6,
            border: "1px solid var(--catfish-border)",
            borderRadius: 10,
            background:
              viewMode === "auto"
                ? "var(--catfish-bg-elevated, rgba(0,0,0,0.03))"
                : viewMode === "ego"
                ? "var(--catfish-cyan, #0E5F66)"
                : "var(--catfish-orange, #F47B3D)",
            color:
              viewMode === "auto" ? "var(--catfish-text)" : "#fff",
            cursor: "pointer",
            fontWeight: 500,
          }}
        >
          {modeLabel.icon} {modeLabel.text}
        </button>
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
      {isManualEgoButNoSelection && (
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            background: "var(--catfish-bg-elevated, rgba(0,0,0,0.03))",
            borderBottom: "1px solid var(--catfish-border-soft, rgba(0,0,0,0.05))",
          }}
        >
          💡 强制子图 — 在左边点一个体系/概念/实体, 拓扑只显它和邻居 (或切回 ✨ 自动)
        </div>
      )}
      {/* P3.5.112 (6/25 鸿波 catch "不要橙色提示混乱"): 砍掉 P3.5.111 真橙色虚拟体系提示条.
          鸿波**真想建立**体系真**走左上 "+新建" 按钮** 真**已经够了**, header 不需要重复入口. */}
      <div ref={containerRef} style={{ flex: 1, background: "var(--catfish-bg)" }} />
    </div>
  );
}
