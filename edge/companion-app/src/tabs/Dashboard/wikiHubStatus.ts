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
