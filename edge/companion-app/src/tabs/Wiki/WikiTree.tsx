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

  // P3.5.182 (7/6 鸿波军规审判): 严格 REMOVE hardcode assumption
  // "concept.related[0] = 上位体系" — 严格 P3.5.107 6/25 加此 assumption 严格
  // = 硬编码 rule form (data 层 hardcode inference — 位置约定当 semantic).
  //
  // 严格军规: enum / example / rule / principle 严格全是硬编码 4 form.
  // 严格 P3.5.176 删 enum, P3.5.180 删 example, P3.5.182 严格严格严格 rule.
  //
  // 严格 root cause 触发场景 (鸿波 7/6 严格 audit "为什么还是市场部"):
  //   LLM 严格写 concepts/组织架构.md 严格无 frontmatter → wiki_read.rs
  //   严格 merge_related_with_body 严格 body 第一 `[[市场部]]` 严格 merge 严格
  //   related[0]="市场部" (是 entity) → 老逻辑 UI 严格误当上位 → 严格生假
  //   "▼ 市场部" 父组. 严格严格 assumption 严格根本失效.
  //
  // 严格新逻辑 (AI-first, 0 hardcode 位置约定):
  //   - concept.subtype === "system" → "🌟 顶级体系" 组 (LLM 自主填 semantic label,
  //     严格不 enum, 严格 concept_type=system 严格 P3.5.176 保留约定)
  //   - 其他 concept → 平铺"概念"组 (严格无上位/下属假设, 严格 body wikilink 保留
  //     给 WikiGraph 走 graph 关联, 严格但 UI 树严格不假当"上位")
  //
  // 严格员工加"政企客户体系"顶级 concept 严格路径 (0 code 改, 语义驱动):
  //   1. 员工 chat 说"存政企客户体系为顶级体系" → LLM 严格生 concept.subtype=system
  //   2. 严格进 "🌟 顶级体系" 组 (severity subtype semantic 严格识别)
  //   3. 层级 3+ 严格靠 WikiGraph 走 body [[wikilink]] 关联可视化, 严格不侵入 UI 树
  const conceptCategories = useMemo(() => {
    const map = new Map<string, WikiFileInfo[]>();
    const TOP_KEY = "🌟 顶级体系";
    const OTHER_KEY = "概念";
    for (const c of grouped.concept) {
      const key = c.subtype === "system" ? TOP_KEY : OTHER_KEY;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(c);
    }
    return Array.from(map.entries()).sort((a, b) => {
      // 顶级体系 推最前 — 根节点先看
      if (a[0] === TOP_KEY) return -1;
      if (b[0] === TOP_KEY) return 1;
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
            forceOpen={isFiltering}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          {/* P3.5.182 (7/6 鸿波军规审判): 概念严格删 related[0]=上位体系 hardcode assumption.
              严格 只按 concept.subtype=system 二分 → "🌟 顶级体系" + "概念" 平铺. */}
          <ConceptGroup
            total={grouped.concept.length}
            categories={conceptCategories}
            forceOpen={isFiltering}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          <Group label="查询 (queries)" emoji="💬" color="#5fc878" files={grouped.query} selectedPath={selectedPath} onSelect={selectFile} forceOpen={isFiltering} />
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
  forceOpen = false,
}: {
  label: string;
  emoji: string;
  color: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
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
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
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
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
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
        aria-expanded={isOpen}
        style={{ color }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>{emoji} {label}</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {files.length}
        </span>
      </div>
      {isOpen && (
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
                {/* 8/4: 员工确认过的打个记号。219/220 条是 LLM 生成的, 在此之前
                    员工完全看不出哪些是没人看过的机器输出。 */}
                {f.authored_by === "employee" && (
                  <span
                    title="你确认过这条 —— 后台蒸馏不会覆盖它的正文"
                    style={{ marginLeft: 4, opacity: 0.7, fontSize: 10 }}
                  >
                    ✎
                  </span>
                )}
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
  forceOpen = false,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
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
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
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
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
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
        aria-expanded={isOpen}
        style={{ color: "#4a9eff" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>🧑 实体 (entities)</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {isOpen && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              forceOpen={forceOpen}
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
  forceOpen = false,
}: {
  category: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
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
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
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
          aria-expanded={isOpen}
          aria-label={isOpen ? "折叠" : "展开"}
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
            transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
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
      {isOpen && (
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
                {/* 8/4: 员工确认过的打个记号。219/220 条是 LLM 生成的, 在此之前
                    员工完全看不出哪些是没人看过的机器输出。 */}
                {f.authored_by === "employee" && (
                  <span
                    title="你确认过这条 —— 后台蒸馏不会覆盖它的正文"
                    style={{ marginLeft: 4, opacity: 0.7, fontSize: 10 }}
                  >
                    ✎
                  </span>
                )}
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

/** P3.5.182 (7/6 鸿波军规审判) — concept 二级分组 group: 顶层 "概念 (N)" + 内嵌
 *  按 concept.subtype 二分: "🌟 顶级体系" (subtype=system) + "概念" (其他, 平铺).
 *
 *  严格 P3.5.107 A (6/25) 老逻辑严格按 related[0] 上位体系分组 → 严格 hardcode
 *  assumption (位置约定当 semantic) 严格军规违反. P3.5.182 严格 删除.
 *
 *  localStorage key 跟原 Group "概念 (concepts)" 真兼容 — 用户原折叠态不丢.
 */
function ConceptGroup({
  total,
  categories,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
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
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
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
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
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
        aria-expanded={isOpen}
        style={{ color: "#ff9933" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>📐 概念 (concepts)</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {isOpen && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              forceOpen={forceOpen}
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
