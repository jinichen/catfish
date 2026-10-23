/** 「可能重复」两条 (或几条) 条目合成一条 / 改名分开 (10/23)。
 *
 * 鸿波 10/23: 「公司治理体系概述」一条是手动入库的资料 (实体 · 文件), 一条是小鲶蒸馏的
 * (概念 · 体系), 关系整理里只列出来、点进去看, 没有任何处理按钮 —— 看完不知道怎么办。
 * 现在: 选一条保留, 其余的内容并进来、文件移到回收站; 或者确实是两个东西, 给其中一条改个名。
 */
import type { RelatedRef, WikiFileFull } from "../../lib/tauri_wiki";
import { setFrontmatterList } from "./WikiUnverifiedPanel";
import { buildConfirmedWikiContent } from "./wikiRelationshipTasks";

const norm = (s: string) => s.normalize("NFKC").replace(/[\s\-*•·,，.。:：;；()（）「」“”"']/g, "");

function union(...lists: (string[] | undefined)[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of lists.flat()) {
    const t = item?.trim();
    if (!t || seen.has(norm(t))) continue;
    seen.add(norm(t));
    out.push(t);
  }
  return out;
}

/** 正文去掉一级标题; 剩下的非空行 */
function bodyLines(body: string): string[] {
  return body.split(/\r?\n/).filter((line) => line.trim() && !/^#\s/.test(line.trim()));
}

/**
 * 把 others 并进 keep, 返回 keep 的新内容。
 *  · aliases: 并集, 其余条目的标题也记成别名 (写那个名字的关系照样连得上)
 *  · tags / sources / evidence / unverified: 并集
 *  · 关系: keep 的原样, 其余条目里 keep 没有的对象补上 (指向彼此的去掉)
 *  · 正文: 其余条目里 keep 正文没有的行, 追加到「并入自「X」」小节下
 *  · 状态不动 (合并不等于核对过关系)
 */
export function mergeWikiEntries(keep: WikiFileFull, others: WikiFileFull[]): string {
  const k = keep.info;
  const titles = new Set([k.title, ...others.map((o) => o.info.title)].map(norm));
  const aliases = union(k.aliases, ...others.map((o) => [o.info.title, ...o.info.aliases]))
    .filter((a) => norm(a) !== norm(k.title));
  const related: RelatedRef[] = [...k.related.filter((r) => r.source !== "body")];
  const haveRel = new Set(related.map((r) => norm(r.name)));
  for (const o of others) {
    for (const r of o.info.related) {
      if (r.source === "body" || titles.has(norm(r.name)) || haveRel.has(norm(r.name))) continue;
      haveRel.add(norm(r.name));
      related.push({ name: r.name, rel: r.rel });
    }
  }

  let content = buildConfirmedWikiContent(keep.content, related, k, { keepStatus: true });
  content = setFrontmatterList(content, "aliases", aliases);
  content = setFrontmatterList(content, "tags", union(k.tags, ...others.map((o) => o.info.tags)));
  content = setFrontmatterList(content, "sources", union(k.sources, ...others.map((o) => o.info.sources)));
  content = setFrontmatterList(content, "evidence", union(k.evidence, ...others.map((o) => o.info.evidence)));
  content = setFrontmatterList(content, "unverified", union(k.unverified, ...others.map((o) => o.info.unverified)));

  const have = new Set(bodyLines(keep.body).map(norm));
  const sections: string[] = [];
  for (const o of others) {
    const fresh = bodyLines(o.body).filter((line) => {
      const n = norm(line);
      if (!n || have.has(n) || /^#{2,6}\s/.test(line.trim())) return false;
      have.add(n);
      return true;
    });
    if (fresh.length) sections.push(`## 并入自「${o.info.title}」\n\n${fresh.join("\n")}`);
  }
  return sections.length ? `${content.replace(/\s*$/, "")}\n\n${sections.join("\n\n")}\n` : content;
}

/** 改名: frontmatter title + 正文一级标题; 旧名不留作别名 (留了还是撞名) */
export function renameWikiEntry(content: string, oldTitle: string, newTitle: string): string {
  const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) throw new Error("该条目缺少 frontmatter");
  const fm = m[1].replace(/^title\s*:.*$/m, `title: ${newTitle}`);
  const rest = content.slice(m[0].length).replace(new RegExp(`^#\\s+${oldTitle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "m"), `# ${newTitle}`);
  return `---\n${fm}\n---${rest}`;
}
