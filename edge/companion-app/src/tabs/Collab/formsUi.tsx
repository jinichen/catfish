/**
 * 一起填表的小零件 (10/17): 弹层、按字段类型的输入框、状态小标签。
 * 跟 roomLinkUi 一样用 inline style (项目里没有全局 .btn / .input)。
 */
import type { ReactNode } from "react";

import type { FieldValues, FormField } from "../../lib/tauri_forms";
import { inputStyle } from "./roomLinkUi";

export function Overlay({ title, onClose, children, width = 640 }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  return (
    <div
      role="dialog"
      aria-label={title}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", zIndex: 100,
               display: "flex", alignItems: "flex-start", justifyContent: "center", overflowY: "auto", padding: "40px 16px" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{ background: "var(--catfish-bg-elevated)", borderRadius: "var(--radius-md)", boxShadow: "var(--shadow-md)",
                    width: "100%", maxWidth: width, padding: "var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>{title}</h3>
          <button type="button" aria-label="关闭" onClick={onClose}
                  style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 16, color: "var(--catfish-text-muted)" }}>✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

const TONE: Record<string, string> = {
  待填: "var(--catfish-cyan)", 草稿: "var(--catfish-cyan)", 未提交: "var(--catfish-text-muted)", 未发: "var(--catfish-text-muted)",
  已提交: "var(--status-ok, #16a34a)", 逾期: "var(--status-warn, #d97706)", 不填: "var(--status-error, #dc2626)",
  已结束: "var(--catfish-text-muted)", 收集中: "var(--catfish-cyan)", 没发出: "var(--catfish-text-muted)",
};

export function StateTag({ state }: { state: string }) {
  return <span style={{ fontSize: 11, color: TONE[state] ?? "var(--catfish-text-muted)", whiteSpace: "nowrap" }}>{state}</span>;
}

export const muted = { fontSize: 11, color: "var(--catfish-text-muted)" } as const;

/** 一个字段的输入框。readOnly 用在已提交 / 已结束的表。 */
export function FieldInput({ field, values, onChange, readOnly, source }: {
  field: FormField;
  values: FieldValues;
  onChange: (key: string, v: string | string[]) => void;
  readOnly?: boolean;
  source?: string;
}) {
  const v = values[field.key];
  const id = `form-field-${field.key}`;
  let input: ReactNode;
  if (field.type === "longtext") {
    input = <textarea id={id} rows={3} value={String(v ?? "")} disabled={readOnly}
                      onChange={(e) => onChange(field.key, e.target.value)} style={{ ...inputStyle, resize: "vertical" }} />;
  } else if (field.type === "choice") {
    input = (
      <select id={id} value={String(v ?? "")} disabled={readOnly} onChange={(e) => onChange(field.key, e.target.value)} style={inputStyle}>
        <option value="">请选择</option>
        {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (field.type === "multichoice") {
    const picked = Array.isArray(v) ? v : [];
    input = (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {(field.options ?? []).map((o) => (
          <label key={o} style={{ fontSize: 13, display: "flex", gap: 4, alignItems: "center" }}>
            <input type="checkbox" checked={picked.includes(o)} disabled={readOnly}
                   onChange={(e) => onChange(field.key, e.target.checked ? [...picked, o] : picked.filter((x) => x !== o))} />
            {o}
          </label>
        ))}
      </div>
    );
  } else {
    input = <input id={id} type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
                   value={String(v ?? "")} disabled={readOnly} onChange={(e) => onChange(field.key, e.target.value)} style={inputStyle} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label htmlFor={id} style={{ fontSize: 13, fontWeight: 500 }}>
        {field.label}{field.required && <span style={{ color: "var(--status-error, #dc2626)" }}> *</span>}
      </label>
      {field.hint && <span style={muted}>{field.hint}</span>}
      {input}
      {source && <span style={muted}>小鲶找到的出处: {source}</span>}
    </div>
  );
}
