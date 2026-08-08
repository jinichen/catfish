/** 拟稿前从本地 wiki 捞背景。
 *
 *  # 为什么是 wiki 而不是「历史邮件」(8/6)
 *
 *  最初想的是串 thread：把同话题的历史邮件喂给拟稿。在鸿波本机实测下来这条路是堵的：
 *
 *    · Sent 只有 8 封 —— 他基本不在这台机器回邮件, 回信在手机/网页/别的机器,
 *      所以「我说过什么」这一半在本机根本不存在
 *    · thread 覆盖率 0.7% —— 且这个数还是在通知箱上量的, 参考价值也有限
 *
 *  换个角度：他跟小鲶的对话已经被沉淀成 wiki 了, 而 wiki 恰好装的就是 Sent 给不了的
 *  那一半。`wiki/entities/chenxiuping.md` 里原话:
 *
 *    > 员工曾收到其发送的第二期集采框架合同及采购方案文件, 在处理时发现版本与预期
 *    > 不符, 遂联系陈秀平要求提供包含侯婧媛批注意见的正确版本。
 *
 *  这条「还欠着一个文件」邮件侧永远看不到 —— 它不在邮件里, 是他跟小鲶说的。
 *
 *  # 为什么不用 local-search (search.db)
 *
 *  两个都试过。search.db 覆盖面更广 (能捞到 outputs/ 下的催办名单和上次的回信草稿),
 *  但它的 SearchHit 只带 20-token snippet 没有全文, 而且会扫到 wiki_dedup_backup_* /
 *  wiki_frontmatter_backup_* 这些备份目录。
 *
 *  `wiki_search_text` + `wiki_read_file` 是现成的 (Rust 侧早就有且已注册):
 *    · 只扫 wiki/entities|concepts|queries + wiki-shared/dept, 天然不碰备份目录
 *    · WikiFileFull.body 已经剥好 frontmatter
 *
 *  代价是捞不到 outputs/ 下那些 —— 先接 wiki, 不够再说。
 */

import { wikiSearchText, wikiReadFile } from "./tauri";
import type { DraftContextItem } from "./emailDraft";

/** 最多喂几条。实测 wiki 页 300–1100 字, 4 条约 2500 字, 跟原邮件的 1500 字量级相当。 */
const MAX_ITEMS = 4;

/** 整体预算。超了就用已经拿到的, 不阻塞拟稿 —— 有背景更好, 没有也得能出草稿。 */
const BUDGET_MS = 6_000;

/** 墓碑页：wiki 里 251 篇有 30 篇是「已废弃 · 内容已并入 X」的重定向壳 (12%)。
 *
 *  它们靠标题精确匹配能拿 20 分排到很前 —— 实测搜「陈秀平」时
 *  `wiki/entities/陈秀平.md` (41 字, 全文是一句「已废弃」) 排第 2, 白占一个位置。
 */
function _isTombstone(body: string): boolean {
  const b = body.trim();
  if (b.length < 80) return true;
  return b.includes("已废弃") || b.includes("已并入");
}

/** 从 "张三 <zhang@x.com>" 里取出用来检索的名字。
 *
 *  优先显示名 —— wiki 里的人物页标题是中文名 (`陈秀平`), 不是邮箱地址。
 *  没有显示名时退回 @ 前面那段。
 */
export function senderQueryKey(sender: string): string {
  const m = sender.match(/^\s*"?([^"<]+?)"?\s*</);
  const display = (m?.[1] || "").trim();
  if (display && !display.includes("@")) return display;
  const addr = (sender.match(/<([^>]+)>/)?.[1] || sender).trim();
  return addr.split("@")[0] || addr;
}

export interface DraftContextResult {
  items: DraftContextItem[];
  /** 检索用的关键词, 出错时显给员工看 */
  query: string;
}

/** 为「回复这封邮件」捞背景。任何一步失败都返空, 不抛 —— 拟稿不能被它挡住。 */
export async function buildDraftContext(sender: string): Promise<DraftContextResult> {
  const query = senderQueryKey(sender);
  if (!query) return { items: [], query: "" };

  const deadline = Date.now() + BUDGET_MS;
  let hits;
  try {
    hits = await wikiSearchText(query);
  } catch {
    return { items: [], query };
  }
  if (!hits?.length) return { items: [], query };

  const items: DraftContextItem[] = [];
  for (const h of hits) {
    if (items.length >= MAX_ITEMS || Date.now() > deadline) break;
    try {
      const full = await wikiReadFile(h.rel_path);
      const body = (full?.body || "").trim();
      if (!body || _isTombstone(body)) continue;
      items.push({ title: h.title || h.rel_path, relPath: h.rel_path, body });
    } catch {
      // 单篇读失败跳过, 不影响其他
    }
  }
  return { items, query };
}
