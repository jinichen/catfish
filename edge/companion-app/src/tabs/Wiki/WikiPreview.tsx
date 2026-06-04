/** BL-CATFISH-WIKI-MODE P3.3.4 (6/4) — markdown preview + wikilink clickable.
 *
 * react-markdown + remark-gfm render body, [[wikilink]] regex 转 clickable button →
 *   点击 → find target file (按 slug / title match) → store.selectFile.
 * 顶部 frontmatter metadata 块 (title / type / tags / related / sources / mtime).
 */

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWikiStore } from "../../store/wiki";
import { topKRelated } from "../../lib/wikiRelevance";

export default function WikiPreview() {
  const selectedFile = useWikiStore((s) => s.selectedFile);
  const selectedLoading = useWikiStore((s) => s.selectedLoading);
  const selectedError = useWikiStore((s) => s.selectedError);
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);

  // wikilink → 替换 真**custom html marker**, react-markdown 透传
  const rendered = useMemo(() => {
    if (!selectedFile) return "";
    // [[name]] → 真**special token <wikilink:name>**真**真**真 ReactMarkdown 真**真**components.a 真**hook**真**或** custom regex 处理
    return selectedFile.body.replace(
      /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
      (_match, name, alias) => {
        const display = alias || name;
        return `[${display}](catfish-wikilink://${encodeURIComponent(name)})`;
      }
    );
  }, [selectedFile]);

  function handleWikilinkClick(name: string) {
    // 找 title / slug match 真 file
    const lower = name.toLowerCase().trim();
    const match = files.find(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower)
    );
    if (match) {
      void selectFile(match.rel_path);
    } else {
      // dangling wikilink — 真**真**红色 alert**真
      // P3.3.10 真**broken link 检测** 真**正式 ship 后这里 toast**
      console.warn(`[wiki] dangling wikilink: [[${name}]] (没找到匹配 file)`);
    }
  }

  if (selectedLoading) {
    return <div style={{ color: "var(--catfish-text-muted)" }}>加载中...</div>;
  }
  if (selectedError) {
    return <div style={{ color: "var(--status-err)" }}>✗ {selectedError}</div>;
  }
  if (!selectedFile) {
    return (
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 13, textAlign: "center", marginTop: 80 }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>📄</div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>左边选 wiki 文件</div>
        <div>entity / concept / query 都行</div>
      </div>
    );
  }

  const info = selectedFile.info;
  const isDangling = (name: string) => {
    const lower = name.toLowerCase().trim();
    return !files.some(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower)
    );
  };

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", fontSize: 14, lineHeight: 1.65 }}>
      {/* metadata header */}
      <div
        style={{
          padding: "var(--space-3) var(--space-4)",
          background: "var(--catfish-bg-elevated)",
          borderRadius: 8,
          marginBottom: "var(--space-4)",
          border: "1px solid var(--catfish-border)",
          fontSize: 12,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 6 }}>
          {info.kind === "entity" ? "🧑" : info.kind === "concept" ? "📐" : "💬"}{" "}
          {info.title}
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", color: "var(--catfish-text-muted)" }}>
          <span>类型: {info.subtype || info.kind}</span>
          <span>路径: <code style={{ fontSize: 11 }}>{info.rel_path}</code></span>
          <span>{(info.size_bytes / 1024).toFixed(1)}KB</span>
        </div>
        {info.tags.length > 0 && (
          <div style={{ marginTop: 6 }}>
            标签:{" "}
            {info.tags.map((t) => (
              <span
                key={t}
                style={{
                  display: "inline-block",
                  padding: "1px 6px",
                  margin: "0 4px 2px 0",
                  background: "var(--catfish-bg)",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 10,
                  fontSize: 10,
                }}
              >
                #{t}
              </span>
            ))}
          </div>
        )}
        {info.related.length > 0 && (
          <div style={{ marginTop: 6 }}>
            相关 ({info.related.length}):{" "}
            {info.related.map((r, i) => {
              const dangling = isDangling(r);
              return (
                <button
                  key={i}
                  onClick={() => handleWikilinkClick(r)}
                  title={dangling ? "找不到 file (dangling link)" : "跳转到 " + r}
                  style={{
                    border: "none",
                    background: "transparent",
                    color: dangling ? "var(--status-err, #d33)" : "var(--catfish-accent, #4a9eff)",
                    textDecoration: "underline",
                    cursor: "pointer",
                    padding: 0,
                    margin: "0 6px 0 0",
                    fontSize: 12,
                    fontStyle: dangling ? "italic" : "normal",
                  }}
                >
                  [[{r}]]
                  {dangling && " ⚠️"}
                </button>
              );
            })}
          </div>
        )}
        {info.sources.length > 0 && (
          <div style={{ marginTop: 6, color: "var(--catfish-text-muted)" }}>
            来源: {info.sources.join(", ")}
          </div>
        )}
      </div>

      {/* P3.2 4 信号 相关推荐 — 渲染 in body 前, 真**bottom 真**真**先 build top-K** */}
      <RelatedRecommend info={info} />

      {/* markdown body */}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("catfish-wikilink://")) {
              const name = decodeURIComponent(href.slice("catfish-wikilink://".length));
              const dangling = isDangling(name);
              return (
                <button
                  onClick={() => handleWikilinkClick(name)}
                  style={{
                    border: "none",
                    background: "transparent",
                    color: dangling ? "var(--status-err, #d33)" : "var(--catfish-accent, #4a9eff)",
                    textDecoration: "underline",
                    cursor: "pointer",
                    padding: 0,
                    fontStyle: dangling ? "italic" : "normal",
                    fontWeight: 500,
                  }}
                  title={dangling ? "dangling link" : "跳转"}
                >
                  {children}
                  {dangling && " ⚠️"}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {rendered}
      </ReactMarkdown>
    </div>
  );
}

function RelatedRecommend({ info }: { info: import("../../lib/tauri").WikiFileInfo }) {
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);

  const recommends = useMemo(() => topKRelated(info, files, 5), [info, files]);

  if (recommends.length === 0) return null;

  return (
    <div
      style={{
        marginTop: "var(--space-3)",
        marginBottom: "var(--space-4)",
        padding: "var(--space-3)",
        background: "var(--catfish-bg-elevated)",
        borderRadius: 8,
        border: "1px solid var(--catfish-border)",
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
        📊 相关推荐 (4 信号 ranked)
      </div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 8 }}>
        direct link (×3) · source overlap (×4) · Adamic-Adar (×1.5) · type affinity (×1)
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, fontSize: 12 }}>
        {recommends.map(({ file, breakdown }) => {
          const signals: string[] = [];
          if (breakdown.direct > 0) signals.push("↔");
          if (breakdown.sourceOverlap > 0) signals.push("◇");
          if (breakdown.adamicAdar > 0) signals.push("∗");
          if (breakdown.typeAffinity > 0) signals.push("≈");
          return (
            <li key={file.rel_path} style={{ padding: "3px 0" }}>
              <button
                onClick={() => void selectFile(file.rel_path)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--catfish-accent, #4a9eff)",
                  cursor: "pointer",
                  textDecoration: "underline",
                  padding: 0,
                  fontSize: 12,
                }}
              >
                {file.kind === "entity" ? "🧑" : file.kind === "concept" ? "📐" : "💬"}{" "}
                {file.title}
              </button>
              <span style={{ marginLeft: 8, color: "var(--catfish-text-muted)", fontSize: 11 }}>
                score: {breakdown.total.toFixed(2)} {signals.join(" ")}
                {breakdown.direct > 0 && ` · 直链`}
                {breakdown.sourceOverlap > 0 && ` · 共源 ${breakdown.sourceOverlap.toFixed(1)}`}
                {breakdown.adamicAdar > 0 && ` · 共邻 ${breakdown.adamicAdar.toFixed(1)}`}
                {breakdown.typeAffinity > 0 && ` · 同型`}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
