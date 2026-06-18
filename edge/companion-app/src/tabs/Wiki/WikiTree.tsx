/** BL-CATFISH-WIKI-MODE P3.3.3 (6/4) — tree view 实际渲染.
 *
 * 3 group: entities / concepts / queries — 列 wiki_list_files 真返回 file.
 * search box (filename + title 模糊 match) + kind filter (all / entity / concept / query).
 * 点击 file → store.selectFile(rel_path) → 中列 preview 加载.
 */

import { useEffect, useMemo, useState } from "react";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";
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
  const [showCreate, setShowCreate] = useState(false);

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
    }, searchMode === "semantic" ? 500 : 300);   // semantic 真**`后台 indexer 慢, 500ms debounce**真
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
        // 真**真**真**name match 真**真**真**target file 真 title / slug**真
        const lower = r.toLowerCase();
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

  // dangling wikilinks (file 真**真**related 含真 target 找不到)
  const danglingMap = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const f of files) {
      const dangling: string[] = [];
      for (const r of f.related) {
        const lower = r.toLowerCase();
        const found = files.some(
          (x) =>
            x.title.toLowerCase() === lower ||
            x.slug.toLowerCase() === lower ||
            x.title.toLowerCase().includes(lower)
        );
        if (!found) dangling.push(r);
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

  // E4 (6/6 taste-skill 改造): 走 globals.css `.wiki-*` class.
  // 主要改: hardcoded `#0d9488` `#4a9eff` 非 brand 色统一到 brand 墨青;
  // mode + kind toggle 改 pill segmented control; 去 emoji; 加 hover/focus.
  return (
    <div style={{ padding: "var(--space-3)", fontSize: 12 }}>
      <div className="wiki-tree__title">
        <h3>知识体系</h3>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setShowCreate(true)}
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

      {showCreate && <WikiCreateModal onClose={() => setShowCreate(false)} />}

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
        <EmptyOnboarding onCreateClick={() => setShowCreate(true)} />
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
          <Group label="实体 (entities)" emoji="🧑" color="#4a9eff" files={grouped.entity} selectedPath={selectedPath} onSelect={selectFile} />
          <Group label="概念 (concepts)" emoji="📐" color="#ff9933" files={grouped.concept} selectedPath={selectedPath} onSelect={selectFile} />
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
 *  第一次开 Companion 真**`员工`** 真**`wiki/ 空`** 真**`不知道怎么生**`真. 列 3 路:
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
