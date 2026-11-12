/**
 * 改纪要 (11/12 鸿波「内容是不是还应该可以修正」): 人名听错、结论记反, 不用整份重新生成。
 *
 * 标准纪要: 摘要 / 决议 / 待办 (事项 · 负责人 · 截止) / 待定问题 / 上次遗留 逐项改、删、加;
 * 存了以后 minutes.md 按改后的重新排 (tool-bridge meeting_minutes_edit.py)。
 * 按文字模版写的那份: 直接改全文; 待办照样逐条改 (「加入任务库」用的是待办这一份)。
 */
import { useState } from "react";

import { meetingMinutesSave, type ActionItem, type Minutes, type MinutesFields } from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";

const linkBtn = { border: "none", background: "transparent", cursor: "pointer", padding: "0 4px", fontSize: 12,
                  color: "var(--catfish-text-muted)" } as const;
const area = { ...inputStyle, resize: "vertical" as const, lineHeight: 1.6, fontFamily: "inherit", fontSize: 13 };
const rowsFor = (t: string) => Math.min(5, Math.max(1, Math.ceil([...t].length / 46)));

function ListEdit({ label, items, onChange }: { label: string; items: string[]; onChange: (v: string[]) => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontWeight: 600 }}>{label}</div>
      {items.map((x, i) => (
        <div key={i} style={{ display: "flex", gap: 4, alignItems: "flex-start" }}>
          {/* 决议一条常常两三行, 单行框看不全 —— 按字数给行数 */}
          <textarea aria-label={`${label} ${i + 1}`} value={x} rows={rowsFor(x)} style={{ ...area, flex: 1 }}
                    onChange={(e) => onChange(items.map((y, j) => (j === i ? e.target.value : y)))} />
          <button type="button" style={linkBtn} title="删掉这条" onClick={() => onChange(items.filter((_, j) => j !== i))}>删</button>
        </div>
      ))}
      <button type="button" style={{ ...linkBtn, alignSelf: "flex-start", color: "var(--catfish-cyan)", padding: 0 }}
              onClick={() => onChange([...items, ""])}>＋ 加一条{label}</button>
    </div>
  );
}

function ItemsEdit({ items, onChange }: { items: ActionItem[]; onChange: (v: ActionItem[]) => void }) {
  const set = (i: number, patch: Partial<ActionItem>) => onChange(items.map((y, j) => (j === i ? { ...y, ...patch } : y)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontWeight: 600 }}>待办</div>
      {items.map((it, i) => (
        <div key={i} style={{ display: "flex", gap: 4, alignItems: "flex-start", flexWrap: "wrap" }}>
          <textarea aria-label={`待办 ${i + 1}`} value={it.task} placeholder="事项" rows={rowsFor(it.task)}
                    style={{ ...area, flex: "1 1 220px" }} onChange={(e) => set(i, { task: e.target.value })} />
          <input aria-label={`待办 ${i + 1} 负责人`} value={it.owner} placeholder="负责人" style={{ ...inputStyle, width: 96 }}
                 onChange={(e) => set(i, { owner: e.target.value })} />
          <input aria-label={`待办 ${i + 1} 截止`} type="date" value={it.due} style={{ ...inputStyle, width: 140 }}
                 onChange={(e) => set(i, { due: e.target.value })} />
          <button type="button" style={linkBtn} title="删掉这条" onClick={() => onChange(items.filter((_, j) => j !== i))}>删</button>
        </div>
      ))}
      <button type="button" style={{ ...linkBtn, alignSelf: "flex-start", color: "var(--catfish-cyan)", padding: 0 }}
              onClick={() => onChange([...items, { task: "", owner: "", due: "" }])}>＋ 加一条待办</button>
    </div>
  );
}

export default function MinutesEditor({ meetingId, minutes, markdownView, onCancel, onSaved }: {
  meetingId: string; minutes: Minutes; markdownView: boolean; onCancel: () => void; onSaved: () => void;
}) {
  const m = minutes.json;
  const [summary, setSummary] = useState(m.summary);
  const [decisions, setDecisions] = useState(m.decisions);
  const [items, setItems] = useState(m.action_items);
  const [questions, setQuestions] = useState(m.open_questions);
  const [carried, setCarried] = useState(m.carried_over ?? []);
  const [markdown, setMarkdown] = useState(minutes.markdown);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    const clean = (xs: string[]) => xs.map((x) => x.trim()).filter(Boolean);
    const cleanItems = items.filter((it) => it.task.trim()).map((it) => ({ ...it, task: it.task.trim(), owner: it.owner.trim() }));
    const fields: MinutesFields = markdownView
      ? { action_items: cleanItems }
      : { summary: summary.trim(), decisions: clean(decisions), action_items: cleanItems,
          open_questions: clean(questions), carried_over: clean(carried) };
    try {
      await meetingMinutesSave(meetingId, fields, markdownView ? markdown : null);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)", fontSize: 13 }}>
      {markdownView ? (
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontWeight: 600 }}>纪要全文</span>
          <textarea aria-label="纪要全文" value={markdown} rows={16} onChange={(e) => setMarkdown(e.target.value)} style={area} />
        </label>
      ) : (
        <>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontWeight: 600 }}>摘要</span>
            <textarea aria-label="摘要" value={summary} rows={5} onChange={(e) => setSummary(e.target.value)} style={area} />
          </label>
          <ListEdit label="决议" items={decisions} onChange={setDecisions} />
        </>
      )}
      <ItemsEdit items={items} onChange={setItems} />
      {!markdownView && <ListEdit label="待定问题" items={questions} onChange={setQuestions} />}
      {!markdownView && carried.length > 0 && <ListEdit label="上次遗留事项进展" items={carried} onChange={setCarried} />}
      <div style={{ display: "flex", gap: "var(--space-2)", justifyContent: "flex-end", alignItems: "center",
                    borderTop: "1px solid var(--catfish-border)", paddingTop: "var(--space-3)" }}>
        <span style={{ flex: 1, fontSize: 12, color: "var(--catfish-text-muted)" }}>
          存了以后纪要里写「已人工核对修改」; 再点「重新生成」会先提醒你, 不会悄悄覆盖。
        </span>
        <Btn kind="ghost" disabled={saving} onClick={onCancel}>取消</Btn>
        <Btn kind="primary" disabled={saving || (markdownView && !markdown.trim())} onClick={() => void save()}>
          {saving ? "保存中…" : "保存"}
        </Btn>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
