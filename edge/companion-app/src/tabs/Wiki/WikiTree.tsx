/** BL-CATFISH-WIKI-MODE P3.3.3 (6/4) — tree view 实际渲染.
 *
 * 3 group: entities / concepts / queries — 列 wiki_list_files 真返回 file.
 * search box (filename + title 模糊 match) + kind filter (all / entity / concept / query).
 * 点击 file → store.selectFile(rel_path) → 中列 preview 加载.
 */

import { useEffect, useMemo, useState } from "react";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";
import { wikiCreateEntityOrConcept } from "../../lib/tauri"; // P3.5.114: dangling click → 真自动建真文件
import WikiCreateModal from "./WikiCreateModal";
// P3.5.173 (7/3 鸿波): 📊 从 xlsx 导入 modal, 严格批量抽体系 concept + 部门 entity 树.
import XlsxImportModal from "./XlsxImportModal";

export default function WikiTree() {
  const files = useWikiStore((s) => s.files);
  // P3.3.18 Phase 4 (6/10): 已装部门 wiki
  const sharedFiles = useWikiStore((s) => s.sharedFiles);
  const filesLoading = useWikiStore((s) => s.filesLoading);
  const filesError = useWikiStore((s) => s.filesError);
  const selectedPath = useWikiStore((s) => s.selectedPath);
  const search = useWikiStore((s) => s.search);
  const kindFilter = useWikiStore((s) => s.kindFilter);
  const query = useWikiStore((s) => s.query);
  const selectedTag = useWikiStore((s) => s.selectedTag);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const selectFile = useWikiStore((s) => s.selectFile);
  const setSearch = useWikiStore((s) => s.setSearch);
  const setKindFilter = useWikiStore((s) => s.setKindFilter);
  const setQuery = useWikiStore((s) => s.setQuery);
  const setSelectedTag = useWikiStore((s) => s.setSelectedTag);
  // P3.5.110: modal trigger 改用 store 支持跨组件 trigger (鸿波点 dangling
  // wikilink / 组 header → WikiPreview / CategorySubgroup 也能弹).
  const createModalState = useWikiStore((s) => s.createModalState);
  const openCreateModal = useWikiStore((s) => s.openCreateModal);
  const closeCreateModal = useWikiStore((s) => s.closeCreateModal);
  // P3.5.173 (7/3 鸿波): 📊 从 xlsx 导入 modal 状态 (内嵌 useState, 无独立 store,
  // 严格 modal 只从 WikiTree 触发, 无跨组件状态需求).
  const [xlsxImportOpen, setXlsxImportOpen] = useState(false);

  // P37 (BM25) + P38 (语义) — mode tristate: "title" | "body" | "semantic"
  const [searchMode, setSearchMode] = useState<"title" | "body" | "semantic">("title");
  const [searchHits, setSearchHits] = useState<import("../../lib/tauri").WikiSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [semanticMessage, setSemanticMessage] = useState<string>("");

  // P37/P38: body/semantic 真 debounce 300ms
  useEffect(() => {
    if (searchMode === "title" || !search.trim()) {
      setSearchHits([]);
      setSemanticMessage("");
      return;
    }
    const handle = setTimeout(async () => {
      setSearching(true);
      setSemanticMessage("");
      try {
        const tauri = await import("../../lib/tauri");
        if (searchMode === "body") {
          const hits = await tauri.wikiSearchText(search);
          setSearchHits(hits);
        } else {
          // semantic
          const res = await tauri.wikiSearchSemantic(search);
          if (!res.model_loaded) {
            setSearchHits([]);
            setSemanticMessage(res.message);
          } else {
            // 复用 WikiSearchHit shape: score / snippet / matched_in
            setSearchHits(
              res.hits.map((h) => ({
                rel_path: h.rel_path,
                title: h.title,
                kind: h.kind,
                score: h.score,
                snippet: h.snippet,
                matched_in: ["semantic"],
              })),
            );
            setSemanticMessage(`✓ ${res.indexed_count} entries 已 index`);
          }
        }
      } catch (e) {
        console.warn("[wiki search] 失败:", e);
        setSearchHits([]);
        setSemanticMessage(String(e));
      } finally {
        setSearching(false);
      }
    }, searchMode === "semantic" ? 500 : 300);   // semantic `后台 indexer 慢, 500ms debounce真
    return () => clearTimeout(handle);
  }, [search, searchMode]);

  // P17 (6/5 鸿波): 切到知识体系 tab 立即 reload (App.tsx 真`activeTab === 'wiki'
  // && <WikiTab/>` 真 conditional render — 切走 unmount, 切回 mount 跑这 effect).
  // 不用 cache expiry — 鸿波要求即刻刷新, 防 LLM 外部 write_file 后 list 老.
  useEffect(() => {
    if (!filesLoading) {
      void loadFiles();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 真 inbound link count per file (dangling/orphan 用)
  const inboundMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of files) {
      for (const r of f.related) {
        // P3.5.132 #5: r 真 RelatedRef, name match target file 真 title / slug.
        const lower = r.name.toLowerCase();
        const target = files.find(
          (x) =>
            x.title.toLowerCase() === lower ||
            x.slug.toLowerCase() === lower ||
            x.title.toLowerCase().includes(lower)
        );
        if (target) {
          m.set(target.rel_path, (m.get(target.rel_path) || 0) + 1);
        }
      }
    }
    return m;
  }, [files]);

  // dangling wikilinks (file 真 related 含真 target 找不到)
  const danglingMap = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const f of files) {
      const dangling: string[] = [];
      for (const r of f.related) {
        // P3.5.132 #5: 用 r.name match
        const lower = r.name.toLowerCase();
        const found = files.some(
          (x) =>
            x.title.toLowerCase() === lower ||
            x.slug.toLowerCase() === lower ||
            x.title.toLowerCase().includes(lower)
        );
        if (!found) dangling.push(r.name); // P3.5.132 #5: 存 name 字符串方便 UI 展示
      }
      if (dangling.length > 0) m.set(f.rel_path, dangling);
    }
    return m;
  }, [files]);

  // top tag count
  const topTags = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of files) {
      for (const t of f.tags) {
        counts.set(t, (counts.get(t) || 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [files]);

  // filter + search + query
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const oneWeekAgo = Date.now() / 1000 - 7 * 86400;
    return files.filter((f) => {
      if (kindFilter !== "all" && f.kind !== kindFilter) return false;
      if (q) {
        const hay = (f.title + " " + f.slug).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      // query 模板 filter
      if (query === "recent-week" && f.mtime < oneWeekAgo) return false;
      if (query === "orphan-concept") {
        if (f.kind !== "concept") return false;
        if ((inboundMap.get(f.rel_path) || 0) > 0) return false;
      }
      if (query === "dangling" && !danglingMap.has(f.rel_path)) return false;
      if (query === "top-tag" && selectedTag && !f.tags.includes(selectedTag)) return false;
      return true;
    });
  }, [files, search, kindFilter, query, selectedTag, inboundMap, danglingMap]);

  const grouped = useMemo(() => {
    const g = {
      entity: [] as WikiFileInfo[],
      concept: [] as WikiFileInfo[],
      query: [] as WikiFileInfo[],
    };
    for (const f of filtered) {
      if (f.kind === "entity") g.entity.push(f);
      else if (f.kind === "concept") g.concept.push(f);
      else if (f.kind === "query") g.query.push(f);
    }
    return g;
  }, [filtered]);

  // P3.5.99 (6/24 鸿波 catch "62 实体看不过来, 200 真要爆"): 实体内部按
  // related[0] (第一个关联的 concept) 自动二级分组. 数据天然形成 — 鲶鱼
  // distill 时把 entity 的 body 写了 `[[XXX 类]]` wikilink (P3.5.42.13 后
  // 端 merge 进 related). 7 个 concept hub 已经是天然类目.
  //
  // 无 related 的 entity → "未分类" 组. 默认推到最后.
  // count desc 排, 大类在前 (UX: 先看主流, 末尾扫边角).
  const entityCategories = useMemo(() => {
    const map = new Map<string, WikiFileInfo[]>();
    for (const e of grouped.entity) {
      const category = e.related[0]?.name?.trim() || "未分类"; // P3.5.132 #5
      if (!map.has(category)) map.set(category, []);
      map.get(category)!.push(e);
    }
    return Array.from(map.entries()).sort((a, b) => {
      // 未分类推最后
      if (a[0] === "未分类") return 1;
      if (b[0] === "未分类") return -1;
      // 其他按 count desc
      return b[1].length - a[1].length;
    });
  }, [grouped.entity]);

  // P3.5.107 A (6/25 鸿波 catch "只有 1 个体系, 加另一个体系能不能"):
  // concept 也走 P3.5.99 同套路真二级分组 — 按 concept.related[0] 上位体系.
  //
  // 数据真因 (审鸿波截图无人机类 concept 真 body): LLM distill 真在 concept body
  // 第一行写 `[[企业资质知识体系]]` 标上位 hub. wiki_read.rs 真 merge_related_with_body
  // 真把它合到 related[]. 所以 concept "无人机类" 真 related[0] = "企业资质知识体系".
  //
  // 0 schema 改 0 数据 backfill: 鸿波想加"政企客户体系" 真路径:
  //   1. 新建 concept "政企客户体系" (顶级体系, 自己 related[0] 空 → 进 "🌟 顶级体系" 组)
  //   2. 新建 concept "央国企" 真 body 写 `[[政企客户体系]]` → 进 "政企客户体系" 组下
  //   3. entity "中移动" body 写 `[[央国企]]` → 进 entity 二级 "央国企" 组
  //   真自然 3 级层级, 0 代码改.
  //
  // 特殊: concept 真 related[0] 空 → "🌟 顶级体系" (顶级 hub, 排第一不排最后)
  // — 跟 entity 真"未分类"语义不同 (未分类是数据残缺, 顶级体系是结构性 root).
  // P3.5.115 (6/25 鸿波 catch "UI 别扭"): 砍顶级体系组真重复.
  // 老逻辑: "🌟 顶级体系 (1) — 企业资质知识体系" + "企业资质知识体系 (10) — 子级"
  // 两组都跟父名相关, 视觉重复.
  //
  // 新逻辑: 顶级体系父 file有子级 → 直接作为该组 header (不重复进"顶级"组).
  // 孤儿 system (无子级) → 兜底进"🌟 顶级体系"组真显示.
  // header 点击: P3.5.113 match 路径 → lookup file → selectFile 跳父 preview ✓
  const conceptCategories = useMemo(() => {
    const map = new Map<string, WikiFileInfo[]>();
    // 第 1 轮: 有 related[0] 真 concept 进各自父组
    for (const c of grouped.concept) {
      const category = c.related[0]?.name?.trim(); // P3.5.132 #5
      if (category) {
        if (!map.has(category)) map.set(category, []);
        map.get(category)!.push(c);
      }
    }
    // 第 2 轮: 孤儿 concept (related[0] 空) — 看它是否已是某组 header (子级真组真 key)
    // 已有子级0 额外处理 (header click 走 P3.5.113 match 路径跳父 preview);
    // 无子级 (真孤儿)进"🌟 顶级体系"组兜底.
    for (const c of grouped.concept) {
      if (c.related[0]?.name?.trim()) continue; // P3.5.132 #5: 已在第 1 轮处理
      const hasChildren = map.has(c.title); // 自己是否为某组真 key
      if (hasChildren) continue; // header 已经隐含就是它, 0 重复
      // 孤儿兜底
      const topKey = "🌟 顶级体系";
      if (!map.has(topKey)) map.set(topKey, []);
      map.get(topKey)!.push(c);
    }
    return Array.from(map.entries()).sort((a, b) => {
      // 顶级体系 (孤儿兜底组) 推最前 — 根节点先看
      if (a[0] === "🌟 顶级体系") return -1;
      if (b[0] === "🌟 顶级体系") return 1;
      // 其他按 count desc
      return b[1].length - a[1].length;
    });
  }, [grouped.concept]);

  // E4 (6/6 taste-skill 改造): 走 globals.css `.wiki-*` class.
  // 主要改: hardcoded `#0d9488` `#4a9eff` 非 brand 色统一到 brand 墨青;
  // mode + kind toggle 改 pill segmented control; 去 emoji; 加 hover/focus.
  return (
    <div style={{ padding: "var(--space-3)", fontSize: 12 }}>
      <div className="wiki-tree__title">
        <h3>知识体系</h3>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => openCreateModal()}
            title="新建 entity / concept"
            className="approval-banner__btn-primary"
            style={{ padding: "4px 12px", fontSize: 12 }}
          >
            + 新建
          </button>
          {/* P3.5.173 (7/3 鸿波): 📊 从 xlsx 批量导入组织架构 (体系 concept + 部
              门 entity 树). 严格 SheetJS 前端 parse + batch wikiCreateEntityOrConcept
              + progress. 员工 mac 需 `npm i xlsx` 装依赖. */}
          <button
            onClick={() => setXlsxImportOpen(true)}
            title="从 xlsx 批量导入组织架构 (体系 + 部门 + 关联树)"
            className="approval-banner__btn-link"
            style={{ padding: "4px 10px", fontSize: 12 }}
          >
            📊
          </button>
          <button
            onClick={() => void loadFiles()}
            title="刷新"
            className="approval-banner__btn-link"
            style={{ padding: "4px 10px", fontSize: 13 }}
          >
            ↻
          </button>
        </div>
      </div>

      {createModalState.open && (
        <WikiCreateModal
          onClose={closeCreateModal}
          prefillTitle={createModalState.prefillTitle}
          prefillKind={createModalState.prefillKind}
        />
      )}

      {/* P3.5.173 (7/3 鸿波): 📊 xlsx 批量导入 modal. 员工点 📊 button → 弹.
          导入完 loadFiles + close 严格无侵. 员工 mac 需 `npm i xlsx`. */}
      {xlsxImportOpen && (
        <XlsxImportModal
          onClose={() => setXlsxImportOpen(false)}
          onImported={() => {
            void loadFiles();
          }}
        />
      )}

      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={
          searchMode === "title"
            ? "按标题 / slug 搜..."
            : searchMode === "body"
              ? "全文 BM25 搜..."
              : "语义搜索 (BGE-M3 本机)..."
        }
        className="wiki-search-input"
        style={{ marginBottom: 6 }}
      />
      {/* P37+P38 (6/5) mode toggle — E4 改 pill segmented, 单 brand 墨青 accent */}
      <div className="wiki-segmented">
        {(["title", "body", "semantic"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setSearchMode(m)}
            className="wiki-segmented__btn"
            data-active={searchMode === m}
          >
            {m === "title" ? "标题" : m === "body" ? "全文" : "语义"}
          </button>
        ))}
      </div>
      {semanticMessage && (
        <div
          className={
            "wiki-semantic-msg " +
            (semanticMessage.startsWith("BGE-M3 model 未装") ? "wiki-semantic-msg--mono" : "")
          }
        >
          {semanticMessage}
        </div>
      )}

      {/* kind filter — 跟 mode toggle 同 segmented pattern, 视觉一致 */}
      <div className="wiki-segmented">
        {(["all", "entity", "concept", "query"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setKindFilter(k)}
            className="wiki-segmented__btn"
            data-active={kindFilter === k}
          >
            {k === "all" ? "全部" : k === "entity" ? "实体" : k === "concept" ? "概念" : "查询"}
          </button>
        ))}
      </div>

      {/* P3.3.9 dataview 预制 query */}
      <select
        value={query}
        onChange={(e) => {
          const v = e.target.value as typeof query;
          setQuery(v);
          if (v !== "top-tag") setSelectedTag(null);
        }}
        style={{
          width: "100%",
          padding: "4px 8px",
          fontSize: 11,
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          marginBottom: "var(--space-2)",
          background: query !== "none" ? "rgba(74, 158, 255, 0.15)" : "var(--catfish-bg)",
          color: "var(--catfish-text)",
        }}
      >
        <option value="none">— 预制 query —</option>
        <option value="recent-week">📅 本周新增 (mtime &lt; 7d)</option>
        <option value="orphan-concept">🏝️ 孤立概念 (0 inbound)</option>
        <option value="dangling">⚠️ 含 dangling wikilink</option>
        <option value="top-tag">🏷️ 按标签 filter</option>
      </select>

      {query === "top-tag" && topTags.length > 0 && (
        <div style={{ marginBottom: "var(--space-2)", display: "flex", flexWrap: "wrap", gap: 4 }}>
          {topTags.map(([tag, count]) => (
            <button
              key={tag}
              onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
              style={{
                padding: "2px 6px",
                fontSize: 10,
                border: "1px solid var(--catfish-border)",
                borderRadius: 10,
                background: selectedTag === tag ? "var(--catfish-accent, #4a9eff)" : "var(--catfish-bg)",
                color: selectedTag === tag ? "#fff" : "var(--catfish-text)",
                cursor: "pointer",
              }}
            >
              #{tag} ({count})
            </button>
          ))}
        </div>
      )}

      {filesLoading && <div style={{ color: "var(--catfish-text-muted)" }}>加载中...</div>}
      {filesError && (
        <div style={{ color: "var(--status-err)", fontSize: 11 }}>
          ✗ {filesError}
        </div>
      )}

      {!filesLoading && files.length === 0 && !filesError && (
        <EmptyOnboarding onCreateClick={() => openCreateModal()} />
      )}

      {/* P37+P38 (6/5 鸿波): body/semantic 模式 → hits 列表替 group tree */}
      {searchMode !== "title" && search.trim() && (
        <SearchResults
          hits={searchHits}
          searching={searching}
          selectedPath={selectedPath}
          onSelect={selectFile}
        />
      )}

      {!(searchMode !== "title" && search.trim()) && (
        <>
          {/* P3.5.99 (6/24 鸿波): 实体改 EntityGroup 走二级分组 (related[0]),
              避免 62/200 全平铺. */}
          <EntityGroup
            total={grouped.entity.length}
            categories={entityCategories}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          {/* P3.5.107 A (6/25 鸿波): 概念也走二级分组 (related[0]) — 体系 → 类目 真 3 级层级 */}
          <ConceptGroup
            total={grouped.concept.length}
            categories={conceptCategories}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          <Group label="查询 (queries)" emoji="💬" color="#5fc878" files={grouped.query} selectedPath={selectedPath} onSelect={selectFile} />
          {/* P3.3.18 Phase 4 (6/10): 已装部门 wiki — read-only, 跟个人 wiki 视觉分离 */}
          {sharedFiles.length > 0 && (
            <Group
              label="部门 wiki (read-only)"
              emoji="📥"
              color="#7c3aed"
              files={sharedFiles.map((s) => {
                const kindNarrow: "entity" | "concept" | "query" =
                  s.kind === "concept" || s.kind === "query" ? s.kind : "entity";
                return {
                  rel_path: s.relPath,
                  kind: kindNarrow,
                  slug: s.fileId,
                  title: `${s.title} · ${s.namespace.replace("dept/", "")}`,
                  subtype: null,
                  tags: [],
                  related: [],
                  sources: [`by ${s.publishedBy || "?"}`],
                  size_bytes: s.sizeBytes,
                  mtime: 0,
                };
              })}
              selectedPath={selectedPath}
              onSelect={selectFile}
            />
          )}
        </>
      )}
    </div>
  );
}

function Group({
  label,
  emoji,
  color,
  files,
  selectedPath,
  onSelect,
}: {
  label: string;
  emoji: string;
  color: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
}) {
  // P40 (6/5 鸿波): Group collapsible. localStorage 记 collapse state per label.
  // 默认: entities (常长 21+) 折起; concepts/queries 展开. Wiki 累积后 sidebar
  // 一眼看 3 group header, 不用 scroll.
  const lsKey = `wiki_group_collapsed_${label}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return label.includes("实体"); // entity 默认折 (最长)
      return v === "1";
    } catch {
      return false;
    }
  });
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* 配额满 ignore */
      }
      return next;
    });
  };
  if (files.length === 0) return null;
  return (
    <div className={"wiki-group " + (collapsed ? "" : "wiki-group--open")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        style={{ color }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>{emoji} {label}</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {files.length}
        </span>
      </div>
      {!collapsed && (
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {files.map((f) => {
          const active = selectedPath === f.rel_path;
          return (
            <li key={f.rel_path}>
              <button
                onClick={() => onSelect(f.rel_path)}
                title={f.rel_path}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "4px 8px",
                  fontSize: 12,
                  border: "none",
                  background: active ? "var(--catfish-bg-hover, #e8f0ff)" : "transparent",
                  color: active ? color : "var(--catfish-text)",
                  cursor: "pointer",
                  borderRadius: 4,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {f.title}
                {f.subtype && (
                  <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
                    {f.subtype}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      )}
    </div>
  );
}

/** P3.5.99 (6/24 鸿波) — 实体二级分组 group: 顶层 "实体 (62)" + 内嵌 N 个
 *  CategorySubgroup ("信息安全与安防类 (15)" 等), 跟图谱里 concept hub 一致.
 *
 *  数据驱动: entity.related[0] (P3.5.42.13 后端 merge body wikilink), 0 人工干预.
 *  localStorage key 跟原 Group "实体 (entities)" 保持兼容 (用户原折叠状态不丢).
 */
function EntityGroup({
  total,
  categories,
  selectedPath,
  onSelect,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
}) {
  const lsKey = "wiki_group_collapsed_实体 (entities)"; // 跟原 Group 一致, 不丢用户折叠
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return true; // 默认折 (62 行太长)
      return v === "1";
    } catch {
      return true;
    }
  });
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  if (total === 0) return null;
  return (
    <div className={"wiki-group " + (collapsed ? "" : "wiki-group--open")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        style={{ color: "#4a9eff" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>🧑 实体 (entities)</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {!collapsed && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** P3.5.99 — 实体的二级 category 子组. 跟 Group 同款 collapsible + localStorage,
 *  但视觉更紧 (字号 / padding 缩小, 加缩进区分). 默认折叠 (二级太多, 全开会爆).
 *
 *  P3.5.110 (6/25 鸿波 catch "体系名称不能选择") 升级:
 *  - 点 caret (▶) → 折叠 (跟原行为一致, stopPropagation)
 *  - 点 category name → 优先 selectFile(找到的 concept file), dangling 弹 +新建 modal
 *    (prefill title=category, 默认 kind=system) — 治体系 dangling 不能点的核心痛点
 */
function CategorySubgroup({
  category,
  files,
  selectedPath,
  onSelect,
}: {
  category: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
}) {
  // P3.5.110: 从 store 拿 files (全 wiki) lookup, 跟 onSelect 同源
  const allFiles = useWikiStore((s) => s.files);
  // P3.5.111: dangling 体系名 click → setVirtualSystem (而不是 openCreateModal — 鸿波 catch)
  const setVirtualSystem = useWikiStore((s) => s.setVirtualSystem);
  // P3.5.113: dangling click 也 clear selectedPath → 触发 useMemo rebuild
  const selectFile = useWikiStore((s) => s.selectFile);
  // P3.5.114 (6/25 鸿波 catch "应该形成真文件才合理"): dangling click → 自动建真文件
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const lsKey = `wiki_subgroup_collapsed_${category}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return true; // 默认折
      return v === "1";
    } catch {
      return true;
    }
  });
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  // P3.5.110-114: 点 category name 真行为 (鸿波 6/25 P3.5.114 catch "应该形成真文件才合理"):
  //   - 特殊组 ("🌟 顶级体系" / "未分类") → 只 toggle
  //   - 真实文件 (match): setVirtualSystem(null) + selectFile(rel_path) → 切到该 system 子树
  //     (清旧虚拟态, 避免被它优先级压住)
  //   - dangling (虚拟体系): 自动建真文件 (concept + subtype=system) → selectFile(新 path)
  //     → 走 P3.5.108 isSystemConcept 真 subtree 路径 → 显该体系子树 (跟虚拟态视觉一致)
  //     0 弹窗 / 0 橙色提示 — 鸿波 P3.5.111-112 真 catch 都尊重
  //     fail-safe: 网络/IO 失败 → fallback setVirtualSystem (走 P3.5.113 虚拟态)
  const handleCategoryClick = async (e: React.MouseEvent | React.KeyboardEvent) => {
    e.stopPropagation();
    const isSpecialGroup =
      category === "🌟 顶级体系" || category === "未分类";
    if (isSpecialGroup) {
      toggle();
      return;
    }
    // 找 wiki/concepts/<category>.md file — 跟 WikiPreview handleWikilinkClick 同 lookup
    const lower = category.toLowerCase().trim();
    const match = allFiles.find(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower),
    );
    if (match) {
      // P3.5.113: 清旧虚拟态 — 真实system file 走 effectiveRoot 真真实 path** +
      // isSystemConcept → subtree (真实). 不清虚拟会被它优先级压住, 显错位的旧虚拟体系.
      setVirtualSystem(null);
      onSelect(match.rel_path);
      return;
    }
    // P3.5.114: dangling → 自动建真文件 (鸿波 catch "形成真文件才合理")
    try {
      const result = await wikiCreateEntityOrConcept({
        kind: "concept",
        title: category,
        subtype: "system", // P3.5.109 顶级体系标记 → P3.5.108 isSystemConcept 自动判定 subtree
        tags: [],
        related: [], // 顶级体系无上位, 默认空 (用户后续可改)
        body: `# ${category}\n\n(由 catfish 自动建立 — 点 group header 触发)\n\n下属概念自动反推: WikiGraph 走 children related[0] 指向本体系.`,
      });
      // 真建成功**: 刷新 files + 切到新 file → P3.5.108 isSystemConcept → subtree ✓
      setVirtualSystem(null);
      await loadFiles();
      await selectFile(result.rel_path);
    } catch (err) {
      // P3.5.114 fail-safe: 已存在 / 网络失败 → fallback P3.5.113 虚拟态显
      console.warn(`[wiki] auto-create system "${category}" failed: ${err} → fallback virtual`);
      setVirtualSystem(category);
      void selectFile(null);
    }
  };
  if (files.length === 0) return null;
  return (
    <div
      style={{
        margin: "2px 0",
        borderLeft: "2px solid var(--catfish-border)",
        paddingLeft: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "3px 6px",
          fontSize: 11,
          color: category === "未分类" ? "var(--catfish-text-muted)" : "var(--catfish-text)",
          userSelect: "none",
          fontWeight: 500,
        }}
      >
        {/* P3.5.110: caret 单独 button — stopPropagation 后 toggle, 不影响 name 点击 */}
        <span
          role="button"
          tabIndex={0}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开" : "折叠"}
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggle();
            }
          }}
          style={{
            display: "inline-block",
            transition: "transform 0.15s",
            transform: collapsed ? "rotate(0deg)" : "rotate(90deg)",
            fontSize: 9,
            cursor: "pointer",
            padding: "0 2px",
          }}
        >
          ▶
        </span>
        {/* P3.5.110: name 独立 click target — selectFile / 弹建 modal */}
        <span
          role="button"
          tabIndex={0}
          onClick={handleCategoryClick}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              handleCategoryClick(e);
            }
          }}
          title={
            category === "🌟 顶级体系" || category === "未分类"
              ? `点击折叠/展开 (特殊组)`
              : `点击跳到"${category}" 真 preview (dangling 弹 +新建)`
          }
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: "pointer",
            padding: "0 2px",
          }}
        >
          {category}
        </span>
        <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
          {files.length}
        </span>
      </div>
      {!collapsed && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {files.map((f) => {
            const active = selectedPath === f.rel_path;
            return (
              <li key={f.rel_path}>
                <button
                  onClick={() => onSelect(f.rel_path)}
                  title={f.rel_path}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "3px 8px 3px 14px",
                    fontSize: 11,
                    border: "none",
                    background: active ? "var(--catfish-bg-hover, #e8f0ff)" : "transparent",
                    color: active ? "#4a9eff" : "var(--catfish-text)",
                    cursor: "pointer",
                    borderRadius: 4,
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {f.title}
                  {f.subtype && (
                    <span style={{ fontSize: 9, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
                      {f.subtype}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** P3.5.107 A (6/25 鸿波) — concept 二级分组 group: 顶层 "概念 (11)" + 内嵌
 *  按 concept.related[0] 上位体系 真子组 (e.g. "企业资质知识体系 (8) / 🌟 顶级体系 (3)"),
 *  跟 EntityGroup 同款架构, 真支持加任意多新体系 (concept body 写 `[[新体系]]` 自动归类).
 *
 *  localStorage key 跟原 Group "概念 (concepts)" 真兼容 — 用户原折叠态不丢.
 */
function ConceptGroup({
  total,
  categories,
  selectedPath,
  onSelect,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
}) {
  const lsKey = "wiki_group_collapsed_概念 (concepts)"; // 跟原 Group 一致, 不丢用户折叠
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return false; // 默认开 (concept 数量小)
      return v === "1";
    } catch {
      return false;
    }
  });
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  if (total === 0) return null;
  return (
    <div className={"wiki-group " + (collapsed ? "" : "wiki-group--open")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        style={{ color: "#ff9933" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>📐 概念 (concepts)</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {!collapsed && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** P37 (6/5 鸿波) — 全文搜 results list (替 group tree). */
function SearchResults({
  hits,
  searching,
  selectedPath,
  onSelect,
}: {
  hits: import("../../lib/tauri").WikiSearchHit[];
  searching: boolean;
  selectedPath: string | null;
  onSelect: (relPath: string) => Promise<void>;
}) {
  if (searching && hits.length === 0) {
    return (
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, padding: 8 }}>
        🔍 搜索中…
      </div>
    );
  }
  if (hits.length === 0) {
    return (
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, padding: 8 }}>
        没匹配 (title / body / tags 都试过)
      </div>
    );
  }
  return (
    <div style={{ marginTop: "var(--space-2)" }}>
      <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
        🔍 全文搜 — {hits.length} 个结果
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {hits.map((h) => (
          <li
            key={h.rel_path}
            onClick={() => void onSelect(h.rel_path)}
            style={{
              padding: "6px 8px",
              marginBottom: 4,
              cursor: "pointer",
              borderRadius: 4,
              fontSize: 11,
              background:
                selectedPath === h.rel_path ? "var(--catfish-bg-elevated, rgba(0,0,0,0.05))" : "transparent",
              borderLeft: `2px solid ${kindColor(h.kind)}`,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 2 }}>
              {kindEmoji(h.kind)} {h.title}
              <span style={{ marginLeft: 6, fontSize: 9, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
                {h.matched_in.join(" · ")} · {h.score.toFixed(1)}
              </span>
            </div>
            <div
              style={{
                color: "var(--catfish-text-muted)",
                fontSize: 10,
                lineHeight: 1.4,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {h.snippet}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function kindColor(k: string): string {
  return k === "entity" ? "#4a9eff" : k === "concept" ? "#ff9933" : "#5fc878";
}
function kindEmoji(k: string): string {
  return k === "entity" ? "🧑" : k === "concept" ? "📐" : "💬";
}

/** P36 (6/5 鸿波) — 知识体系 tab 空状态 onboarding.
 *
 *  第一次开 Companion `员工` `wiki/ 空` `不知道怎么生`真. 列 3 路:
 *   1. chat 拖文件 → auto ingest → 24h 后 distill 生 entity/concept (P16)
 *   2. chat 聊天 → bg distill → 自动生 (P0/P1.1)
 *   3. + 新建 entity/concept → 手建 (P3.3.8, button 已在顶部)
 */
function EmptyOnboarding({ onCreateClick }: { onCreateClick: () => void }) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "var(--space-4)",
        background: "var(--catfish-bg-elevated, rgba(0,0,0,0.03))",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        fontSize: 12,
        lineHeight: 1.6,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 10, fontSize: 13 }}>
        🐟 知识体系还是空的
      </div>
      <div style={{ color: "var(--catfish-text-muted)", marginBottom: 14 }}>
        3 种方式开始累积:
      </div>

      <Step n={1} title="拖文件进 chat" body={
        <>
          PDF / Word / Excel / 文本拖进 💬 对话框, 小鲶自动保存全文到 <code style={ocode}>~/.catfish/wiki/raw/sources/</code>, 24 小时内 distill 生成 entity / concept. 适合资料 / 文档入库.
        </>
      } />

      <Step n={2} title="跟小鲶聊" body={
        <>
          chat 时小鲶后台记日记 (<code style={ocode}>employee_journal.md</code>). 想沉淀对话进 wiki, 在消息右下角点 💾 存 wiki 手动存.
        </>
      } />

      <Step n={3} title="手动新建" body={
        <>
          <button
            onClick={onCreateClick}
            style={{
              background: "var(--catfish-teal, #0d9488)",
              border: "none",
              color: "#fff",
              borderRadius: 4,
              padding: "3px 10px",
              fontSize: 11,
              cursor: "pointer",
              marginRight: 6,
              fontWeight: 500,
            }}
          >
            + 新建
          </button>
          点这或顶部 + 新建 按钮, 直接建 entity / concept, 自己填 markdown body. 适合已知想立刻沉淀的知识点.
        </>
      } />

      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px dashed var(--catfish-border)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
        }}
      >
        💡 wiki 文件存在 <code style={ocode}>~/.catfish/wiki/</code>, 也能用
        <strong> Obsidian </strong> 直接打开当 vault. wikilink <code style={ocode}>[[name]]</code> 互联.
      </div>
    </div>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
      <div
        style={{
          flexShrink: 0,
          width: 22,
          height: 22,
          borderRadius: "50%",
          background: "var(--catfish-teal, #0d9488)",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {n}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
        <div style={{ color: "var(--catfish-text-muted)" }}>{body}</div>
      </div>
    </div>
  );
}

const ocode: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  background: "rgba(0,0,0,0.06)",
  padding: "1px 4px",
  borderRadius: 2,
};
