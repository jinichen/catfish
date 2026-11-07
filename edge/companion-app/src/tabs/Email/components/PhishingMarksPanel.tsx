/** 邮箱设置里的「钓鱼标注」(11/07): 你在红条上标过「不是钓鱼」/「这是钓鱼」的发件人, 一行一个, 能撤销。
 *  标注只存在这台电脑上, 也作为参考交给模型复审。撤销后这个发件人的信下次进邮件页时按没标过重新判。 */
import { useEffect, useState } from "react";

import { usePhishingMarksStore } from "../../../store/phishingMarks";

export default function PhishingMarksPanel() {
  const marks = usePhishingMarksStore((s) => s.marks);
  const load = usePhishingMarksStore((s) => s.load);
  const mark = usePhishingMarksStore((s) => s.mark);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void load(); }, [load]);
  const rows = Object.entries(marks?.senders ?? {}).sort((a, b) => b[1].at.localeCompare(a[1].at));
  if (!rows.length) return null;

  const undo = async (addr: string) => {
    setBusy(addr);
    setError(null);
    try {
      await mark(addr, null, "", []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ margin: "8px 12px", padding: "10px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
                  background: "var(--catfish-bg)", fontSize: 12, lineHeight: 1.6 }}>
      <strong>钓鱼标注</strong>
      <div style={{ color: "var(--catfish-muted)", fontSize: 11 }}>
        你在邮件上标过的发件人: 以后这些地址的来信按你的标注报, 也作为参考让小鲶判得更准。只存在这台电脑上。
      </div>
      {rows.map(([addr, m]) => (
        <div key={addr} style={{ display: "flex", gap: 8, alignItems: "baseline", marginTop: 4, flexWrap: "wrap" }}>
          <span style={{ color: m.verdict === "safe" ? "var(--status-ok, #059669)" : "rgb(185,28,28)", flexShrink: 0 }}>
            {m.verdict === "safe" ? "不是钓鱼" : "是钓鱼"}
          </span>
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}
                title={m.subject ? `标的时候那封: ${m.subject}` : undefined}>
            {m.name ? `${m.name} <${addr}>` : addr}
            {m.subject && <span style={{ color: "var(--catfish-muted)" }}> · {m.subject}</span>}
          </span>
          <button type="button" disabled={busy === addr} onClick={() => void undo(addr)}
                  style={{ border: "none", background: "transparent", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12, padding: 0 }}>
            {busy === addr ? "撤销中…" : "撤销"}
          </button>
        </div>
      ))}
      {error && <div style={{ color: "rgb(185,28,28)" }}>没撤销成: {error}</div>}
    </div>
  );
}
