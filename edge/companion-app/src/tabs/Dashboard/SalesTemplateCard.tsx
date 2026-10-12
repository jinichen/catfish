/** 销售模板 (10/12, docs/SALES-FOLLOWUP-PLAN.md): 阶段名 + 每个阶段多少天没动静算冷了。
 *
 * 不同商品的销售阶段不同 (软件: 线索→报价→谈判; 包装: 询价→打样→报价→试单…), 这里改了,
 * 小鲶建客户 / 改阶段 / 早安「客户跟进」都按这份来。存在本机 ~/.catfish/sales_template.json。
 */
import { useEffect, useState } from "react";

import { salesTemplateGet, salesTemplateSet, type SalesTemplate } from "../../lib/tauri_sales";

const card: React.CSSProperties = {
  background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md)", padding: "var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-3)",
};
const input: React.CSSProperties = {
  padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--catfish-border)",
  background: "var(--catfish-bg)", color: "var(--catfish-text)", fontSize: 13,
};
const num = (v: string) => Math.max(0, Math.floor(Number(v) || 0));

export default function SalesTemplateCard() {
  const [tpl, setTpl] = useState<SalesTemplate | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    salesTemplateGet().then(setTpl).catch((e) => setMsg({ ok: false, text: `读不到销售模板: ${e}` }));
  }, []);

  if (!tpl) return msg ? <div style={card}>{msg.text}</div> : null;
  const set = (patch: Partial<SalesTemplate>) => setTpl({ ...tpl, ...patch });
  const setStage = (i: number, patch: Partial<SalesTemplate["stages"][number]>) =>
    set({ stages: tpl.stages.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      setTpl(await salesTemplateSet(tpl!));
      setMsg({ ok: true, text: "已保存。早安「客户跟进」下次刷新按新模板算。" });
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={card}>
      <h3 style={{ margin: 0 }}>销售模板</h3>
      <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
        客户的阶段, 以及每个阶段客户多少天没来消息算「冷了」(0 = 这个阶段不提醒, 比如成交 / 输单)。小鲶建客户、改阶段和早安「客户跟进」都按这里来。
      </div>
      <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
        模板名 <input style={{ ...input, width: 200 }} value={tpl.name} onChange={(e) => set({ name: e.target.value })} />
      </label>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(120px, 220px) 140px auto", gap: 6, alignItems: "center", fontSize: 13 }}>
        <span style={{ color: "var(--catfish-text-muted)" }}>阶段</span>
        <span style={{ color: "var(--catfish-text-muted)" }}>几天没动静算冷</span>
        <span />
        {tpl.stages.map((s, i) => (
          <div key={i} style={{ display: "contents" }}>
            <input style={input} value={s.name} onChange={(e) => setStage(i, { name: e.target.value })} />
            <input style={input} type="number" min={0} value={s.cold_days} onChange={(e) => setStage(i, { cold_days: num(e.target.value) })} />
            <button
              type="button" className="chat-composer__secondary-button" disabled={tpl.stages.length <= 1}
              onClick={() => set({ stages: tpl.stages.filter((_, j) => j !== i) })}
            >
              删除
            </button>
          </div>
        ))}
      </div>
      <button
        type="button" className="chat-composer__secondary-button" style={{ alignSelf: "flex-start" }}
        onClick={() => set({ stages: [...tpl.stages, { name: "", cold_days: 7 }] })}
      >
        + 加一个阶段
      </button>
      <div style={{ display: "flex", gap: "var(--space-4)", flexWrap: "wrap", fontSize: 13, alignItems: "center" }}>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          算「已报价」的阶段
          <select style={input} value={tpl.quoted_stage} onChange={(e) => set({ quoted_stage: e.target.value })}>
            {tpl.stages.map((s) => s.name && <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          客户问了多少个工作日没回就提醒
          <input style={{ ...input, width: 64 }} type="number" min={0} value={tpl.reply_wait_days} onChange={(e) => set({ reply_wait_days: num(e.target.value) })} />
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          报价后几天没回音就提醒
          <input style={{ ...input, width: 64 }} type="number" min={0} value={tpl.quote_followup_days} onChange={(e) => set({ quote_followup_days: num(e.target.value) })} />
        </label>
      </div>
      <button type="button" className="chat-composer__primary-button" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => void save()}>
        {busy ? "保存中…" : "保存"}
      </button>
      {msg && <div style={{ fontSize: 13, color: msg.ok ? "var(--status-ok)" : "var(--status-err)" }}>{msg.text}</div>}
    </div>
  );
}
