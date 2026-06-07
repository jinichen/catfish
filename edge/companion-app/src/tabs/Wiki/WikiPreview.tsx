/** BL-CATFISH-WIKI-MODE P3.3.4 (6/4) — markdown preview + wikilink clickable.
 *
 * react-markdown + remark-gfm render body, [[wikilink]] regex 转 clickable button →
 *   点击 → find target file (按 slug / title match) → store.selectFile.
 * 顶部 frontmatter metadata 块 (title / type / tags / related / sources / mtime).
 *
 * E5 (6/6 taste-skill 改造): 走 className `.wiki-preview*` / `.wiki-related*`.
 * 7 类 anti-pattern 修法见 globals.css 顶 `.wiki-preview` block 注释.
 */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWikiStore } from "../../store/wiki";
import { topKRelated } from "../../lib/wikiRelevance";
import { wikiUpdateFile } from "../../lib/tauri";

const KIND_LABEL: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  query: "查询",
};

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
  // merge 自动生成 section), 拆 main body + history section. UI 默认 collapsed.
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

  // wikilink → 替换 custom html marker, react-markdown 透传
  const rendered = useMemo(() => {
    if (!selectedFile) return "";
    // [[name]] → special token <wikilink:name>, ReactMarkdown components.a hook 或 custom regex 处理
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
    // 找 title / slug match file
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
      // dangling wikilink — 红色 alert
      // P3.3.10 broken link 检测 正式 ship 后这里 toast
      console.warn(`[wiki] dangling wikilink: [[${name}]] (没找到匹配 file)`);
    }
  }

  if (selectedLoading) {
    return <div style={{ color: "var(--catfish-text-muted)", padding: 20 }}>加载中…</div>;
  }
  if (selectedError) {
    return (
      <div style={{ color: "var(--status-err)", padding: 20 }}>
        ✗ {selectedError}
      </div>
    );
  }
  if (!selectedFile) {
    return (
      <div className="wiki-preview__empty">
        <div className="wiki-preview__empty-icon">📄</div>
        <div className="wiki-preview__empty-title">左侧选 wiki 文件</div>
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

  const kind = info.kind;
  const kindLabel = KIND_LABEL[kind] || kind;

  return (
    <div className="wiki-preview">
      {/* metadata header — elevation shadow 替 1px border, kind badge 替 emoji */}
      <div className="wiki-preview__meta">
        <div className="wiki-preview__meta-title">
          <span className={`wiki-kind-badge wiki-kind-badge--${kind}`}>{kindLabel}</span>
          {info.title}
        </div>
        <div className="wiki-preview__meta-row">
          <span>类型 {info.subtype || info.kind}</span>
          <span>
            路径 <code>{info.rel_path}</code>
          </span>
          <span>{(info.size_bytes / 1024).toFixed(1)}KB</span>
        </div>
        {info.tags.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">标签</span>
            {info.tags.map((t) => (
              <span key={t} className="wiki-preview__tag">
                #{t}
              </span>
            ))}
          </div>
        )}
        {info.related.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">
              相关 ({info.related.length})
            </span>
            {info.related.map((r, i) => {
              const dangling = isDangling(r);
              return (
                <button
                  key={i}
                  className={
                    "wiki-preview__wikilink" +
                    (dangling ? " wiki-preview__wikilink--dangling" : "")
                  }
                  onClick={() => handleWikilinkClick(r)}
                  title={dangling ? "找不到 file (dangling link)" : "跳转到 " + r}
                >
                  [[{r}]]
                </button>
              );
            })}
          </div>
        )}
        {info.sources.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">来源</span>
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
              {info.sources.join(", ")}
            </span>
          </div>
        )}
      </div>

      {/* P3.2 4 信号 相关推荐 — 渲染 in body 前, 先 build top-K */}
      <RelatedRecommend info={info} />

      {/* P35 (6/5): 编辑 toggle + action bar — 复用 banner btn 系列 */}
      <div className="wiki-preview__actions">
        {!editing && (
          <button
            className="approval-banner__btn-link"
            onClick={startEdit}
            title="编辑 body markdown"
          >
            编辑
          </button>
        )}
        {editing && (
          <>
            <span className="wiki-preview__edit-hint">
              frontmatter 不动, 只改 body
            </span>
            <button
              className="approval-banner__btn-primary"
              onClick={saveEdit}
              disabled={saving}
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              className="approval-banner__btn-link"
              onClick={cancelEdit}
              disabled={saving}
            >
              取消
            </button>
          </>
        )}
      </div>

      {saveErr && (
        <div className="wiki-preview__save-err">保存失败: {saveErr}</div>
      )}

      {/* P35 (6/5): editing → textarea; 否则 → markdown */}
      {editing ? (
        <textarea
          className="wiki-preview__textarea"
          value={draftBody}
          onChange={(e) => setDraftBody(e.target.value)}
          spellCheck={false}
        />
      ) : (
        /* markdown body */
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith("catfish-wikilink://")) {
                const name = decodeURIComponent(
                  href.slice("catfish-wikilink://".length),
                );
                const dangling = isDangling(name);
                return (
                  <button
                    className={
                      "wiki-preview__wikilink" +
                      (dangling ? " wiki-preview__wikilink--dangling" : "")
                    }
                    onClick={() => handleWikilinkClick(name)}
                    title={dangling ? "dangling link" : "跳转"}
                  >
                    {children}
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

      {/* P39 (6/5 鸿波): 变更历史 collapsible — P19 LLM merge 自动生 ## 变更历史 段.
       *  E5 改造: 走 caret rotate (跟 .wiki-group 一致), 不再 emoji ▶/▼ swap. */}
      {!editing && historyBody && (
        <div
          className={
            "wiki-preview__history" +
            (showHistory ? " wiki-preview__history--open" : "")
          }
        >
          <button
            className="wiki-preview__history-toggle"
            onClick={() => setShowHistory(!showHistory)}
            aria-expanded={showHistory}
          >
            <span className="wiki-preview__history-caret">▶</span>
            变更历史
            <span className="wiki-preview__history-hint">
              (LLM merge 自动记录)
            </span>
          </button>
          {showHistory && (
            <div className="wiki-preview__history-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {historyBody}
              </ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RelatedRecommend({
  info,
}: {
  info: import("../../lib/tauri").WikiFileInfo;
}) {
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);

  const recommends = useMemo(() => topKRelated(info, files, 5), [info, files]);

  if (recommends.length === 0) return null;

  return (
    <div className="wiki-related">
      <div className="wiki-related__header">相关推荐 · 4 信号 ranked</div>
      <div className="wiki-related__sub">
        direct link (×3) · source overlap (×4) · Adamic-Adar (×1.5) · type affinity (×1)
      </div>
      <ul className="wiki-related__list">
        {recommends.map(({ file, breakdown }) => {
          const signals: string[] = [];
          if (breakdown.direct > 0) signals.push("↔");
          if (breakdown.sourceOverlap > 0) signals.push("◇");
          if (breakdown.adamicAdar > 0) signals.push("∗");
          if (breakdown.typeAffinity > 0) signals.push("≈");
          const fileKind = file.kind;
          const fileKindLabel = KIND_LABEL[fileKind] || fileKind;
          return (
            <li key={file.rel_path} className="wiki-related__item">
              <button
                className="wiki-related__link"
                onClick={() => void selectFile(file.rel_path)}
              >
                <span
                  className={`wiki-kind-badge wiki-kind-badge--${fileKind}`}
                >
                  {fileKindLabel}
                </span>
                {file.title}
              </button>
              <span className="wiki-related__score">
                score {breakdown.total.toFixed(2)} {signals.join(" ")}
                {breakdown.direct > 0 && ` · 直链`}
                {breakdown.sourceOverlap > 0 &&
                  ` · 共源 ${breakdown.sourceOverlap.toFixed(1)}`}
                {breakdown.adamicAdar > 0 &&
                  ` · 共邻 ${breakdown.adamicAdar.toFixed(1)}`}
                {breakdown.typeAffinity > 0 && ` · 同型`}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
