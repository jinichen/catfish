/** 知识卡「来自哪些对话」—— 点一下打开原对话 (11/02, 借 DeepTutor 的记忆脚注)。
 *
 * 知识库几乎全是从聊天蒸馏出来的, 看到可疑的一条, 员工要能一步查回原话。
 * 蒸馏时按核对过的原文段落记下了 `session:<id>` (精确到哪次对话); 老条目只有
 * `journal:日期`, 就把那天日志里记过的对话都列出来, 标「当天」让员工自己挑。
 */
import * as React from "react";

import { wikiSourceSessions, type WikiSourceSession } from "../../lib/tauri_wiki";
import { useUIStore } from "../../store/ui";

const MAX_SHOWN = 8;

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function WikiSourceSessions({ sources }: { sources: string[] }) {
  const [items, setItems] = React.useState<WikiSourceSession[] | null>(null);
  const [all, setAll] = React.useState(false);
  const openChatSession = useUIStore((s) => s.openChatSession);
  const key = sources.join("|");

  React.useEffect(() => {
    let alive = true;
    const relevant = sources.filter((s) => s.startsWith("session:") || s.startsWith("journal:"));
    if (relevant.length === 0) {
      setItems([]);
      return;
    }
    wikiSourceSessions(relevant).then((r) => { if (alive) setItems(r); }).catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!items || items.length === 0) return null;
  const shown = all ? items : items.slice(0, MAX_SHOWN);
  const exactCount = items.filter((i) => i.exact).length;
  return (
    <details className="wiki-preview__relation-details" open={exactCount > 0}>
      <summary>来自哪些对话 ({items.length})</summary>
      <ul className="wiki-preview__evidence" aria-label="来源对话">
        {shown.map((i) => (
          <li key={i.sessionId}>
            <button type="button" className="wiki-preview__source-link"
              onClick={() => openChatSession(i.sessionId)} title="打开这次对话">
              {i.title || "(没有标题的对话)"}
            </button>
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
              {" · "}{when(i.startedAt)}{i.exact ? "" : " · 当天的对话, 不一定是这次"}
            </span>
          </li>
        ))}
      </ul>
      {items.length > MAX_SHOWN && !all && (
        <button type="button" className="wiki-preview__source-link" onClick={() => setAll(true)}>
          还有 {items.length - MAX_SHOWN} 次, 全部显示
        </button>
      )}
    </details>
  );
}
