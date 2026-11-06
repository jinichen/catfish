/**
 * 类型定义页里一类的编辑区 (11/07): 改名、提醒天数、属性 (加 / 改 / 隐藏 / 恢复 / 删)、行动 (同上)。
 * 每次只提交一处改动 (onChange → wiki_type_defs_apply), 内置的不能删只能隐藏, 自己加的随便删。
 */
import { useEffect, useState } from "react";
import { T } from "../../components/ModalShell";
import type { TypeDef, TypeDefAction, TypeDefChange, TypeDefProperty } from "../../lib/tauri_wiki_types";

const ORIGIN: Record<string, string> = { builtin: "内置", local: "我加的", "builtin+local": "内置 · 改过" };
const input = { padding: "6px 8px", border: `1px solid ${T.border}`, borderRadius: 6, fontSize: 13, boxSizing: "border-box" } as const;
const small = { border: 0, background: "transparent", color: T.cyanHover, cursor: "pointer", fontSize: 12, padding: "2px 4px" } as const;
const primary = { padding: "6px 12px", border: 0, borderRadius: 8, background: T.cyan, color: "#fff", cursor: "pointer", fontSize: 13 } as const;
const section = { margin: "14px 0 6px", fontSize: 14, fontWeight: 600 } as const;
const tag = (origin: string, hidden: boolean) => (
  <span style={{ fontSize: 11, color: hidden ? T.textTertiary : origin === "local" ? "#b45309" : T.textSecondary }}>
    {hidden ? "已隐藏" : ORIGIN[origin] ?? origin}
  </span>
);

type Apply = (change: TypeDefChange) => Promise<boolean>;

/** 内置行动的「要做的事」里写的是属性 key ({issuer}), 给人看 / 改时换成属性名 ({发证机构}); 两种写法执行时都认 */
export function keysToLabels(text: string, props: TypeDefProperty[]): string {
  const byKey = new Map(props.map((p) => [p.key, p.label]));
  return (text || "").replace(/\{([a-z_][a-z0-9_]*)\}/g, (m, k: string) => (byKey.has(k) ? `{${byKey.get(k)}}` : m));
}

function PropRow({ type, p, apply, busy }: { type: string; p: TypeDefProperty; apply: Apply; busy: boolean }) {
  const [edit, setEdit] = useState(false);
  const [label, setLabel] = useState(p.label);
  const [hint, setHint] = useState(p.hint ?? "");
  const [kind, setKind] = useState(p.type);
  const save = async () => {
    if (await apply({ op: "update_property", type, key: p.key, property: { label, hint, type: kind } })) setEdit(false);
  };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(90px, 140px) 64px minmax(0, 1fr) 76px auto", gap: 8, alignItems: "center", padding: "5px 0", borderBottom: `1px solid #ececef`, opacity: p.hidden ? 0.55 : 1 }}>
      {edit ? (
        <>
          <input style={input} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={20} aria-label="属性名" />
          <select style={input} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="属性类型">
            <option value="text">文本</option>
            <option value="date">日期</option>
          </select>
          <input style={input} value={hint} onChange={(e) => setHint(e.target.value)} maxLength={80} placeholder="说明 (可不填)" aria-label="说明" />
        </>
      ) : (
        <>
          <strong style={{ fontSize: 13 }}>{p.label}</strong>
          <span style={{ fontSize: 12, color: T.textSecondary }}>{p.type === "date" ? "日期" : "文本"}</span>
          <span style={{ fontSize: 12, color: T.textSecondary }}>{p.hint}</span>
        </>
      )}
      {tag(p.origin, p.hidden)}
      <span style={{ whiteSpace: "nowrap" }}>
        {edit ? (
          <>
            <button type="button" style={small} disabled={busy} onClick={() => void save()}>保存</button>
            <button type="button" style={small} onClick={() => setEdit(false)}>取消</button>
          </>
        ) : (
          <>
            {!p.hidden && <button type="button" style={small} onClick={() => setEdit(true)}>改</button>}
            {p.origin === "local" ? (
              <button type="button" style={small} disabled={busy} onClick={() => void apply({ op: "delete_property", type, key: p.key })}>删除</button>
            ) : (
              <button type="button" style={small} disabled={busy}
                onClick={() => void apply({ op: p.hidden ? "unhide_property" : "hide_property", type, key: p.key })}>
                {p.hidden ? "恢复" : "隐藏"}
              </button>
            )}
          </>
        )}
      </span>
    </div>
  );
}

function ActionForm({ props, initial, onSubmit, onCancel, busy }: {
  props: TypeDefProperty[];
  initial?: TypeDefAction;
  onSubmit: (a: Partial<TypeDefAction>) => Promise<boolean>;
  onCancel?: () => void;
  busy: boolean;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [instruction, setInstruction] = useState(keysToLabels(initial?.instruction ?? "", props));
  const [skill, setSkill] = useState(initial?.skill_path ?? "");
  const submit = async () => {
    if (await onSubmit({ label, instruction, skill_path: skill, description: instruction.slice(0, 60) }) && !initial) {
      setLabel("");
      setInstruction("");
      setSkill("");
    }
  };
  return (
    <div style={{ display: "grid", gap: 6, padding: "8px 0" }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(110px, 160px) minmax(0, 1fr)", gap: 8 }}>
        <input style={input} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={20} placeholder="行动名，如 续期准备" aria-label="行动名" />
        <input style={input} value={skill} onChange={(e) => setSkill(e.target.value)} placeholder="要用的技能 (可不填，例 personal/xxx)" aria-label="技能" />
      </div>
      <textarea style={{ ...input, minHeight: 64, fontFamily: "inherit" }} value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={1000}
        placeholder="要做的事：点了这个行动，小鲶按这句话做。可以写 {属性名}，会换成这条知识的属性值" aria-label="要做的事" />
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center", fontSize: 12, color: T.textSecondary }}>
        插入属性：
        {props.filter((p) => !p.hidden).map((p) => (
          <button key={p.key} type="button" style={{ ...small, border: `1px solid ${T.border}`, borderRadius: 6 }}
            onClick={() => setInstruction((s) => `${s}{${p.label}}`)}>{p.label}</button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" style={primary} disabled={busy || !label.trim() || !instruction.trim()} onClick={() => void submit()}>
          {initial ? "保存行动" : "加行动"}
        </button>
        {onCancel && <button type="button" style={{ ...primary, background: T.closeBg, color: T.text }} onClick={onCancel}>取消</button>}
      </div>
    </div>
  );
}

function ActionRow({ type, a, props, apply, busy }: { type: string; a: TypeDefAction; props: TypeDefProperty[]; apply: Apply; busy: boolean }) {
  const [edit, setEdit] = useState(false);
  if (edit) {
    return (
      <ActionForm props={props} initial={a} busy={busy} onCancel={() => setEdit(false)}
        onSubmit={async (patch) => {
          const ok = await apply({ op: "update_action", type, id: a.id, action: patch });
          if (ok) setEdit(false);
          return ok;
        }} />
    );
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(90px, 140px) minmax(0, 1fr) 76px auto", gap: 8, padding: "6px 0", borderBottom: `1px solid #ececef`, opacity: a.hidden ? 0.55 : 1 }}>
      <strong style={{ fontSize: 13 }}>{a.label}</strong>
      <span style={{ fontSize: 12, color: T.textSecondary, lineHeight: 1.5 }}>
        {keysToLabels(a.instruction ?? "", props)}
        {a.skill_path && <em style={{ display: "block", color: T.textTertiary }}>技能：{a.skill_path}</em>}
      </span>
      {tag(a.origin, a.hidden)}
      <span style={{ whiteSpace: "nowrap" }}>
        {!a.hidden && <button type="button" style={small} onClick={() => setEdit(true)}>改</button>}
        {a.origin === "local" ? (
          <button type="button" style={small} disabled={busy} onClick={() => void apply({ op: "delete_action", type, id: a.id })}>删除</button>
        ) : (
          <button type="button" style={small} disabled={busy}
            onClick={() => void apply({ op: a.hidden ? "unhide_action" : "hide_action", type, id: a.id })}>
            {a.hidden ? "恢复" : "隐藏"}
          </button>
        )}
      </span>
    </div>
  );
}

export default function WikiTypeDefEditor({ def, apply, busy }: { def: TypeDef; apply: Apply; busy: boolean }) {
  const [label, setLabel] = useState(def.label);
  const [days, setDays] = useState(String(def.status?.warn_days ?? ""));
  const [newProp, setNewProp] = useState({ label: "", type: "text", hint: "" });
  useEffect(() => {
    setLabel(def.label);
    setDays(String(def.status?.warn_days ?? ""));
  }, [def.key, def.label, def.status?.warn_days]);
  const fromLabel = def.properties.find((p) => p.key === def.status?.from)?.label;

  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input style={{ ...input, fontSize: 15, fontWeight: 600, width: 180 }} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={20} aria-label="类型名" />
        {label.trim() && label !== def.label && (
          <button type="button" style={small} disabled={busy} onClick={() => void apply({ op: "rename_type", type: def.key, label: label.trim() })}>改名</button>
        )}
        <span style={{ fontSize: 12, color: T.textTertiary }}>知识里的细分类型：{def.key} · {def.origin === "builtin" ? "内置" : "我加的"}</span>
      </div>
      {def.status && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, fontSize: 13 }}>
          「{fromLabel ?? def.status.from}」前
          <input style={{ ...input, width: 70 }} type="number" min={1} max={1000} value={days} onChange={(e) => setDays(e.target.value)} aria-label="提前天数" />
          天算「快到了」
          {days && Number(days) !== def.status.warn_days && (
            <button type="button" style={small} disabled={busy} onClick={() => void apply({ op: "set_warn_days", type: def.key, warn_days: Number(days) })}>保存</button>
          )}
        </div>
      )}

      <h4 style={section}>属性</h4>
      {def.properties.length === 0 && <p style={{ fontSize: 12, color: T.textTertiary }}>还没有属性。</p>}
      {def.properties.map((p) => <PropRow key={p.key} type={def.key} p={p} apply={apply} busy={busy} />)}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(90px, 140px) 64px minmax(0, 1fr) auto", gap: 8, padding: "8px 0" }}>
        <input style={input} value={newProp.label} onChange={(e) => setNewProp({ ...newProp, label: e.target.value })} maxLength={20} placeholder="新属性名，如 投标用途" aria-label="新属性名" />
        <select style={input} value={newProp.type} onChange={(e) => setNewProp({ ...newProp, type: e.target.value })} aria-label="新属性类型">
          <option value="text">文本</option>
          <option value="date">日期</option>
        </select>
        <input style={input} value={newProp.hint} onChange={(e) => setNewProp({ ...newProp, hint: e.target.value })} maxLength={80} placeholder="说明 (可不填，小鲶抽属性时会参考)" aria-label="新属性说明" />
        <button type="button" style={primary} disabled={busy || !newProp.label.trim()}
          onClick={async () => {
            if (await apply({ op: "add_property", type: def.key, property: { label: newProp.label.trim(), type: newProp.type, hint: newProp.hint } })) {
              setNewProp({ label: "", type: "text", hint: "" });
            }
          }}>加属性</button>
      </div>

      <h4 style={section}>行动 <small style={{ fontWeight: 400, color: T.textTertiary }}>这一类的每条知识都会有，点了交给小鲶</small></h4>
      {def.actions.length === 0 && <p style={{ fontSize: 12, color: T.textTertiary }}>还没有行动。</p>}
      {def.actions.map((a) => <ActionRow key={a.id} type={def.key} a={a} props={def.properties} apply={apply} busy={busy} />)}
      <ActionForm props={def.properties} busy={busy} onSubmit={(a) => apply({ op: "add_action", type: def.key, action: a })} />
    </div>
  );
}
