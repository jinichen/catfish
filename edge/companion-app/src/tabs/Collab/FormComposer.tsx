/**
 * 发起填表 (10/17, docs/FORM-COLLECT-PLAN.md §2)。
 *
 * 字段两种来源: 选一个 Excel 模板 (第一行做表头, 类型自动推断, 导出按模板列顺序), 或者一行一行自己加。
 * 也可以直接跟小鲶说「把这张表发给 xx 填」—— 它走同一个 catfish_form, 发之前会先念给你确认。
 * 收件人: 输邮箱, 或按姓名 / 部门搜全公司通讯录 (中央只给 邮箱 / 姓名 / 部门)。
 */
import { useEffect, useState } from "react";

import { refreshForms } from "../../lib/formsStore";
import { salesPickXlsx } from "../../lib/tauri_sales";
import {
  formCreate, formDirectory, formFromExcel, formSend,
  type FieldDraft, type FieldType, type Person,
} from "../../lib/tauri_forms";
import { Overlay, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

const TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "文字" }, { value: "longtext", label: "长文字" }, { value: "number", label: "数字" },
  { value: "date", label: "日期" }, { value: "choice", label: "单选" }, { value: "multichoice", label: "多选" },
];

type Row = FieldDraft & { optionsText?: string };

const blankRow = (): Row => ({ label: "", type: "text", required: false });

function toFields(rows: Row[]): FieldDraft[] {
  return rows.filter((r) => r.label.trim()).map(({ optionsText, ...r }) => ({
    ...r,
    label: r.label.trim(),
    options: r.type === "choice" || r.type === "multichoice"
      ? (optionsText ?? (r.options ?? []).join("、")).split(/[、,，\n]/).map((s) => s.trim()).filter(Boolean)
      : undefined,
  }));
}

/** onCreated 的 notice: 发送失败的原因 —— 弹层马上要关, 得交给进度页接着显示, 不然员工以为发出去了。 */
export default function FormComposer({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (formId: string, notice?: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [deadline, setDeadline] = useState("");
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<Row[]>([blankRow()]);
  const [template, setTemplate] = useState<string | null>(null);
  const [recipients, setRecipients] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!q.trim()) { setPeople([]); return; }
    const t = setTimeout(() => {
      formDirectory(q.trim()).then((r) => setPeople(r.people)).catch((e) => setError(String(e)));
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const addRecipient = (email: string) => {
    const e = email.trim().toLowerCase();
    if (e && !recipients.includes(e)) setRecipients([...recipients, e]);
  };

  const fromExcel = async () => {
    setError(null);
    const path = await salesPickXlsx("选一个 Excel 模板 (第一行是表头)");
    if (!path) return;
    try {
      const r = await formFromExcel(path);
      setRows(r.fields.map((f) => ({ ...f, optionsText: (f.options ?? []).join("、") })));
      setTemplate(path);
      if (!title) setTitle(r.title);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const patch = (i: number, p: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));

  async function save(send: boolean) {
    setBusy(true);
    setError(null);
    try {
      const all = [...new Set([...recipients, ...typed.split(/[\s,;，；]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)])];
      const { form } = await formCreate({ title, fields: toFields(rows), deadline: deadline || null, note,
                                          recipients: all, template_path: template });
      let notice: string | undefined;
      if (send) {
        try {
          const r = await formSend(form.form_id);
          if (r.failed.length) {
            notice = `${r.failed.length} 人没发出去 (表已存好, 可以在这里再发): ${r.failed.map((f) => `${f.to} — ${f.error}`).join("; ")}`;
          }
        } catch (e) {
          notice = `表已存好, 但没发出去: ${e instanceof Error ? e.message : String(e)}`;
        }
      }
      void refreshForms();
      onCreated(form.form_id, notice);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const canSave = !busy && title.trim() && rows.some((r) => r.label.trim());
  const canSend = canSave && (recipients.length > 0 || typed.trim());

  return (
    <Overlay title="发起填表" onClose={onClose} width={760}>
      <span style={muted}>也可以直接跟小鲶说「把这张表发给安全部的人填, 周五前」。回答只回到你这台电脑, 中央只转交。</span>
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 8 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="表名, 如「10 月安全检查」" style={inputStyle} />
        <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} title="截止日" style={inputStyle} />
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="给填表人的说明 (可不填)" style={{ ...inputStyle, resize: "vertical" }} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong style={{ fontSize: 13 }}>要填的项</strong>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {template && <span style={muted}>来自模板, 导出按模板列顺序</span>}
          <Btn kind="ghost" onClick={fromExcel}>从 Excel 模板读表头</Btn>
        </div>
      </div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr auto auto", gap: 6, alignItems: "center" }}>
          <input value={r.label} onChange={(e) => patch(i, { label: e.target.value })} placeholder={`第 ${i + 1} 项名称`} style={inputStyle} />
          <select value={r.type} onChange={(e) => patch(i, { type: e.target.value as FieldType })} style={inputStyle}>
            {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          {r.type === "choice" || r.type === "multichoice" ? (
            <input value={r.optionsText ?? (r.options ?? []).join("、")} onChange={(e) => patch(i, { optionsText: e.target.value })}
                   placeholder="选项, 用顿号隔开" style={inputStyle} />
          ) : <span />}
          <label style={{ fontSize: 12, display: "flex", gap: 4, alignItems: "center", whiteSpace: "nowrap" }}>
            <input type="checkbox" checked={!!r.required} onChange={(e) => patch(i, { required: e.target.checked })} />必填
          </label>
          <Btn kind="ghost" onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : [blankRow()])}>删</Btn>
        </div>
      ))}
      <div><Btn kind="ghost" onClick={() => setRows([...rows, blankRow()])}>+ 加一项</Btn></div>

      <strong style={{ fontSize: 13 }}>发给谁</strong>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {recipients.map((e) => (
          <span key={e} style={{ fontSize: 12, padding: "2px 8px", border: "1px solid var(--catfish-border)", borderRadius: 12 }}>
            {e} <button type="button" aria-label={`去掉 ${e}`} onClick={() => setRecipients(recipients.filter((x) => x !== e))}
                        style={{ border: "none", background: "transparent", cursor: "pointer" }}>✕</button>
          </span>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按姓名 / 部门搜同事" style={inputStyle} />
        <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="或直接填邮箱, 多个用逗号隔开" style={inputStyle} />
      </div>
      {people.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 160, overflowY: "auto" }}>
          {people.map((p) => (
            <button key={p.email} type="button" onClick={() => addRecipient(p.email)} disabled={recipients.includes(p.email)}
                    style={{ textAlign: "left", fontSize: 12, padding: "4px 8px", border: "none", cursor: "pointer",
                             background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))", borderRadius: 4 }}>
              {p.name} · {p.department} · {p.email}{recipients.includes(p.email) ? " (已加)" : ""}
            </button>
          ))}
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={!canSave} onClick={() => save(false)}>先存着不发</Btn>
        <Btn kind="primary" disabled={!canSend} onClick={() => save(true)}>{busy ? "发送中…" : "发出去"}</Btn>
      </div>
    </Overlay>
  );
}
