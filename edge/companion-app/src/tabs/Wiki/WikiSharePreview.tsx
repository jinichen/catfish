/** 分享对话框里「会发出去」的预览 —— 按知识页的样子显示, 不给员工看 YAML (11/09)。
 *
 * 鸿波 11/09:「没做 MD 渲染后预览, 是不是效果不好?」—— 第一版是一块等宽字体的原始文本 (--- 头部 + markdown),
 * 员工看不出发出去长什么样。现在:
 *   · 头部字段一条条列: 类型 / 别名 / 标签 / 关系 / 来源 (改写后的说法)
 *   · 原文依据 (从原件里摘的原话, 常有经营数字) 单独一块, 写明「会一起发出去」—— 以前是默默跟着发的
 *   · 正文跟知识页同一套渲染 (react-markdown + remark-gfm, .wiki-doc 排版); [[链接]] 按同事那边点不点得开上色
 *   · 原始文本收进一个小链接, 要核对时再看
 */
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { GFM_PLUGINS } from "../../lib/markdown";

import type { WikiSharePlan } from "../../lib/tauri";
import { wikiMarkdownUrl } from "../../lib/wikiMarkdownUrl";
import { wikiSubtypeLabel } from "./wikiLabels";

/** 同事那边点得开的名字: 已在部门里的 / 这次一起发的关系页、原件页 */
export function reachableNames(plan: WikiSharePlan, attached: string[], together: Set<string>): Set<string> {
  const out = new Set<string>();
  for (const r of plan.relations) {
    if (r.status === "shared" || (r.status === "local" && r.rel_path && together.has(r.rel_path))) {
      out.add(r.name);
      if (r.title) out.add(r.title);
    }
  }
  for (const s of plan.sources) {
    if (s.kind === "raw" && s.title && s.key && (s.shared || attached.includes(s.key))) out.add(s.title);
  }
  return out;
}

export default function WikiSharePreview({
  plan,
  reachable,
}: {
  plan: WikiSharePlan;
  reachable: Set<string>;
}) {
  const [raw, setRaw] = useState(false);
  const f = plan.preview.fields;

  const body = useMemo(() => {
    // 头部已经显示了标题: 正文开头一模一样的 H1 去掉 (跟知识页一样)
    const md = plan.preview.body_md;
    const lead = md.match(/^\s*#\s+([^\n]+)\n?/);
    const trimmed = lead?.[1].trim() === (f?.title ?? plan.title).trim() ? md.slice(lead[0].length).trimStart() : md;
    return trimmed.replace(/\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, (_m, name: string, alias?: string) =>
      `[${alias || name}](catfish-wikilink://${encodeURIComponent(name.trim())})`);
  }, [plan, f]);

  if (raw) {
    return (
      <div className="wiki-share__preview-wrap">
        <pre className="wiki-share__preview">{`---\n${plan.preview.frontmatter_yaml}\n---\n\n${plan.preview.body_md}`}</pre>
        <button type="button" className="wiki-share__link" onClick={() => setRaw(false)}>看渲染后的样子</button>
      </div>
    );
  }

  const linkClass = (name: string) =>
    "wiki-preview__wikilink" + (reachable.has(name) ? "" : " wiki-preview__wikilink--dangling");

  return (
    <div className="wiki-share__preview-wrap">
      <div className="wiki-share__preview wiki-share__preview--rendered">
        <div className="wiki-share__doc-title">{f?.title ?? plan.title}</div>
        {f && (
          <dl className="wiki-share__fields">
            {f.entity_type && (<><dt>类型</dt><dd>{wikiSubtypeLabel(f.entity_type, "entity")}</dd></>)}
            {f.aliases.length > 0 && (<><dt>别名</dt><dd>{f.aliases.join("、")}</dd></>)}
            {f.tags.length > 0 && (<><dt>标签</dt><dd>{f.tags.join("、")}</dd></>)}
            {f.related.length > 0 && (
              <>
                <dt>关系</dt>
                <dd>
                  {f.related.map((r, i) => (
                    <span key={`${r.name}-${i}`} className="wiki-share__rel">
                      {r.rel && <span className="wiki-share__muted">{r.rel} </span>}
                      <span className={linkClass(r.name)} title={reachable.has(r.name) ? "同事点得开" : "同事那边点不开"}>{r.name}</span>
                    </span>
                  ))}
                </dd>
              </>
            )}
            {f.sources.length > 0 && (
              <>
                <dt>来源</dt>
                <dd>
                  {f.sources.map((s) => (
                    <div key={s} className={reachable.has(s) ? "" : "wiki-share__muted"}>
                      {reachable.has(s) ? <span className={linkClass(s)}>{s}</span> : s}
                    </div>
                  ))}
                </dd>
              </>
            )}
          </dl>
        )}
        {f && f.evidence.length > 0 && (
          <details className="wiki-share__evidence">
            <summary>
              原文依据 {f.evidence.length} 条 <span className="wiki-share__warn-text">· 从原件里摘的原话, 会一起发出去</span>
            </summary>
            <ul>
              {f.evidence.map((q, i) => <li key={i}>「{q}」</li>)}
            </ul>
          </details>
        )}
        <div className="wiki-doc wiki-share__doc">
          <ReactMarkdown
            remarkPlugins={GFM_PLUGINS}
            urlTransform={wikiMarkdownUrl}
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith("catfish-wikilink://")) {
                  const name = decodeURIComponent(href.slice("catfish-wikilink://".length));
                  return <span className={linkClass(name)} title={reachable.has(name) ? "同事点得开" : "同事那边点不开"}>{children}</span>;
                }
                return <span className="wiki-share__ext">{children}</span>;
              },
            }}
          >
            {body}
          </ReactMarkdown>
        </div>
      </div>
      <div className="wiki-share__legend">
        <span className="wiki-preview__wikilink">点得开</span>
        <span className="wiki-preview__wikilink wiki-preview__wikilink--dangling">同事那边点不开</span>
        <button type="button" className="wiki-share__link" onClick={() => setRaw(true)}>看原始文本</button>
      </div>
    </div>
  );
}
