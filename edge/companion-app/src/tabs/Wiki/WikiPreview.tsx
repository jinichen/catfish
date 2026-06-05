/** BL-CATFISH-WIKI-MODE P3.3.4 (6/4) — markdown preview + wikilink clickable.
 *
 * react-markdown + remark-gfm render body, [[wikilink]] regex 转 clickable button →
 *   点击 → find target file (按 slug / title match) → store.selectFile.
 * 顶部 frontmatter metadata 块 (title / type / tags / related / sources / mtime).
 */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWikiStore } from "../../store/wiki";
import { topKRelated } from "../../lib/wikiRelevance";
import { wikiUpdateFile } from "../../lib/tauri";

export default function WikiPreview() {
  const selectedFile = useWikiStore((s) => s.selectedFile);
  const selectedLoading = useWikiStore((s) => s.selectedLoading);
  const selectedError = useWikiStore((s) => s.selectedError);
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);
  const loadFiles = useWikiStore((s) => s.loadFiles);

  // P35 (6/5 鸿波): inline 编辑器 state. editing=true 时 body 渲染 textarea.
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // selectedFile 切换时 reset edit state
  useEffect(() => {
    setEditing(false);
    setSaveErr(null);
    if (selectedFile) {
      setDraftBody(selectedFile.body);
    }
  }, [selectedFile?.info.rel_path]);

  const startEdit = () => {
    if (!selectedFile) return;
    setDraftBody(selectedFile.body);
    setEditing(true);
    setSaveErr(null);
  };

  const cancelEdit = () => {
    setEditing(false);
    setSaveErr(null);
  };

  const saveEdit = async () => {
    if (!selectedFile) return;
    setSaving(true);
    setSaveErr(null);
    try {
      // 拼 full content: --- frontmatter --- + body
      const fm = selectedFile.frontmatter.trim();
      const content = `---\n${fm}\n---\n\n${draftBody}\n`;
      await wikiUpdateFile(selectedFile.info.rel_path, content);
      await loadFiles();
      await selectFile(selectedFile.info.rel_path); // 重 fetch 看新 body
      setEditing(false);
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  // P39 (6/5 鸿波): 变更历史 collapsible — 检测 body 末尾 "## 变更历史" 段 (P19 LLM
  // merge 真自动生成 真**`section**`), 拆 main body + history section. UI 默认 collapsed.
  const { mainBody, historyBody } = useMemo(() => {
    if (!selectedFile) return { mainBody: "", historyBody: "" };
    const body = selectedFile.body;
    // 匹配 "## 变更历史" 或 "## 变更日志" / "## Changelog" — 兼容 LLM 生成的不同标题
    const re = /^(##\s+(?:变更历史|变更日志|更新历史|Changelog|Change Log)\s*)$/im;
    const match = body.match(re);
    if (!match || match.index === undefined) {
      return { mainBody: body, historyBody: "" };
    }
    return {
      mainBody: body.slice(0, match.index).trimEnd(),
      historyBody: body.slice(match.index),
    };
  }, [selectedFile]);

  // wikilink → 替换 真**custom html marker**, react-markdown 透传
  const rendered = useMemo(() => {
    if (!selectedFile) return "";
    // [[name]] → 真**special token <wikilink:name>**真**真**真 ReactMarkdown 真**真**components.a 真**hook**真**或** custom regex 处理
    return mainBody.replace(
      /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
      (_match, name, alias) => {
        const display = alias || name;
        return `[${display}](catfish-wikilink://${encodeURIComponent(name)})`;
      }
    );
  }, [mainBody, selectedFile]);

  const [showHistory, setShowHistory] = useState(false);

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

      {/* P35 (6/5): 编辑 toggle + action bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginBottom: 8,
          alignItems: "center",
        }}
      >
        {!editing && (
          <button
            onClick={startEdit}
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              color: "var(--catfish-text)",
              borderRadius: 4,
              padding: "4px 12px",
              fontSize: 12,
              cursor: "pointer",
            }}
            title="编辑 body markdown"
          >
            ✏️ 编辑
          </button>
        )}
        {editing && (
          <>
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginRight: 8 }}>
              ⚠️ frontmatter 不动, 改 body
            </span>
            <button
              onClick={saveEdit}
              disabled={saving}
              style={{
                background: "var(--catfish-teal, #0d9488)",
                border: "1px solid var(--catfish-teal, #0d9488)",
                color: "#fff",
                borderRadius: 4,
                padding: "4px 14px",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              {saving ? "保存中…" : "💾 保存"}
            </button>
            <button
              onClick={cancelEdit}
              disabled={saving}
              style={{
                background: "transparent",
                border: "1px solid var(--catfish-border)",
                color: "var(--catfish-text)",
                borderRadius: 4,
                padding: "4px 12px",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              取消
            </button>
          </>
        )}
      </div>

      {saveErr && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          ✗ 保存失败: {saveErr}
        </div>
      )}

      {/* P35 (6/5): editing → textarea; 否则 → markdown */}
      {editing ? (
        <textarea
          value={draftBody}
          onChange={(e) => setDraftBody(e.target.value)}
          spellCheck={false}
          style={{
            width: "100%",
            minHeight: 400,
            padding: 12,
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            lineHeight: 1.6,
            border: "1px solid var(--catfish-border)",
            borderRadius: 6,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            boxSizing: "border-box",
            resize: "vertical",
          }}
        />
      ) : (
      /* markdown body */
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
      )}

      {/* P39 (6/5 鸿波): 变更历史 collapsible — P19 LLM merge 真生 真 ## 变更历史 段. */}
      {!editing && historyBody && (
        <div style={{ marginTop: 16, borderTop: "1px dashed var(--catfish-border)", paddingTop: 12 }}>
          <button
            onClick={() => setShowHistory(!showHistory)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--catfish-text-muted)",
              fontSize: 12,
              cursor: "pointer",
              padding: 0,
              fontWeight: 500,
            }}
          >
            {showHistory ? "▼" : "▶"} 📜 变更历史
            <span style={{ marginLeft: 6, fontSize: 10, opacity: 0.7 }}>
              (LLM merge 真自动记录)
            </span>
          </button>
          {showHistory && (
            <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{historyBody}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
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
