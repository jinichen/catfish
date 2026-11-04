/**
 * 多阶段任务 (11/4, docs/COLLEAGUE-TASK-PLAN.md §3): 一段一段写「做什么 + 谁来做」。
 * 一段里选几个人就是几个人同时做 (并行); 每段做完结果先回到你这里, 你核对 (可以改) 后放行才交给下一段的人。
 */
import { useCallback, useState } from "react";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { pipelineCreate } from "../../lib/tauri_colleague_tasks";
import { Overlay, muted } from "./formsUi";
import RecipientPicker, { allRecipients } from "./RecipientPicker";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import { MAX_PEOPLE, TEXT_MAX } from "./TaskComposer";

const MAX_STAGES = 8;

interface Row { name: string; text: string; picked: string[]; typed: string }

const blank = (): Row => ({ name: "", text: "", picked: [], typed: "" });

export default function PipelineComposer({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (pipelineId: string, notice?: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [rows, setRows] = useState<Row[]>([blank(), blank()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onPickError = useCallback((m: string) => setError(m), []);
  const patch = (i: number, p: Partial<Row>) => setRows((old) => old.map((r, j) => (j === i ? { ...r, ...p } : r)));

  const problems = rows.map((r, i) => {
    const n = allRecipients(r.picked, r.typed).length;
    if (!r.text.trim()) return `第 ${i + 1} 段要写做什么`;
    if (r.text.length > TEXT_MAX) return `第 ${i + 1} 段太长`;
    if (!n) return `第 ${i + 1} 段要选谁来做`;
    if (n > MAX_PEOPLE) return `第 ${i + 1} 段最多 ${MAX_PEOPLE} 人`;
    return null;
  }).filter(Boolean);
  const canSend = !busy && title.trim().length > 0 && problems.length === 0;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const r = await pipelineCreate({
        title,
        stages: rows.map((x) => ({ name: x.name, text: x.text, recipients: allRecipients(x.picked, x.typed) })),
      });
      void refreshTasks();
      onCreated(r.pipeline.pipeline_id, r.failed.length
        ? `第一段有 ${r.failed.length} 人没发出去 (在那一段里可以再发): ${r.failed.map((f) => `${f.to} — ${f.error}`).join("; ")}`
        : undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title="多阶段任务" onClose={onClose} width={760}>
      <span style={muted}>
        一段一段做: 先发第一段; 每段的结果先回到你这里, 你核对 (可以删改) 后点「放行」才交给下一段的人 —— 上一段的东西不会绕过你流到下一个人。
        一段里选几个人就是同时做, 都交回 (或你不等了) 再放行。
      </span>
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="流程名字, 如「本周客户周报」" style={inputStyle} />
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10, border: "1px solid var(--catfish-border)", borderRadius: 6 }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center" }}>
            <strong style={{ fontSize: 13 }}>第 {i + 1} 段</strong>
            <input value={r.name} onChange={(e) => patch(i, { name: e.target.value })} placeholder="这一段叫什么 (可不填), 如「收集」" style={inputStyle} />
            {rows.length > 2 && <Btn kind="ghost" onClick={() => setRows(rows.filter((_, j) => j !== i))}>删</Btn>}
          </div>
          <textarea value={r.text} onChange={(e) => patch(i, { text: e.target.value })} rows={2} maxLength={TEXT_MAX}
                    placeholder={i === 0 ? "做什么, 如「把你负责客户本周的拜访记录整理出来」" : "做什么 (会附上你放行的上一段结果), 如「根据大家的记录写一段分析」"}
                    style={{ ...inputStyle, resize: "vertical" }} />
          <RecipientPicker picked={r.picked} onPicked={(v) => patch(i, { picked: v })} typed={r.typed}
                           onTyped={(v) => patch(i, { typed: v })} onError={onPickError} />
        </div>
      ))}
      {rows.length < MAX_STAGES && <div><Btn kind="ghost" onClick={() => setRows([...rows, blank()])}>+ 加一段</Btn></div>}
      {problems.length > 0 && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>{problems[0]}</span>}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={!canSend} onClick={send}>{busy ? "发送中…" : "发出第一段"}</Btn>
      </div>
    </Overlay>
  );
}
