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
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const selectFile = useWikiStore((s) => s.selectFile);
  const setSearch = useWikiStore((s) => s.setSearch);
  const setKindFilter = useWikiStore((s) => s.setKindFilter);
  const [showCreate, setShowCreate] = useState(false);

  // 进 tab 时 load files
  useEffect(() => {
    if (files.length === 0 && !filesLoading) {
      void loadFiles();
    }
  }, [files.length, filesLoading, loadFiles]);

  // filter + search
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return files.filter((f) => {
      if (kindFilter !== "all" && f.kind !== kindFilter) return false;
      if (q) {
        const hay = (f.title + " " + f.slug).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [files, search, kindFilter]);

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

      <div style={{ display: "flex", gap: 4, marginBottom: "var(--space-3)" }}>
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
