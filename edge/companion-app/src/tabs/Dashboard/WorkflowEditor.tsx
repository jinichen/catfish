/**
 * 工作流的步骤卡片编辑器 (10/17, docs/WORKFLOW-SKILL-PLAN.md §4.3)。
 *
 * 一列卡片, 不做拖拽连线: 加一步 (从积木目录挑) / 上下移 / 删; 每个参数可以填固定值、选工作流参数,
 * 或从下拉里选**前面某一步声明过的输出** —— 下拉里没有的东西选不到, 写不出指向不存在的数据。
 * 对外的积木会停下等确认, 勾选框锁定; 别的步骤也可以自己勾「这步要我确认」。
 */
import { useState, type CSSProperties } from "react";

import type { CatalogAction, CatalogSkill, ReturnType, Workflow, WorkflowStep } from "../../lib/tauri_workflows";
import { Btn, inputStyle } from "../Collab/roomLinkUi";

const RETURN_TYPES: ReturnType[] = ["text", "number", "list", "bool", "date"];
const LEVEL_LABEL = { read: "查", local: "写 (本机)", outward: "对外 · 会停下等你确认" } as const;
const small: CSSProperties = { ...inputStyle, padding: "4px 8px", fontSize: 12 };

export interface Catalog {
  actions: CatalogAction[];
  skills: CatalogSkill[];
}

function kindOf(s: WorkflowStep): "skill" | "action" | "xiaonian" {
  return s.skill !== undefined ? "skill" : s.action !== undefined ? "action" : "xiaonian";
}

/** 这一步之前能取的值: 工作流参数 + 前面每一步的输出 (+ for_each 的 item)。 */
export function sourcesBefore(wf: Workflow, index: number, cat: Catalog): { value: string; label: string }[] {
  const out = Object.entries(wf.params ?? {}).map(([k, p]) => ({ value: `\${params.${k}}`, label: `参数 · ${p.label || k}` }));
  wf.steps.slice(0, index).forEach((s, i) => {
    const name = `第 ${i + 1} 步 (${s.id})`;
    let fields: string[] = [];
    if (s.for_each) fields = ["items", "count", "files"];
    else if (s.action) fields = [...(cat.actions.find((a) => a.name === s.action)?.outputs ?? []), "files"];
    else if (s.skill) fields = ["files", "path"];
    else fields = Object.keys(s.returns ?? {});
    for (const f of [...new Set(fields)]) out.push({ value: `\${steps.${s.id}.${f}}`, label: `${name} · ${f}` });
    if ((s.action || s.skill) && fields.includes("files")) out.push({ value: `\${steps.${s.id}.files[0]}`, label: `${name} · 第一个文件` });
  });
  if (wf.steps[index]?.for_each) out.push({ value: "${item}", label: "当前这一项 (item)" });
  return out;
}

function ValueField({ value, onChange, sources }: { value: unknown; onChange: (v: unknown) => void; sources: { value: string; label: string }[] }) {
  const text = typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
  const picked = sources.find((s) => s.value === text);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 160px", gap: 6 }}>
      <input value={picked ? picked.label : text} disabled={!!picked} onChange={(e) => onChange(e.target.value)}
             placeholder="固定值, 或右边选从哪来" style={small} />
      <select value={picked ? picked.value : ""} onChange={(e) => onChange(e.target.value || "")} style={small}>
        <option value="">固定值</option>
        {sources.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    </div>
  );
}

function StepCard({ wf, i, cat, onChange, onMove, onRemove }: {
  wf: Workflow; i: number; cat: Catalog;
  onChange: (s: WorkflowStep) => void; onMove: (d: -1 | 1) => void; onRemove: () => void;
}) {
  const s = wf.steps[i];
  const kind = kindOf(s);
  const action = s.action ? cat.actions.find((a) => a.name === s.action) : undefined;
  const skill = s.skill ? cat.skills.find((k) => k.skill_path === s.skill) : undefined;
  const sources = sourcesBefore(wf, i, cat);
  const listSources = sourcesBefore(wf, i, cat).filter((x) => /items|matches|ids|tasks|files|list|results/.test(x.value) || x.label.includes("list"));
  const paramNames = action ? action.params : skill ? skill.params.map((p) => p.name).filter((n) => n !== "max_captcha_retry") : [];
  const outward = action?.level === "outward";
  const set = (patch: Partial<WorkflowStep>) => onChange({ ...s, ...patch });
  const setParam = (k: string, v: unknown) => {
    const params = { ...(s.params ?? {}) };
    if (v === "" || v === undefined) delete params[k];
    else params[k] = v;
    set({ params });
  };
  return (
    <div style={{ border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm, 6px)", padding: 10,
                  display: "flex", flexDirection: "column", gap: 6, borderLeft: outward ? "3px solid var(--status-warn, #d97706)" : undefined }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <strong style={{ fontSize: 13 }}>第 {i + 1} 步</strong>
        <input value={s.id} onChange={(e) => set({ id: e.target.value })} title="步骤 id (后面的步骤用它取值)" style={{ ...small, width: 110 }} />
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)", flex: 1 }}>
          {kind === "action" && (action ? `${action.label} · ${LEVEL_LABEL[action.level]}` : `未知积木 ${s.action}`)}
          {kind === "skill" && `我的技能 · ${s.skill}`}
          {kind === "xiaonian" && "小鲶判断 (只看给它的数据, 不能调工具)"}
        </span>
        <Btn kind="ghost" disabled={i === 0} onClick={() => onMove(-1)}>↑</Btn>
        <Btn kind="ghost" disabled={i === wf.steps.length - 1} onClick={() => onMove(1)}>↓</Btn>
        <Btn kind="ghost" onClick={onRemove}>删</Btn>
      </div>
      {kind === "xiaonian" ? (
        <>
          <textarea rows={2} value={s.xiaonian ?? ""} onChange={(e) => set({ xiaonian: e.target.value })}
                    placeholder="让小鲶做什么, 例: 从这些邮件里挑出跟资质申请有关的" style={{ ...small, resize: "vertical" }} />
          <label style={{ fontSize: 12 }}>给它看的数据</label>
          <ValueField value={s.input ?? ""} onChange={(v) => set({ input: (v as string) || undefined })} sources={sources} />
          <label style={{ fontSize: 12 }}>交回的字段</label>
          {Object.entries(s.returns ?? {}).map(([k, t]) => (
            <div key={k} style={{ display: "grid", gridTemplateColumns: "1fr 120px auto", gap: 6 }}>
              <input value={k} readOnly style={small} />
              <select value={t} onChange={(e) => set({ returns: { ...s.returns, [k]: e.target.value as ReturnType } })} style={small}>
                {RETURN_TYPES.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
              <Btn kind="ghost" onClick={() => { const r = { ...s.returns }; delete r[k]; set({ returns: r }); }}>删</Btn>
            </div>
          ))}
          <AddName label="+ 交回字段" placeholder="字段名, 小写字母 (例 summary)"
                   onAdd={(name) => set({ returns: { ...(s.returns ?? {}), [name]: "text" } })} />
        </>
      ) : (
        <>
          {paramNames.map((k) => (
            <div key={k} style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 6, alignItems: "center" }}>
              <span style={{ fontSize: 12 }}>{k}{action?.required.includes(k) || skill?.params.find((p) => p.name === k)?.required ? " *" : ""}</span>
              <ValueField value={s.params?.[k]} onChange={(v) => setParam(k, v)} sources={sources} />
            </div>
          ))}
          <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 6, alignItems: "center" }}>
            <span style={{ fontSize: 12 }} title="对前面某一步交回的列表, 每一项做一次这一步">每一项做一次</span>
            <select value={s.for_each ?? ""} onChange={(e) => set({ for_each: e.target.value || undefined })} style={small}>
              <option value="">(只做一次)</option>
              {listSources.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
            </select>
          </div>
        </>
      )}
      <div style={{ display: "flex", gap: 14, fontSize: 12, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={outward || !!s.confirm} disabled={outward}
                 onChange={(e) => set({ confirm: e.target.checked || undefined })} />
          这步要我确认{outward ? " (对外, 一定会停)" : ""}
        </label>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }} title="结果少于这么多项就算这一步没做成">
          至少几项
          <input type="number" min={0} value={String(s.expect?.count_min ?? "")} style={{ ...small, width: 60 }}
                 onChange={(e) => set({ expect: { ...(s.expect ?? {}), count_min: e.target.value ? Number(e.target.value) : undefined } })} />
        </label>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }} title="拿到的文件少于这么多个就算没做成">
          至少几个文件
          <input type="number" min={0} value={String(s.expect?.files_min ?? "")} style={{ ...small, width: 60 }}
                 onChange={(e) => set({ expect: { ...(s.expect ?? {}), files_min: e.target.value ? Number(e.target.value) : undefined } })} />
        </label>
      </div>
    </div>
  );
}

/** 加一个名字 (交回字段 / 参数)。Tauri WebView 里 window.prompt / confirm 静默返回 null, 不能用 (见 CronJobsCard 7/6 那次)。 */
function AddName({ label, placeholder, onAdd }: { label: string; placeholder: string; onAdd: (name: string) => void }) {
  const [v, setV] = useState("");
  const ok = /^[a-z][a-z0-9_]{0,30}$/.test(v);
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <input value={v} onChange={(e) => setV(e.target.value.trim())} placeholder={placeholder} style={{ ...small, width: 200 }} />
      <Btn kind="ghost" disabled={!ok} onClick={() => { onAdd(v); setV(""); }}>{label}</Btn>
    </div>
  );
}

function newId(wf: Workflow, base: string): string {
  const b = base.replace(/[^a-z0-9_]/g, "_").replace(/^_+/, "").slice(0, 20) || "step";
  let id = /^[a-z]/.test(b) ? b : `s_${b}`;
  let n = 2;
  while (wf.steps.some((s) => s.id === id)) id = `${b}_${n++}`;
  return id;
}

export default function WorkflowEditor({ wf, cat, onChange }: { wf: Workflow; cat: Catalog; onChange: (wf: Workflow) => void }) {
  const steps = wf.steps;
  const setStep = (i: number, s: WorkflowStep) => onChange({ ...wf, steps: steps.map((x, j) => (j === i ? s : x)) });
  const move = (i: number, d: -1 | 1) => {
    const next = [...steps];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange({ ...wf, steps: next });
  };
  const add = (value: string) => {
    if (!value) return;
    const [kind, name] = value.split(":", 2);
    const step: WorkflowStep = kind === "action" ? { id: newId(wf, name), action: name }
      : kind === "skill" ? { id: newId(wf, name.split("/").pop() ?? "skill"), skill: name }
      : { id: newId(wf, "judge"), xiaonian: "", returns: { summary: "text" } };
    onChange({ ...wf, steps: [...steps, step] });
  };
  const groups = ["查", "写", "发"];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <strong style={{ fontSize: 13 }}>参数 (每次跑可以改的)</strong>
        {Object.entries(wf.params ?? {}).map(([k, p]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "110px 1fr 1fr auto", gap: 6 }}>
            <input value={k} readOnly style={small} />
            <input value={p.label ?? ""} placeholder="叫什么" style={small}
                   onChange={(e) => onChange({ ...wf, params: { ...wf.params, [k]: { ...p, label: e.target.value } } })} />
            <input value={p.default == null ? "" : String(p.default)} placeholder="默认值 (日期可填 today)" style={small}
                   onChange={(e) => onChange({ ...wf, params: { ...wf.params, [k]: { ...p, default: e.target.value } } })} />
            <Btn kind="ghost" onClick={() => { const ps = { ...wf.params }; delete ps[k]; onChange({ ...wf, params: ps }); }}>删</Btn>
          </div>
        ))}
        <AddName label="+ 参数" placeholder="参数名, 小写字母 (例 keyword)"
                 onAdd={(name) => onChange({ ...wf, params: { ...(wf.params ?? {}), [name]: { type: "text", label: name } } })} />
      </div>
      {steps.map((_, i) => (
        <StepCard key={i} wf={wf} i={i} cat={cat} onChange={(s) => setStep(i, s)} onMove={(d) => move(i, d)}
                  onRemove={() => onChange({ ...wf, steps: steps.filter((__, j) => j !== i) })} />
      ))}
      <select value="" onChange={(e) => add(e.target.value)} style={inputStyle} aria-label="加一步">
        <option value="">+ 加一步…</option>
        {groups.map((g) => (
          <optgroup key={g} label={g === "发" ? "发 (对外, 会停下等你确认)" : g}>
            {cat.actions.filter((a) => a.group === g).map((a) => <option key={a.name} value={`action:${a.name}`}>{a.label}</option>)}
          </optgroup>
        ))}
        <optgroup label="我的技能 (教过的)">
          {cat.skills.map((k) => <option key={k.skill_path} value={`skill:${k.skill_path}`}>{k.skill_path}</option>)}
        </optgroup>
        <optgroup label="判断">
          <option value="xiaonian:">让小鲶判断 / 总结</option>
        </optgroup>
      </select>
    </div>
  );
}
