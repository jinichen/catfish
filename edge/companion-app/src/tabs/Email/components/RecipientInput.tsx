/**
 * 收件人 / 抄送输入框 + 知识库候选 (11/12 鸿波「如果现有的知识库有收件人的邮件地址, 是不是就会自动填进去」)。
 *
 * 打名字 (一个汉字起) 或邮箱片段 (两个字母起), 下面列出知识库里对得上的人 (名字 · 邮箱 · 来自哪一页);
 * 点一下 / ↑↓ + 回车 就换成邮箱, 后面补「, 」接着打下一个。Esc 收起。候选只来自本机知识库 (wiki_people.py)。
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { applySuggestion, suggestPeople, wikiPeopleCached, type WikiPerson } from "../../../lib/tauri_wiki_people";

export default function RecipientInput({ value, onChange, placeholder, ariaLabel, style }: {
  value: string; onChange: (v: string) => void; placeholder?: string; ariaLabel: string; style?: CSSProperties;
}) {
  const [people, setPeople] = useState<WikiPerson[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { void wikiPeopleCached().then(setPeople); }, []);
  const hits = open ? suggestPeople(people, value) : [];
  useEffect(() => setActive(0), [value]);

  function pick(p: WikiPerson) {
    onChange(applySuggestion(value, p.email));
    setOpen(true);
    ref.current?.focus();
  }

  return (
    <span style={{ position: "relative", flex: 1, display: "flex", minWidth: 0 }}>
      <input
        ref={ref}
        type="text"
        aria-label={ariaLabel}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (!hits.length) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % hits.length); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + hits.length) % hits.length); }
          else if (e.key === "Enter") { e.preventDefault(); pick(hits[active]); }
          else if (e.key === "Escape") setOpen(false);
        }}
        style={{ ...style, flex: 1, minWidth: 0 }}
      />
      {hits.length > 0 && (
        <div role="listbox" aria-label={`${ariaLabel}候选`}
             style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20, marginTop: 2, borderRadius: 6,
                      border: "1px solid var(--catfish-border)", background: "var(--catfish-bg-elevated)",
                      boxShadow: "0 8px 24px rgba(0,0,0,0.18)", overflow: "hidden" }}>
          {hits.map((p, i) => (
            <div key={p.email + p.name} role="option" aria-selected={i === active}
                 onMouseDown={(e) => { e.preventDefault(); pick(p); }}
                 onMouseEnter={() => setActive(i)}
                 style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "6px 10px", cursor: "pointer", fontSize: 13,
                          background: i === active ? "var(--catfish-bg-cream, rgba(14,95,102,0.10))" : "transparent" }}>
              <strong style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{p.name}</strong>
              <span style={{ color: "var(--catfish-text)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.email}</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 180 }}
                    title={`知识库「${p.sources.join("」「")}」`}>
                来自「{p.sources[0]}」
              </span>
            </div>
          ))}
        </div>
      )}
    </span>
  );
}
