/**
 * 让小鲶改纪要 (11/17 鸿波「目前无法利用小鲶来共同修正纪要?」)。
 *
 * 原来只有「重新生成」(改过的全丢) 和「编辑」(自己一项项改)。现在纪要上方一个输入框, 跟小鲶说一句
 * 「说话人3 是王总, 待办负责人换过来」「摘要压到三句」「漏了合同履约那段, 补上」, 小鲶对照转写改出一版:
 *   · 纪要区变成「改动预览」: 新加的标青底、删掉的划掉, 摘要改了能看原来的;
 *   · 「采用」才存 (跟手动编辑同一个口子, 标「已人工修改」), 「不要」原样不动;
 *   · 不满意接着说, 小鲶在这一版上继续改 (采用前可以来回几轮)。
 * 后端 tool-bridge meeting_minutes_revise.py (只出提议, 不写文件)。
 */
import { useState, type CSSProperties, type ReactNode } from "react";

import { Markdown } from "../../lib/markdown";
import {
  meetingMinutesRevise, meetingMinutesSave, type ActionItem, type Minutes, type MinutesFields, type MinutesRevision,
} from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { diffItems, diffList, itemText, markdownHunks, reviseSuggestions, type DiffRow } from "./reviseDiff";

const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const chip: CSSProperties = {
  border: "1px solid var(--catfish-border)", background: "transparent", color: "var(--catfish-text-muted)",
  borderRadius: 999, padding: "2px 10px", fontSize: 12, cursor: "pointer", fontFamily: "inherit",
};
const rowStyle = (kind: DiffRow<unknown>["kind"]): CSSProperties => ({
  lineHeight: 1.7, padding: "0 6px", borderRadius: 4,
  ...(kind === "added" || kind === "changed" ? { background: "var(--catfish-cyan-tint)", boxShadow: "inset 2px 0 0 var(--catfish-cyan)" } : null),
  ...(kind === "removed" ? { color: "var(--catfish-text-muted)", textDecoration: "line-through" } : null),
});

export interface Turn { instruction: string; reply: string }

/** 状态放在 MinutesView 里 (预览要替换纪要正文), 组件只管画。 */
export function useRevise(meetingId: string, minutes: Minutes | null, markdownView: boolean, onApplied: () => void) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<MinutesRevision | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);

  async function send(text?: string) {
    const instruction = (text ?? draft).trim();
    if (!instruction || !minutes || busy) return;
    setBusy(true);
    setError(null);
    try {
      const base = proposal ? { fields: proposal.fields, markdown: proposal.markdown } : null;
      const r = await meetingMinutesRevise(meetingId, instruction, base, turns.map((t) => t.instruction));
      setTurns([...turns, { instruction, reply: r.reply }]);
      // 这一轮没改动: 保留上一轮的提议 (要是有), 只显示小鲶的说明
      if (r.changed.length) setProposal(r);
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function discard() {
    setProposal(null);
    setTurns([]);
    setError(null);
  }

  async function apply() {
    if (!proposal) return;
    setApplying(true);
    setError(null);
    try {
      const fields: MinutesFields = markdownView ? { action_items: proposal.fields.action_items ?? [] } : proposal.fields;
      await meetingMinutesSave(meetingId, fields, markdownView ? proposal.markdown : null);
      discard();
      onApplied();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }

  function reset() {
    discard();
    setDraft("");
    setBusy(false);
  }

  return { draft, setDraft, busy, applying, error, proposal, turns, send, discard, apply, reset };
}

export type ReviseApi = ReturnType<typeof useRevise>;

/** 纪要上方的输入框 + 现成说法 + 这一轮小鲶说了什么 */
export function ReviseComposer({ api, minutes, names, disabled }: {
  api: ReviseApi; minutes: Minutes; names: Record<string, string>; disabled?: boolean;
}) {
  const last = api.turns[api.turns.length - 1];
  const tips = reviseSuggestions(minutes.json, minutes.markdown, names);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 12px", borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea
          aria-label="让小鲶改纪要"
          value={api.draft}
          rows={2}
          maxLength={1000}
          disabled={disabled}
          onChange={(e) => api.setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void api.send();
            }
          }}
          placeholder={api.proposal ? "还要怎么改? 小鲶在这一版上接着改" : "让小鲶改纪要: 说一句要怎么改, 小鲶对照转写改好给你过目"}
          style={{ ...inputStyle, flex: 1, resize: "none", fontSize: 13, lineHeight: 1.6 }}
        />
        <Btn kind="primary" disabled={disabled || api.busy || !api.draft.trim()} onClick={() => void api.send()}>
          {api.busy ? "小鲶在改…" : "🐟 让小鲶改"}
        </Btn>
      </div>
      {!api.proposal && !api.busy && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
          <span style={muted}>比如</span>
          {tips.map((t) => (
            <button key={t} type="button" style={chip} disabled={disabled} onClick={() => api.setDraft(t)}>{t}</button>
          ))}
        </div>
      )}
      {api.busy && <div style={muted}>小鲶在对照转写改, 长会议要半分钟到一分钟。</div>}
      {last && !api.busy && (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <span style={muted}>你: {last.instruction}</span>
          <div>🐟 {last.reply}</div>
        </div>
      )}
      {api.error && <ErrorLine>{api.error}</ErrorLine>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
      {children}
    </div>
  );
}

function Rows<T>({ rows, text }: { rows: DiffRow<T>[]; text: (x: T) => string }) {
  if (!rows.length) return <div style={muted}>(无)</div>;
  return (
    <>
      {rows.map((r, i) => (
        <div key={i} style={rowStyle(r.kind)}>
          {r.kind === "added" ? "＋ " : "· "}{text(r.value)}
          {r.kind === "changed" && r.old !== undefined && (
            <span style={{ ...muted, marginLeft: 6 }}>原来: <s>{text(r.old)}</s></span>
          )}
        </div>
      ))}
    </>
  );
}

const ItemRows = ({ before, after }: { before: ActionItem[]; after: ActionItem[] }) => {
  // 待办只显示负责人 / 截止的变化, 事项本身一样时「原来」那行只写括号里的
  const tail = (it: ActionItem) => [it.owner, it.due && `截止 ${it.due}`].filter(Boolean).join(" · ") || "没写负责人";
  const rows = diffItems(before, after);
  if (!rows.length) return <div style={muted}>(无)</div>;
  return (
    <>
      {rows.map((r, i) => (
        <div key={i} style={rowStyle(r.kind)}>
          {r.kind === "added" ? "＋ " : "· "}{itemText(r.value)}
          {r.kind === "changed" && r.old && <span style={{ ...muted, marginLeft: 6 }}>原来: <s>{tail(r.old)}</s></span>}
        </div>
      ))}
    </>
  );
};

const ListDiff = ({ before, after }: { before: string[]; after: string[] }) => (
  <Rows rows={diffList(before, after)} text={(x) => x} />
);

/** 改动预览: 替换纪要正文。新加的青底、删掉的划掉。 */
export function RevisePreview({ api, minutes, markdownView }: { api: ReviseApi; minutes: Minutes; markdownView: boolean }) {
  const p = api.proposal;
  const [showOld, setShowOld] = useState(false);
  if (!p) return null;
  const m = minutes.json;
  const f = p.fields;
  const items = (f.action_items ?? m.action_items) as ActionItem[];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)", padding: "12px 14px",
                  border: "1px solid var(--catfish-cyan)", borderRadius: "var(--radius-sm)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13, flex: 1, minWidth: 160 }}>
          🐟 小鲶改了: {p.changed_labels.join("、") || "没有改动"}
          <span style={{ ...muted, fontWeight: 400, marginLeft: 8 }}>还没存, 看过再决定</span>
        </strong>
        <Btn kind="ghost" disabled={api.applying} onClick={api.discard}>不要</Btn>
        <Btn kind="primary" disabled={api.applying || api.busy} onClick={() => void api.apply()}>
          {api.applying ? "保存中…" : "采用"}
        </Btn>
      </div>
      {!p.transcript_full && (
        <div style={muted}>会议较长, 小鲶这次只看了转写里跟你这句话相关的片段; 要补的内容要是没找到, 可以说具体些 (人名、关键词)。</div>
      )}
      {markdownView ? (
        <>
          <Section title="改动">
            {markdownHunks(minutes.markdown, p.markdown ?? "").map((r, i) =>
              r === null
                ? <div key={i} style={muted}>…</div>
                : <div key={i} style={{ ...rowStyle(r.kind), whiteSpace: "pre-wrap" }}>{r.kind === "added" ? "＋ " : r.kind === "removed" ? "－ " : "  "}{r.value || " "}</div>)}
          </Section>
          {p.changed.includes("action_items") && (
            <Section title="待办"><ItemRows before={m.action_items} after={items} /></Section>
          )}
          <button type="button" onClick={() => setShowOld((v) => !v)}
                  style={{ ...chip, alignSelf: "flex-start", border: "none", padding: 0 }}>
            {showOld ? "▾ 收起改后全文" : "▸ 看改后全文"}
          </button>
          {showOld && <Markdown text={p.markdown ?? ""} />}
        </>
      ) : (
        <>
          <Section title={p.changed.includes("summary") ? "摘要 (改过)" : "摘要"}>
            <div style={{ lineHeight: 1.75, ...(p.changed.includes("summary") ? rowStyle("added") : null) }}>{f.summary ?? m.summary}</div>
            {p.changed.includes("summary") && (
              <>
                <button type="button" onClick={() => setShowOld((v) => !v)}
                        style={{ ...chip, border: "none", padding: 0, marginTop: 4 }}>
                  {showOld ? "▾ 收起原来的" : "▸ 看原来的"}
                </button>
                {showOld && <div style={{ ...rowStyle("removed"), lineHeight: 1.75 }}>{m.summary}</div>}
              </>
            )}
          </Section>
          <Section title="决议"><ListDiff before={m.decisions} after={f.decisions ?? m.decisions} /></Section>
          <Section title="待办"><ItemRows before={m.action_items} after={items} /></Section>
          <Section title="待定问题">
            <ListDiff before={m.open_questions} after={f.open_questions ?? m.open_questions} />
          </Section>
          {((m.carried_over?.length ?? 0) > 0 || (f.carried_over?.length ?? 0) > 0) && (
            <Section title="上次遗留事项进展">
              <ListDiff before={m.carried_over ?? []} after={f.carried_over ?? m.carried_over ?? []} />
            </Section>
          )}
        </>
      )}
    </div>
  );
}
