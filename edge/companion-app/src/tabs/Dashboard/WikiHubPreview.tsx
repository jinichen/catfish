/** 部门知识库 (WikiHubCard) 的三样小东西 (11/12 鸿波「项目是不是需要优化」):
 *
 *   · cleanPreview  卡片上那两行摘要: 去 `#` `**` `|` 这些格式符号、开头重复的标题、原件页的「SOURCE_PATH: /Users/…」。
 *                   中心端 (wiki-hub preview.py) 已经这么生成, 这里再过一遍是给还没升级的中心 / 老数据兜底。
 *   · personLabel   「chenhongbo@ffcs.cn · 2 天前 · chenhongbo@ffcs.cn 审过」→ 邮箱只留 @ 前面, 自己审自己的不再重复。
 *   · WikiDocPreview 不收进资料库也能先看全文 (原来只能先「安装」再去资料库翻)。
 */
import { useEffect, useState } from "react";

import { ModalShell } from "../../components/ModalShell";
import { config } from "../../lib/env";
import { Markdown } from "../../lib/markdown";
import { fetchWithAuth } from "../../lib/me";

const META_LINE = /^\s*(?:SOURCE_PATH|SOURCE_FILE|KEPT_PATH|kept_path|METHOD|EXTRACT(?:ED)?_(?:BY|WITH))\s*[:：].*$/gm;
const LOCAL_PATH =
  /(?:~|\/Users\/[^/\s]+|\/home\/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)[/\\](?:(?:(?!:\/\/)[^\n"'`<>|;；，。、])*?\.[A-Za-z0-9]{1,8}(?=$|[\s，。；;、）)\]"'`])|[^\s"'`<>|，。；;、）)\]]+)/gm;

const norm = (s: string) => s.replace(/[\s·\-—_：:（）()]+/g, "").toLowerCase();
const baseName = (p: string) => p.replace(/[/\\\s]+$/, "").split(/[/\\]/).pop() || "本机文件";

/** 去掉原件页头上的「# Upload: 文件名」和 SOURCE_PATH / METHOD 说明行, 本机绝对路径只留文件名 (全文预览也用) */
export function hideLocalPaths(text: string): string {
  return text.replace(/^\s*#\s*Upload:[^\n]*\n+/, "").replace(META_LINE, "").replace(LOCAL_PATH, (m) => `「${baseName(m)}」`);
}

export function cleanPreview(title: string, raw: string, limit = 200): string {
  const lines: string[] = [];
  for (const rawLine of hideLocalPaths(raw || "").split("\n")) {
    let ln = rawLine.trim();
    if (!ln || /^[\s|:-]+$/.test(ln) || ln.startsWith("```")) continue;
    const heading = ln.startsWith("#");
    ln = ln
      .replace(/^#+\s*/, "")
      .replace(/^(?:[-*+]\s+|>\s*|\d+[.)、]\s+)/, "")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, a: string, b?: string) => b || a)
      .replace(/(\*\*|__|\*|`|~~)/g, "")
      .replace(/\|/g, " ")
      .trim();
    if (heading) ln = ln.replace(/^Upload:\s*/, "");
    if (!ln) continue;
    // 第一行是「# 标题」就不重复; 「# 标题 - 一句说明」只留说明
    if (!lines.length && heading) {
      if (/^#+\s*Upload:/.test(rawLine.trim()) || norm(title).includes(norm(ln))) continue;
      if (title && ln.startsWith(title)) {
        ln = ln.slice(title.length).replace(/^[\s\-—:：·|]+/, "").trim();
        if (!ln) continue;
      }
    }
    lines.push(ln);
    if (lines.join("").length > limit) break;
  }
  return lines.join(" ").replace(/\s+/g, " ").trim().slice(0, limit);
}

const local = (email: string) => email.split("@")[0] || email;

/** 「分享人 · 时间 · 谁审过」—— 邮箱只留 @ 前面; 分享人自己审的写「已审」 */
export function personLabel(publishedBy: string, when: string, reviewedBy?: string | null): string {
  const who = publishedBy ? local(publishedBy) : "?";
  const reviewed = reviewedBy ? (reviewedBy === publishedBy ? " · 已审" : ` · ${local(reviewedBy)} 审过`) : "";
  return `${who}${when ? ` · ${when}` : ""}${reviewed}`;
}

export function WikiDocPreview({ namespace, fileId, title, onClose }: {
  namespace: string; fileId: string; title: string; onClose: () => void;
}) {
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const segs = namespace.split("/").map(encodeURIComponent).join("/");
    fetchWithAuth(`${config.gatewayUrl}/v1/wiki/documents/${segs}/${encodeURIComponent(fileId)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 403 ? "你没有权限看这一页" : r.status === 404 ? "这一页已经不在了" : `HTTP ${r.status}`);
        const d = (await r.json()) as { body_md?: string };
        setBody(d.body_md ?? "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [namespace, fileId]);
  return (
    <ModalShell onClose={onClose} maxWidth={820} themed>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <strong style={{ fontSize: 15, flex: 1, minWidth: 0, color: "var(--catfish-text)" }}>{title}</strong>
        <button type="button" onClick={onClose} aria-label="关闭预览"
                style={{ border: "1px solid var(--catfish-border)", background: "transparent", color: "var(--catfish-text)",
                         borderRadius: 4, padding: "2px 10px", cursor: "pointer", fontSize: 12 }}>关闭</button>
      </div>
      <div style={{ maxHeight: "65vh", overflowY: "auto", fontSize: 13, lineHeight: 1.7, color: "var(--catfish-text)" }}>
        {error ? <span style={{ color: "var(--status-err)" }}>{error}</span>
          : body === null ? <span style={{ color: "var(--catfish-text-muted)" }}>正在打开…</span>
          : <Markdown text={hideLocalPaths(body) || "(这一页没有正文)"} />}
      </div>
    </ModalShell>
  );
}
