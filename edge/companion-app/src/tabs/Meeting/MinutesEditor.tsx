/**
 * 改纪要 (11/12 鸿波「内容是不是还应该可以修正」): 人名听错、结论记反, 不用整份重新生成。
 *
 * 标准纪要: 摘要 / 决议 / 待办 (事项 · 负责人 · 截止) / 待定问题 / 上次遗留 逐项改、删、加;
 * 存了以后 minutes.md 按改后的重新排 (tool-bridge meeting_minutes_edit.py)。
 * 按文字模版写的那份: 直接改全文; 待办照样逐条改 (「加入任务库」用的是待办这一份)。
 *
 * 11/12 第二版 (鸿波「UI 是不是应该优化, 现在的感觉太局促了」): 第一版在右栏里就地改 —— 右栏不到 500px,
 * 输入框按字数估行数估少了, 字被截在框里要滚; 待办的日期挤到下一行。现在:
 *   · 在一个宽的编辑框里改 (最宽 960px), 标题在上、保存按钮钉在底下;
 *   · 每个输入框随内容长高, 框里不出滚动条;
 *   · 待办一条一张小卡: 事项占满一行, 负责人 / 截止 / 删除在下面一行。
 * 点框外面不关 (改了一半误点就全丢了), 只有「取消」「保存」。
 */
import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { meetingMinutesSave, type ActionItem, type Minutes, type MinutesFields } from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";

const fieldStyle: CSSProperties = {
  ...inputStyle, width: "100%", boxSizing: "border-box", resize: "none", overflow: "hidden",
  lineHeight: 1.7, fontFamily: "inherit", fontSize: 14, padding: "8px 10px",
};
const removeBtn: CSSProperties = {
  border: "1px solid var(--catfish-border)", background: "transparent", cursor: "pointer", borderRadius: 6,
  padding: "4px 10px", fontSize: 12, color: "var(--catfish-text-muted)", whiteSpace: "nowrap", flexShrink: 0,
};
const addBtn: CSSProperties = {
  border: "1px dashed var(--catfish-border)", background: "transparent", cursor: "pointer", borderRadius: 6,
  padding: "6px 12px", fontSize: 13, color: "var(--catfish-cyan)", alignSelf: "flex-start",
};
const sectionTitle: CSSProperties = { fontWeight: 600, fontSize: 14, display: "flex", alignItems: "baseline", gap: 8 };
const countStyle: CSSProperties = { fontWeight: 400, fontSize: 12, color: "var(--catfish-text-muted)" };

/** 随内容长高的输入框 (框里不出滚动条) */
function AutoText({ value, onChange, label, placeholder, minRows = 1, autoFocus }: {
  value: string; onChange: (v: string) => void; label: string; placeholder?: string; minRows?: number; autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return (
    <textarea ref={ref} aria-label={label} value={value} rows={minRows} placeholder={placeholder} autoFocus={autoFocus}
              onChange={(e) => onChange(e.target.value)} style={fieldStyle} />
  );
}

function ListEdit({ label, items, onChange }: { label: string; items: string[]; onChange: (v: string[]) => void }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={sectionTitle}>{label}<span style={countStyle}>{items.length} 条</span></div>
      {items.map((x, i) => (
        <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <span style={{ ...countStyle, width: 18, paddingTop: 10, textAlign: "right", flexShrink: 0 }}>{i + 1}.</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <AutoText label={`${label} ${i + 1}`} value={x} onChange={(v) => onChange(items.map((y, j) => (j === i ? v : y)))} />
          </div>
          <button type="button" style={{ ...removeBtn, marginTop: 6 }} aria-label={`删掉${label} ${i + 1}`}
                  onClick={() => onChange(items.filter((_, j) => j !== i))}>删除</button>
        </div>
      ))}
      <button type="button" style={addBtn} onClick={() => onChange([...items, ""])}>＋ 加一条{label}</button>
    </section>
  );
}

function ItemsEdit({ items, onChange }: { items: ActionItem[]; onChange: (v: ActionItem[]) => void }) {
  const set = (i: number, patch: Partial<ActionItem>) => onChange(items.map((y, j) => (j === i ? { ...y, ...patch } : y)));
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={sectionTitle}>待办<span style={countStyle}>{items.length} 条 · 负责人、截止日期可不填</span></div>
      {items.map((it, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10, borderRadius: 8,
                              border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)" }}>
          <AutoText label={`待办 ${i + 1}`} value={it.task} placeholder="要做的事" onChange={(v) => set(i, { task: v })} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--catfish-text-muted)" }}>
              负责人
              <input aria-label={`待办 ${i + 1} 负责人`} value={it.owner} placeholder="谁来做"
                     style={{ ...inputStyle, width: 150, fontSize: 13 }} onChange={(e) => set(i, { owner: e.target.value })} />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--catfish-text-muted)" }}>
              截止
              <input aria-label={`待办 ${i + 1} 截止`} type="date" value={it.due}
                     style={{ ...inputStyle, width: 150, fontSize: 13 }} onChange={(e) => set(i, { due: e.target.value })} />
            </label>
            <span style={{ flex: 1 }} />
            <button type="button" style={removeBtn} aria-label={`删掉待办 ${i + 1}`}
                    onClick={() => onChange(items.filter((_, j) => j !== i))}>删除</button>
          </div>
        </div>
      ))}
      <button type="button" style={addBtn} onClick={() => onChange([...items, { task: "", owner: "", due: "" }])}>＋ 加一条待办</button>
    </section>
  );
}

export default function MinutesEditor({ meetingId, title, minutes, markdownView, onCancel, onSaved }: {
  meetingId: string; title: string; minutes: Minutes; markdownView: boolean; onCancel: () => void; onSaved: () => void;
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
      setSaving(false);
    }
  }

  return (
    <div role="dialog" aria-label="编辑纪要"
         style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.45)",
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ width: "min(960px, 100%)", maxHeight: "92vh", display: "flex", flexDirection: "column",
                    background: "var(--catfish-bg-elevated)", color: "var(--catfish-text)", border: "1px solid var(--catfish-border)",
                    borderRadius: 12, boxShadow: "0 16px 48px rgba(0,0,0,0.3)", overflow: "hidden" }}>
        <div style={{ padding: "16px 24px", borderBottom: "1px solid var(--catfish-border)", display: "flex", alignItems: "baseline", gap: 10 }}>
          <strong style={{ fontSize: 16 }}>编辑纪要</strong>
          <span style={{ fontSize: 13, color: "var(--catfish-text-muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "20px 24px", display: "flex", flexDirection: "column", gap: 24, fontSize: 14 }}>
          {markdownView ? (
            <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={sectionTitle}>纪要全文<span style={countStyle}>按模版写的那份, 直接改</span></div>
              <AutoText label="纪要全文" value={markdown} onChange={setMarkdown} minRows={12} />
            </section>
          ) : (
            <>
              <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={sectionTitle}>摘要</div>
                <AutoText label="摘要" value={summary} onChange={setSummary} minRows={3} />
              </section>
              <ListEdit label="决议" items={decisions} onChange={setDecisions} />
            </>
          )}
          <ItemsEdit items={items} onChange={setItems} />
          {!markdownView && <ListEdit label="待定问题" items={questions} onChange={setQuestions} />}
          {!markdownView && carried.length > 0 && <ListEdit label="上次遗留事项进展" items={carried} onChange={setCarried} />}
        </div>
        <div style={{ padding: "12px 24px", borderTop: "1px solid var(--catfish-border)", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: "1 1 260px", fontSize: 12, color: "var(--catfish-text-muted)" }}>
            存了以后纪要里写「已人工核对修改」; 再点「重新生成」会先提醒你, 不会悄悄覆盖。
          </span>
          {error && <span style={{ flexBasis: "100%" }}><ErrorLine>{error}</ErrorLine></span>}
          <Btn kind="ghost" disabled={saving} onClick={onCancel}>取消</Btn>
          <Btn kind="primary" disabled={saving || (markdownView && !markdown.trim())} onClick={() => void save()}>
            {saving ? "保存中…" : "保存"}
          </Btn>
        </div>
      </div>
    </div>
  );
}
