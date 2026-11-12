/** 知识库里的「人 → 邮箱」(11/12, tool-bridge wiki_people.py) —— 写信面板填收件人 / 收件人框给候选。只读本机。 */
import { invoke } from "@tauri-apps/api/core";

export interface WikiPerson { name: string; aliases: string[]; email: string; sources: string[] }
export interface WikiPeople { people: WikiPerson[]; matches: Record<string, string[]> }

export const wikiPeopleEmails = (names: string[] = []) => invoke<WikiPeople>("wiki_people_emails", { names });

let cache: { at: number; p: Promise<WikiPerson[]> } | null = null;
/** 候选用: 一分钟内重复打开写信面板不重扫知识库 */
export function wikiPeopleCached(): Promise<WikiPerson[]> {
  if (!cache || Date.now() - cache.at > 60_000) {
    const p = wikiPeopleEmails().then((r) => r.people).catch(() => [] as WikiPerson[]);
    cache = { at: Date.now(), p };
  }
  return cache.p;
}

/** 收件人框里最后一段 (逗号 / 分号后面正在打的那段) */
export function lastToken(value: string): string {
  const parts = value.split(/[,，;；]/);
  return (parts[parts.length - 1] ?? "").trim();
}

/** 已经填了的邮箱 (小写) */
export function filledEmails(value: string): Set<string> {
  return new Set((value.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g) ?? []).map((x) => x.toLowerCase()));
}

/** 按正在打的那段给候选: 名字 / 别名 / 邮箱里含这几个字; 已经填了的不再给; 最多 6 个 */
export function suggestPeople(people: WikiPerson[], value: string, limit = 6): WikiPerson[] {
  const t = lastToken(value).toLowerCase();
  if (!t || (!/[一-鿿]/.test(t) && t.length < 2)) return [];
  const have = filledEmails(value);
  const score = (p: WikiPerson) => (p.name.toLowerCase().startsWith(t) ? 0 : p.name.toLowerCase().includes(t) ? 1
    : p.aliases.some((a) => a.toLowerCase().includes(t)) ? 2 : p.email.includes(t) ? 3 : 9);
  return people.filter((p) => !have.has(p.email) && score(p) < 9).sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name, "zh"))
    .slice(0, limit);
}

/** 选了一个候选: 把正在打的那段换成邮箱, 后面补「, 」接着打 */
export function applySuggestion(value: string, email: string): string {
  const cut = Math.max(value.lastIndexOf(","), value.lastIndexOf("，"), value.lastIndexOf(";"), value.lastIndexOf("；"));
  const head = cut >= 0 ? value.slice(0, cut + 1).replace(/[，；;]$/, ",") + " " : "";
  return `${head}${email}, `;
}
