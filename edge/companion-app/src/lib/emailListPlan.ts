/** 邮件列表刷新: 平时只拉最新一小批合并进来, 隔一阵 / 手动收信才拉全量 (10/31)。
 *
 * 鸿波 10/31「邮件内容加载的时间很长」。实测 (Apple Mail, 3 个账号):
 *   - 单读一封 2.4 s; 但「邮件」App 一次只处理一个请求, 正在拉列表时读同一封要 20 s
 *   - 全量拉收件箱 500 封 20.6 s, 最新 30 封 2.5 s; 已发送 200 封 1.6 s
 * 原来每次打开邮件页、每 3 分钟后台轮询都拉全量 500 封 —— 「邮件」App 隔三差五忙 20 秒,
 * 这期间点开任何一封都得排队。改成平时拉最新 30 封合并 (邮件 App 只忙 2–3 秒),
 * 全量只在: 手里没有列表 / 员工点「收信」/ 距上次全量超过 30 分钟。
 *
 * 全量那次还负责把「在别处删掉的信」从列表里去掉 (增量看不到); Companion 里自己删的
 * 有墓碑, 不受影响。
 */

export const INCREMENTAL_LIMIT = 30;
export const FULL_REFRESH_MS = 30 * 60 * 1000;

// 进程级: 跨挂载存活。起点记成模块加载那一刻 —— 重启后进邮件页手里已经有落盘的列表,
// 不急着立刻全量 (那 20 秒正是员工开始点信的时候), 半小时后的轮询再做。
let lastFullAt = Date.now();

export interface ListFetchPlan {
  incremental: boolean;
  limit: number;
  sentLimit: number;
}

export function planListFetch(o: { hasItems: boolean; full?: boolean; inboxAll: boolean; maxLimit: number }): ListFetchPlan {
  const incremental = o.inboxAll && o.hasItems && !o.full && Date.now() - lastFullAt < FULL_REFRESH_MS;
  return incremental
    ? { incremental, limit: INCREMENTAL_LIMIT, sentLimit: INCREMENTAL_LIMIT }
    : { incremental, limit: o.maxLimit, sentLimit: 200 };
}

export function markFullRefresh(): void {
  lastFullAt = Date.now();
}

/** 测试用: 把「上次全量」拨到很久以前 / 现在。 */
export function _setLastFullAtForTest(t: number): void {
  lastFullAt = t;
}

type Mail = { id?: string; date?: string };

/** 新拉的一批 (同 id 以新的为准, 已读状态等会更新) + 手里原有的, 按时间新→旧, 截到 max 封。 */
export function mergeNewest<T>(prev: unknown[] | undefined, fresh: T[], max: number): T[] {
  const seen = new Set(fresh.map((m) => (m as Mail).id));
  const rest = ((prev ?? []) as T[]).filter((m) => !seen.has((m as Mail).id));
  return [...fresh, ...rest]
    .sort((a, b) => String((b as Mail).date ?? "").localeCompare(String((a as Mail).date ?? "")))
    .slice(0, max);
}
