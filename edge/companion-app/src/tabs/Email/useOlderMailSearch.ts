/** 搜索框搜「更早的邮件」(11/19 鸿波「搜索框也改成能搜到更早的邮件」)。
 *
 * 列表最多 500 封, 搜索框原来只在这 500 封里筛 (filterAndRankEmails)。现在输入停 600ms 后
 * 再去「邮件」App / 邮箱服务器搜一次 (emailSearch), 列表里已经有的去掉, 剩下的就是「更早的」。
 * 只搜员工正在看的文件夹 (收件箱 / 已发送 / 草稿箱)。换关键词 / 文件夹时, 上一次没回来的结果作废。
 */
import { useEffect, useState } from "react";

import { hideDeleted } from "../../lib/emailDeleted";
import { emailSearch, type EmailDigestItem } from "../../lib/tauri";
import { useEmailStore } from "../../store/email";

export const OLDER_SEARCH_DEBOUNCE_MS = 600;
export const OLDER_SEARCH_MIN_CHARS = 2;

export interface OlderMailSearch {
  older: EmailDigestItem[];
  searching: boolean;
  error: string | null;
  /** 这次结果对应的关键词 (还没搜过 = "") */
  query: string;
}

const IDLE: OlderMailSearch = { older: [], searching: false, error: null, query: "" };

export function useOlderMailSearch(search: string, folder: string, items: EmailDigestItem[]): OlderMailSearch {
  const [state, setState] = useState<OlderMailSearch>(IDLE);
  const deleted = useEmailStore((s) => s.deleted); // 在这里点开又删掉的, 跟列表一样立刻消失
  const q = search.trim();

  useEffect(() => {
    if (q.length < OLDER_SEARCH_MIN_CHARS) { setState(IDLE); return; }
    let cancelled = false;
    setState((s) => ({ ...s, searching: true, error: null }));
    const t = window.setTimeout(() => {
      emailSearch(q, folder)
        .then((raw) => {
          if (cancelled) return;
          const hits = JSON.parse(raw) as EmailDigestItem[];
          setState({ older: Array.isArray(hits) ? hits : [], searching: false, error: null, query: q });
        })
        .catch((e) => {
          if (!cancelled) setState({ older: [], searching: false, error: e instanceof Error ? e.message : String(e), query: q });
        });
    }, OLDER_SEARCH_DEBOUNCE_MS);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [q, folder]);

  // 列表里已经有的不算「更早的」(按 id; 同一封在两边 id 一样, 都来自 catfish-email)
  const have = new Set(items.map((m) => m.id));
  return { ...state, older: hideDeleted(state.older.filter((m) => !have.has(m.id)), deleted) };
}
