/** 知识库卡片: 本机已装副本跟 hub 比, 是不是落后了 (9/30).
 *
 * 之前卡片只记"已装 / 未装"。发布方改了内容重发, hub 上 updated_at 变了,
 * 已装的人这边永远显示 "✓ 已装", 静默用旧版, 除非手动卸了重装。
 *
 * 比较基准:
 *   - 装的时候记下的 hub 版本 (meta.json 的 hub_updated_at) —— 两边都是 hub 的时钟, 可比
 *   - 9/30 前装的没有这个字段, 退回用本机 installed_at。那是本机时钟, 跟服务器
 *     可能差几分钟, 但只影响那些老副本, 最坏是多提示一次"有更新"。
 * 两边任何一个解析不了 → 不提示 (宁可漏报, 不要每分钟乱闪)。
 */

export interface HubDocTimes {
  updated_at: string | null;
  published_at: string | null;
}

export interface InstalledTimes {
  hubUpdatedAt: string;
  installedAt: string;
}

export function hasHubUpdate(doc: HubDocTimes, installed: InstalledTimes | undefined): boolean {
  if (!installed) return false;
  const hub = Date.parse(doc.updated_at || doc.published_at || "");
  const base = Date.parse(installed.hubUpdatedAt || installed.installedAt || "");
  if (Number.isNaN(hub) || Number.isNaN(base)) return false;
  return hub > base;
}

/** toolBridgeCallTool 的外层 ok 只表示"调到了工具", 工具自己失败在 result.ok 里。
 *  之前只看外层, 安装失败也提示 "已装"。 */
export function toolResultError(res: { ok: boolean; result?: unknown; error?: string | null }): string | null {
  if (!res.ok) return res.error || "工具调用失败";
  const r = res.result as { ok?: boolean; error?: string } | null | undefined;
  if (r && typeof r === "object" && r.ok === true) return null;
  if (r && typeof r === "object" && typeof r.error === "string" && r.error) return r.error;
  return typeof res.result === "string" ? res.result : JSON.stringify(res.result ?? null);
}

/** 11/09 分组: 全公司可见的 (管理员审过, 含别的部门分享的) 放一组排最前, 其余按部门 */
export const COMPANY_GROUP = "全公司";
export function groupOf(d: { namespace: string; visible_to?: string | null }): string {
  return d.visible_to === "company" ? COMPANY_GROUP : d.namespace.replace(/^dept\//, "");
}
export function sortGroups(groups: string[]): string[] {
  return [...groups].sort((a, b) => Number(b === COMPANY_GROUP) - Number(a === COMPANY_GROUP) || a.localeCompare(b));
}

/** 11/13: 「同事分享」里不列自己分享的 —— 那几页本来就在自己的资料库里, 列出来还挂着「收进资料库」
 *  (鸿波: 「为什么自己分享的也会列出来」)。发布人是中央按登录身份记的 (published_by = 登录邮箱),
 *  跟本机登录邮箱比, 不分大小写。不知道我是谁 (没登录 / 取不到) 时不过滤, 宁可多列。 */
export function splitMine<T extends { published_by: string }>(docs: T[], me: string | null | undefined): { others: T[]; mine: T[] } {
  const who = (me ?? "").trim().toLowerCase();
  if (!who) return { others: docs, mine: [] };
  const mine = docs.filter((d) => (d.published_by ?? "").trim().toLowerCase() === who);
  return { others: docs.filter((d) => !mine.includes(d)), mine };
}
