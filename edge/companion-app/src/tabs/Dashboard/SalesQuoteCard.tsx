/** 价格表 + 报价单 (销售跟进阶段 2, docs/SALES-FOLLOWUP-PLAN.md §10)。
 *
 * 价格表 = 团队现有的 Excel, 导入后小鲶出报价单按它取价 (价格表外的商品要员工给价, 不编)。
 * 报价方信息 / 有效期 / 条款填在这里; 团队有自己的报价单 .xlsx 就导入当模板, 否则用内置版式。
 * 都存在本机 ~/.catfish/sales/。
 */
import { useEffect, useState } from "react";

import {
  salesPickXlsx, salesPricebookGet, salesPricebookImport, salesQuoteSettingsGet, salesQuoteSettingsSet,
  salesQuoteTemplateClear, salesQuoteTemplateImport, type Pricebook, type QuoteSettings,
} from "../../lib/tauri_sales";

const card: React.CSSProperties = {
  background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md)", padding: "var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-3)",
};
const input: React.CSSProperties = {
  padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--catfish-border)",
  background: "var(--catfish-bg)", color: "var(--catfish-text)", fontSize: 13,
};
const muted: React.CSSProperties = { fontSize: 13, color: "var(--catfish-text-muted)" };
const cell: React.CSSProperties = { padding: "4px 8px", borderBottom: "1px solid var(--catfish-border)", textAlign: "left" };
const PREVIEW_ROWS = 8;

const money = (n: number | null) => (n == null ? "" : n.toLocaleString("zh-CN", { maximumFractionDigits: 2 }));

type Msg = { ok: boolean; text: string } | null;

function Status({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return <div style={{ fontSize: 13, color: msg.ok ? "var(--status-ok)" : "var(--status-err)" }}>{msg.text}</div>;
}

export function PricebookCard() {
  const [book, setBook] = useState<Pricebook | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    salesPricebookGet().then(setBook).catch((e) => setMsg({ ok: false, text: `读不到价格表: ${e}` }));
  }, []);

  async function importFile() {
    const path = await salesPickXlsx("选择价格表 (Excel)");
    if (!path) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await salesPricebookImport(path);
      setBook(r);
      const skipped = r.skipped ? `, ${r.skipped} 行没有价格跳过了` : "";
      setMsg({ ok: true, text: `导入了 ${r.count} 个商品${skipped}。核对一下下面的价格。` });
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  const items = book?.items ?? [];
  return (
    <div style={card}>
      <h3 style={{ margin: 0 }}>价格表</h3>
      <div style={muted}>
        导入团队现有的 Excel 价格表。小鲶出报价单按它取价; 价格表里没有的商品会先问你单价, 不会自己编。
        表头写「商品 / 规格 / 单位 / 标价 / 最低价 / 备注」这类常见叫法就能认; 折后低于「最低价」要你确认才出单。
      </div>
      {book?.source ? (
        <div style={{ fontSize: 13 }}>
          当前: <b>{book.source}</b>
          {book.sheet ? ` (${book.sheet})` : ""} · {items.length} 个商品
          {book.imported_at ? ` · ${book.imported_at.replace("T", " ").slice(0, 16)} 导入` : ""}
        </div>
      ) : (
        <div style={muted}>还没导入价格表。</div>
      )}
      {items.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 13, minWidth: 420 }}>
            <thead>
              <tr>{["商品", "规格", "单位", "标价", "最低价"].map((h) => <th key={h} style={{ ...cell, ...muted }}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {items.slice(0, PREVIEW_ROWS).map((x, i) => (
                <tr key={i}>
                  <td style={cell}>{x.name}</td>
                  <td style={cell}>{x.spec}</td>
                  <td style={cell}>{x.unit}</td>
                  <td style={{ ...cell, textAlign: "right" }}>{money(x.price)}</td>
                  <td style={{ ...cell, textAlign: "right" }}>{money(x.floor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length > PREVIEW_ROWS && <div style={{ ...muted, marginTop: 4 }}>… 还有 {items.length - PREVIEW_ROWS} 个</div>}
        </div>
      )}
      <button type="button" className="chat-composer__primary-button" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => void importFile()}>
        {busy ? "导入中…" : book?.source ? "重新导入" : "导入 Excel 价格表"}
      </button>
      <Status msg={msg} />
    </div>
  );
}

export function QuoteSettingsCard() {
  const [s, setS] = useState<QuoteSettings | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    salesQuoteSettingsGet().then(setS).catch((e) => setMsg({ ok: false, text: `读不到报价单设置: ${e}` }));
  }, []);

  if (!s) return msg ? <div style={card}>{msg.text}</div> : null;
  const set = (patch: Partial<QuoteSettings>) => setS({ ...s, ...patch });

  async function run(fn: () => Promise<QuoteSettings>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      setS(await fn());
      setMsg({ ok: true, text: ok });
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function importTemplate() {
    const path = await salesPickXlsx("选择公司报价单模板 (Excel)");
    if (path) await run(() => salesQuoteTemplateImport(path), "已换成公司模板, 下一份报价单按它出。");
  }

  const { custom_template: _ro, ...editable } = s;
  const field = (label: string, key: "company" | "contact" | "phone" | "email", placeholder = "") => (
    <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
      <span style={{ width: 64 }}>{label}</span>
      <input style={{ ...input, flex: 1, maxWidth: 320 }} value={s[key]} placeholder={placeholder} onChange={(e) => set({ [key]: e.target.value })} />
    </label>
  );

  return (
    <div style={card}>
      <h3 style={{ margin: 0 }}>报价单</h3>
      <div style={muted}>报价单上的我方信息和条款。跟小鲶说「给 X 公司出个报价」就会按这里生成 Excel; 只生成文件, 发不发由你。</div>
      {field("报价方", "company", "我方公司全称")}
      {field("联系人", "contact", "留空 = 用客户档案里的负责销售")}
      {field("电话", "phone")}
      {field("邮箱", "email")}
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
        <span style={{ width: 64 }}>有效期</span>
        <input style={{ ...input, width: 64 }} type="number" min={1} value={s.valid_days}
          onChange={(e) => set({ valid_days: Math.max(1, Math.floor(Number(e.target.value) || 1)) })} /> 天
      </label>
      <label style={{ display: "flex", gap: 8, fontSize: 13 }}>
        <span style={{ width: 64, paddingTop: 6 }}>条款</span>
        <textarea style={{ ...input, flex: 1, maxWidth: 480, minHeight: 72, fontFamily: "inherit" }} value={s.terms}
          onChange={(e) => set({ terms: e.target.value })} />
      </label>
      <button type="button" className="chat-composer__primary-button" style={{ alignSelf: "flex-start" }} disabled={busy}
        onClick={() => void run(() => salesQuoteSettingsSet(editable), "已保存。")}>
        保存
      </button>
      <div style={{ borderTop: "1px solid var(--catfish-border)", paddingTop: "var(--space-3)", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13 }}>版式: <b>{s.custom_template ? "公司模板" : "内置版式"}</b></div>
        <div style={muted}>
          有自己的报价单 Excel 就导入当模板: 在要填的格子里写占位, 如 {"{{客户}}"} {"{{报价编号}}"} {"{{日期}}"} {"{{有效期至}}"}{" "}
          {"{{合计}}"} {"{{合计大写}}"}; 明细那一行写 {"{{序号}}"} {"{{商品}}"} {"{{规格}}"} {"{{数量}}"} {"{{单价}}"} {"{{金额}}"},
          有几条明细就往下复制几行。合计请用 {"{{合计}}"}, 不要用 SUM 公式 (插行后公式范围不会跟着变)。
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="chat-composer__secondary-button" disabled={busy} onClick={() => void importTemplate()}>
            {s.custom_template ? "换一个模板" : "导入公司模板"}
          </button>
          {s.custom_template && (
            <button type="button" className="chat-composer__secondary-button" disabled={busy}
              onClick={() => void run(salesQuoteTemplateClear, "已改回内置版式。")}>
              改回内置版式
            </button>
          )}
        </div>
      </div>
      <Status msg={msg} />
    </div>
  );
}
