/** 搜索框下面「更早的邮件」那一段 (11/19)。列表里筛到的照旧在上面, 这里接着放列表以外搜到的。 */
import type { EmailDigestItem } from "../../../lib/tauri";
import type { OlderMailSearch } from "../useOlderMailSearch";
import ListItem from "./ListItem";

const noteStyle = { padding: "8px 12px", fontSize: 11, color: "var(--catfish-text-muted)", lineHeight: 1.5 } as const;

export default function OlderMailResults({ result, search, selectedId, showRecipients, onSelect }: {
  result: OlderMailSearch;
  search: string;
  selectedId: string | null;
  showRecipients: boolean;
  onSelect: (m: EmailDigestItem) => void;
}) {
  if (search.trim().length < 2) return null;
  const { older, searching, error } = result;
  return (
    <>
      <li style={{ ...noteStyle, borderTop: "1px solid var(--catfish-border)", background: "var(--catfish-bg-elevated, var(--catfish-bg))" }}>
        {searching
          ? "🔍 正在搜更早的邮件…"
          : error
            ? `更早的邮件没搜成: ${error}`
            : older.length
              ? `更早的邮件 · ${older.length} 封 (列表以外, 在邮箱里搜到的)`
              : "更早的邮件里也没有"}
      </li>
      {!searching && older.map((m) => (
        <ListItem key={`older-${m.id}`} item={m} active={selectedId === m.id} showRecipients={showRecipients} onClick={() => onSelect(m)} />
      ))}
    </>
  );
}
