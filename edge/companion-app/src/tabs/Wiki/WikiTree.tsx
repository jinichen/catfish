/** BL-CATFISH-WIKI-MODE P3.3.3 (6/4) — tree view 实际渲染.
 *
 * 3 group: entities / concepts / queries — 列 wiki_list_files 真返回 file.
 * search box (filename + title 模糊 match) + kind filter (all / entity / concept / query).
 * 点击 file → store.selectFile(rel_path) → 中列 preview 加载.
 */

import { useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise,
  MagnifyingGlass,
  Plus,
  SlidersHorizontal,
} from "@phosphor-icons/react";
import { useWikiStore } from "../../store/wiki";
import {
  wikiSearchHybrid,
  wikiGraphStatus,
  wikiSearchSemantic,
  wikiSearchText,
  type WikiFileInfo,
  type WikiGraphStatus,
  type WikiSearchHit,
} from "../../lib/tauri";
import { resolveWikiRef, resolveWikiRefOrNull } from "../../lib/wikiResolve";
import WikiCreateModal from "./WikiCreateModal";

// 8/15: WikiTree.tsx 原本 1215 行, 过了 CLAUDE.md §1 的 800 红线。下面三块是
// 从本文件搬出去的**同一批组件**, 不是新东西:
//   · Groups          分组渲染 (Group / EntityGroup / CategorySubgroup / ConceptGroup)
//   · SearchResults   搜索态下整个替掉 group tree 的那条分支
//   · EmptyOnboarding 空状态引导
// 它们只被本文件用, 不算对外 API。CategorySubgroup / kindColor / kindEmoji /
// Step / ocode 没有 export —— 它们各自只在所属文件内部用, 露出来只会让人以为
// 可以到处引。
import { ConceptGroup, EntityGroup, Group } from "./WikiTreeGroups";
import { CONCEPT_TYPE_ORDER, ENTITY_TYPE_ORDER, groupByType } from "./wikiLabels";
import { SearchResults } from "./WikiTreeSearchResults";
import { EmptyOnboarding } from "./WikiTreeEmptyOnboarding";

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

  // P37/P38/P39 — 默认智能检索，保留旧模式用于排查和精确搜索。
  const [searchMode, setSearchMode] = useState<"title" | "body" | "semantic" | "hybrid">("hybrid");
  const [searchHits, setSearchHits] = useState<WikiSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [semanticMessage, setSemanticMessage] = useState<string>("");
  const [graphStatus, setGraphStatus] = useState<WikiGraphStatus | null>(null);

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
        if (searchMode === "hybrid") {
          const hits = await wikiSearchHybrid(search, 20);
          setSearchHits(hits);
          setSemanticMessage("");
        } else if (searchMode === "body") {
          const hits = await wikiSearchText(search);
          setSearchHits(hits);
        } else {
          // semantic
          const res = await wikiSearchSemantic(search);
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
                matched_in: h.matched_in?.length ? h.matched_in : ["semantic"],
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
    }, searchMode === "semantic" || searchMode === "hybrid" ? 500 : 300);
    return () => clearTimeout(handle);
  }, [search, searchMode]);

  // P17 (6/5 鸿波): 切到知识体系 tab 立即 reload (App.tsx 真`activeTab === 'wiki'
  // && <WikiTab/>` 真 conditional render — 切走 unmount, 切回 mount 跑这 effect).
  // 不用 cache expiry — 鸿波要求即刻刷新, 防 LLM 外部 write_file 后 list 老.
  useEffect(() => {
    if (!filesLoading) {
      void loadFiles();
    }
    let cancelled = false;
    void wikiGraphStatus()
      .then((status) => {
        if (!cancelled) setGraphStatus(status);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!searchHits.length) return;
    let cancelled = false;
    void wikiGraphStatus()
      .then((status) => {
        if (!cancelled) setGraphStatus(status);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [searchHits.length]);

  // 真 inbound link count per file (dangling/orphan 用)
  const inboundMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of files) {
      for (const r of f.related) {
        // 与 WikiGraph/体检共用唯一解析规则；不能再按数组顺序取第一个子串命中。
        const target = resolveWikiRefOrNull(r.name, files);
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
        const result = resolveWikiRef(r.name, files);
        if (result.kind !== "hit") dangling.push(r.name);
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

  // 8/3 鸿波"根本就无效": 搜索/筛选生效时强制展开所有分组。
  //
  // 老行为是搜索完全不管折叠态 —— collapsed 只来自 localStorage 和手动点击,
  // 没有任何地方跟 search 挂钩。而实体组**默认就是折叠的** (下面 484 行:
  // localStorage 为空时 label.includes("实体") → true), 二级子组也默认折
  // (CategorySubgroup 里 v === null → true)。
  //
  // 于是: 员工搜一个词, filtered 明明命中了, 组标题的计数也变了, 但组是折的,
  // 屏幕上一条都不显示 —— 看起来就是"搜不到 / 根本没入库"。
  //
  // 这个坑对**新建的条目**格外狠: 新条目没有 related, 落进"未分类"子组, 而
  // 未分类被排到最后 (entityCategories 的 sort)。折叠的组 + 最后一个子组 +
  // 子组也折叠 = UI 上最难被发现的位置。8/3 那份对标矩阵反复"看不到", 这是
  // 最可能的直接原因。
  //
  // 主动筛选时展开是安全的: 员工正在找东西, 这时候藏结果没有任何道理; 手动
  // 折叠的偏好仍存在 localStorage 里, 清掉搜索就恢复。
  const isFiltering =
    !!search.trim() || kindFilter !== "all" || query !== "none";

  // 9/24 (鸿波"左侧也应该优化"): 对象、主题都按类型分组。
  //
  // 以前对象按 related[0] (第一条关系的名字) 分组, 主题只分"知识体系 / 其他主题"两堆。
  // related[0] 只是写入时碰巧排第一的那条关系, 于是左侧冒出「资质对标分析流程 2」
  // 「ISO 20000… 1」这类组, 一半是单条组; 部门、人员、证书又混在「中电福富…」一个大组里。
  // 类型 (entity_type / concept_type) 是写入侧受控词表管着的字段, 用它分组稳定可预期。
  // 组内按标题排序; 没填类型的放「未分类」排最后。
  const entityCategories = useMemo(
    () => groupByType(grouped.entity, ENTITY_TYPE_ORDER),
    [grouped.entity],
  );
  const conceptCategories = useMemo(
    () => groupByType(grouped.concept, CONCEPT_TYPE_ORDER),
    [grouped.concept],
  );

  // E4 (6/6 taste-skill 改造): 走 globals.css `.wiki-*` class.
  // 主要改: hardcoded `#0d9488` `#4a9eff` 非 brand 色统一到 brand 墨青;
  // mode + kind toggle 改 pill segmented control; 去 emoji; 加 hover/focus.
  return (
    <div className="wiki-tree">
      <div className="wiki-tree__title">
        <h3>知识体系</h3>
        <div className="wiki-tree__actions">
          <button
            onClick={() => openCreateModal()}
            title="新建知识"
            aria-label="新建知识"
            className="wiki-tree__primary-action"
          >
            <Plus size={17} aria-hidden="true" />新建
          </button>
          <button
            onClick={() => void loadFiles()}
            title="刷新"
            aria-label="刷新知识库"
            className="wiki-tree__icon-action"
          >
            <ArrowClockwise size={18} aria-hidden="true" />
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

      <label className="wiki-tree__search">
        <MagnifyingGlass size={20} aria-hidden="true" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索知识名称或内容"
          aria-label="搜索知识"
        />
      </label>

      <div className="wiki-segmented wiki-segmented--kinds" aria-label="知识类型">
        {(["all", "entity", "concept", "query"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setKindFilter(k)}
            className="wiki-segmented__btn"
            data-active={kindFilter === k}
          >
            {k === "all" ? "全部" : k === "entity" ? "对象" : k === "concept" ? "主题" : "记录"}
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

      {graphStatus && (
        <details className="wiki-tree__graph-status">
          <summary>
            关系图 {graphStatus.nodeCount} 节点 · {graphStatus.edgeCount} 条关系
            {graphStatus.unresolvedCount > 0 && ` · ${graphStatus.unresolvedCount} 条关系待确认`}
          </summary>
          <div className="wiki-tree__graph-status-body">
            {graphStatus.unresolvedCount > 0 && (
              <div>
                这些关系暂未连入图谱：目标不存在或名称有歧义，确认后才会建立连接。
              </div>
            )}
            <div>
              同步：{graphStatus.syncMode} · {graphStatus.changedFiles} 个文件更新 · 最近：
              {graphStatus.lastSyncAt
                ? new Date(graphStatus.lastSyncAt).toLocaleString()
                : "未同步"}
            </div>
            {graphStatus.unresolvedSamples.map((item) => (
              <div
                className="wiki-tree__graph-status-item"
                key={`${item.sourcePath}:${item.sourceName}`}
              >
                {item.sourcePath} → {item.sourceName}（
                {item.reason === "ambiguous" ? "名称有歧义" : "未找到目标"}）
              </div>
            ))}
          </div>
        </details>
      )}

      <details className="wiki-tree__advanced">
        <summary><SlidersHorizontal size={16} aria-hidden="true" />高级搜索与筛选</summary>
        <div className="wiki-tree__advanced-body">
          <div className="wiki-tree__advanced-label">搜索方式</div>
          <div className="wiki-segmented">
            {(["hybrid", "title", "body", "semantic"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setSearchMode(m)}
                className="wiki-segmented__btn"
                data-active={searchMode === m}
              >
                {m === "hybrid" ? "智能" : m === "title" ? "标题" : m === "body" ? "全文" : "语义"}
              </button>
            ))}
          </div>
          <label className="wiki-tree__advanced-label" htmlFor="wiki-quick-filter">快速筛选</label>
          <select
            id="wiki-quick-filter"
            value={query}
            onChange={(e) => {
              const v = e.target.value as typeof query;
              setQuery(v);
              if (v !== "top-tag") setSelectedTag(null);
            }}
            className="wiki-tree__quick-filter"
          >
            <option value="none">不使用额外筛选</option>
            <option value="recent-week">最近一周新增</option>
            <option value="orphan-concept">尚未建立关系的主题</option>
            <option value="dangling">包含失效关系的条目</option>
            <option value="top-tag">按标签筛选</option>
          </select>

          {query === "top-tag" && topTags.length > 0 && (
            <div className="wiki-tree__tags">
              {topTags.map(([tag, count]) => (
                <button
                  key={tag}
                  onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                  data-active={selectedTag === tag}
                >
                  {tag}（{count}）
                </button>
              ))}
            </div>
          )}
        </div>
      </details>

      {filesLoading && <div style={{ color: "var(--catfish-text-muted)" }}>加载中...</div>}
      {filesError && (
        <div style={{ color: "var(--status-err)", fontSize: 13 }}>
          ✗ {filesError}
        </div>
      )}

      {!filesLoading && files.length === 0 && !filesError && (
        <EmptyOnboarding onCreateClick={() => openCreateModal()} />
      )}

      {/* P37/P38/P39: body/semantic/hybrid 模式 → hits 列表替 group tree */}
      {searchMode !== "title" && search.trim() && (
        <SearchResults
          hits={searchHits}
          searching={searching}
          mode={searchMode}
          selectedPath={selectedPath}
          onSelect={selectFile}
        />
      )}

      {!(searchMode !== "title" && search.trim()) && (
        <>
          {/* 9/24: 对象 / 主题都按类型二级分组 (见 entityCategories 注释) */}
          <EntityGroup
            total={grouped.entity.length}
            categories={entityCategories}
            forceOpen={isFiltering}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          <ConceptGroup
            total={grouped.concept.length}
            categories={conceptCategories}
            forceOpen={isFiltering}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          <Group label="记录" files={grouped.query} selectedPath={selectedPath} onSelect={selectFile} forceOpen={isFiltering} />
          {/* P3.3.18 Phase 4 (6/10): 已装部门 wiki — read-only, 跟个人 wiki 视觉分离 */}
          {sharedFiles.length > 0 && (
            <Group
              label="部门知识（只读）"
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
                  aliases: [],
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
