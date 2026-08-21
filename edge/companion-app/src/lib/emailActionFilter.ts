/** 邮件列表的 action 筛选 + 排序 (8/21, 分诊的下半件事)。
 *
 * badge 只是标注 —— 列表仍按时间混排, 「要办」的散在几百封里还是得自己翻。
 * 这里补上「按分类排列」: 员工点「办」只看要办的、且**截止日近的在前**。
 *
 * # 排序判据 (写死在测试里, 改之前看一眼)
 *
 *   全部  → 原序 (时间序)。不打乱人的习惯 —— 找"刚收到的那封"靠的是时间位置。
 *   办    → 有截止日的在前, 按截止日**升序** (最急的最上面);
 *           无截止日的排后, 按收信时间降序。
 *           YYYY-MM-DD 字典序 = 时间序, 直接字符串比较, 不解析日期。
 *   回    → 按收信时间降序 (回信通常越新越该先回)。
 *
 * # 为什么抽成纯函数
 *
 * EmailTab.tsx 顶着 800 行红线 (现 799); 且排序判据值得单测 ——
 * 「办里无截止日的排哪」这种边界, UI 里肉眼根本看不出错。
 */

export type ActionFilter = "全部" | "办" | "回";

export interface FilterableEmail {
  id: string;
  subject?: string;
  sender?: string;
  account?: string;
  date?: string;
}

export function filterAndRankEmails<T extends FilterableEmail>(
  items: T[],
  opts: {
    search: string;
    actionFilter: ActionFilter;
    actionMap: Record<string, { action: string; deadline?: string }>;
  },
): T[] {
  const q = opts.search.trim().toLowerCase();
  let out = !q
    ? items
    : items.filter(
        (m) =>
          (m.subject || "").toLowerCase().includes(q) ||
          (m.sender || "").toLowerCase().includes(q) ||
          (m.account || "").toLowerCase().includes(q),
      );

  if (opts.actionFilter === "全部") return out;

  out = out.filter((m) => opts.actionMap[m.id]?.action === opts.actionFilter);

  if (opts.actionFilter === "办") {
    // 有截止日的在前按截止日升序; 无截止日的排后按时间降序。
    // [...out] 复制是防御性的: 当前路径上 sort 只会作用在上面 filter 产出的
    // 新数组 ("全部"分支已提前 return, 走到这必先 filter), 原地排也污染不了
    // items —— 变异测试证明这是等价变异。留着是防将来有人把 filter 挪走。
    out = [...out].sort((a, b) => {
      const da = opts.actionMap[a.id]?.deadline;
      const db = opts.actionMap[b.id]?.deadline;
      if (da && db) return da < db ? -1 : da > db ? 1 : 0;
      if (da) return -1;
      if (db) return 1;
      return (b.date || "") < (a.date || "") ? -1 : 1;
    });
  } else {
    out = [...out].sort((a, b) => ((b.date || "") < (a.date || "") ? -1 : 1));
  }
  return out;
}
