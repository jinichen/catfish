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

  return (
    <div style={{ padding: "var(--space-3)", fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>知识体系</h3>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => setShowCreate(true)}
            title="新建 entity / concept"
            style={{
              background: "var(--catfish-accent, #4a9eff)",
              border: "none",
              borderRadius: 4,
              padding: "2px 10px",
              fontSize: 11,
              color: "#fff",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            + 新建
          </button>
          <button
            onClick={() => void loadFiles()}
            title="刷新"
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "2px 8px",
              fontSize: 11,
              cursor: "pointer",
            }}
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
        placeholder="搜索 title / slug..."
        style={{
          width: "100%",
          padding: "6px 8px",
          fontSize: 12,
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          marginBottom: "var(--space-2)",
          background: "var(--catfish-bg)",
          color: "var(--catfish-text)",
        }}
      />

      <div style={{ display: "flex", gap: 4, marginBottom: "var(--space-2)" }}>
        {(["all", "entity", "concept", "query"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setKindFilter(k)}
            style={{
              flex: 1,
              padding: "4px 6px",
              fontSize: 11,
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: kindFilter === k ? "var(--catfish-accent, #4a9eff)" : "transparent",
              color: kindFilter === k ? "#fff" : "var(--catfish-text)",
              cursor: "pointer",
            }}
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
        <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
          wiki/ 目录为空. 点 chat 真 💾 存 wiki, 等下次 distill 跑生成 entity/concept.
        </div>
      )}

      <Group label="实体 (entities)" emoji="🧑" color="#4a9eff" files={grouped.entity} selectedPath={selectedPath} onSelect={selectFile} />
      <Group label="概念 (concepts)" emoji="📐" color="#ff9933" files={grouped.concept} selectedPath={selectedPath} onSelect={selectFile} />
      <Group label="查询 (queries)" emoji="💬" color="#5fc878" files={grouped.query} selectedPath={selectedPath} onSelect={selectFile} />
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
  if (files.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--space-3)" }}>
      <div style={{ fontWeight: 600, fontSize: 11, color, marginBottom: 4 }}>
        {emoji} {label} <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)" }}>({files.length})</span>
      </div>
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
    </div>
  );
}
