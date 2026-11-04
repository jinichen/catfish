/**
 * 选同事 (10/17 一起填表起, 11/4 抽出来给派任务共用): 已选的小标签 + 按姓名 / 部门搜全公司通讯录 +
 * 直接填邮箱。中央通讯录只给 邮箱 / 姓名 / 部门。
 */
import { useEffect, useState } from "react";

import { formDirectory, type Person } from "../../lib/tauri_forms";
import { inputStyle } from "./roomLinkUi";

/** 已选的 + 输入框里还没回车的邮箱, 去重小写。 */
export function allRecipients(picked: string[], typed: string): string[] {
  return [...new Set([...picked, ...typed.split(/[\s,;，；]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)])];
}

export default function RecipientPicker({ picked, onPicked, typed, onTyped, onError }: {
  picked: string[];
  onPicked: (next: string[]) => void;
  typed: string;
  onTyped: (v: string) => void;
  onError: (msg: string) => void;
}) {
  const [q, setQ] = useState("");
  const [people, setPeople] = useState<Person[]>([]);

  useEffect(() => {
    if (!q.trim()) { setPeople([]); return; }
    const t = setTimeout(() => {
      formDirectory(q.trim()).then((r) => setPeople(r.people)).catch((e) => onError(String(e)));
    }, 300);
    return () => clearTimeout(t);
  }, [q, onError]);

  const add = (email: string) => {
    const e = email.trim().toLowerCase();
    if (e && !picked.includes(e)) onPicked([...picked, e]);
  };

  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {picked.map((e) => (
          <span key={e} style={{ fontSize: 12, padding: "2px 8px", border: "1px solid var(--catfish-border)", borderRadius: 12 }}>
            {e} <button type="button" aria-label={`去掉 ${e}`} onClick={() => onPicked(picked.filter((x) => x !== e))}
                        style={{ border: "none", background: "transparent", cursor: "pointer" }}>✕</button>
          </span>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按姓名 / 部门搜同事" style={inputStyle} />
        <input value={typed} onChange={(e) => onTyped(e.target.value)} placeholder="或直接填邮箱, 多个用逗号隔开" style={inputStyle} />
      </div>
      {people.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 160, overflowY: "auto" }}>
          {people.map((p) => (
            <button key={p.email} type="button" onClick={() => add(p.email)} disabled={picked.includes(p.email)}
                    style={{ textAlign: "left", fontSize: 12, padding: "4px 8px", border: "none", cursor: "pointer",
                             background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))", borderRadius: 4 }}>
              {p.name} · {p.department} · {p.email}{picked.includes(p.email) ? " (已加)" : ""}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
