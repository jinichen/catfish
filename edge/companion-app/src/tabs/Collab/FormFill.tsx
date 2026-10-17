/**
 * 收件人填表 (10/17, docs/FORM-COLLECT-PLAN.md §3)。
 *
 * 「让小鲶先填」: 开一个工作台对话, 小鲶用本机材料找值、存草稿 (它不能提交)。回到这里重新打开就看到草稿 + 出处。
 * 「提交」才发出去 (只发表单里的值); 截止前可以改了再交, 后交的覆盖先前的。也可以「不填」并写原因。
 */
import { useEffect, useState } from "react";

import { prefillChatKey, prefillPrompt } from "../../lib/formPrompts";
import { refreshForms } from "../../lib/formsStore";
import {
  formDecline, formDraft, formGet, formSubmit,
  type FieldValues, type IncomingRecord,
} from "../../lib/tauri_forms";
import { useUIStore } from "../../store/ui";
import { FieldInput, Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

export default function FormFill({ formId, onClose }: { formId: string; onClose: () => void }) {
  const [rec, setRec] = useState<IncomingRecord | null>(null);
  const [state, setState] = useState("");
  const [values, setValues] = useState<FieldValues>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const startChat = useUIStore((s) => s.startEmailChat);

  useEffect(() => {
    formGet(formId).then((d) => {
      if (d.role !== "recipient") return;
      setRec(d.record);
      setState(d.state);
      setValues(d.record.submitted?.values ?? d.record.draft?.values ?? {});
    }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [formId]);

  if (!rec) {
    return <Overlay title="填表" onClose={onClose}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</Overlay>;
  }
  const closed = rec.status === "closed";
  const sources = rec.draft?.sources ?? {};

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const saveDraft = () => run(async () => {
    await formDraft(formId, values);
    setDone("草稿存好了 (还没交)");
    void refreshForms();
  });

  const submit = () => run(async () => {
    const r = await formSubmit(formId, values);
    if (!r.ok) {
      setProblems(r.problems ?? []);
      return;
    }
    setProblems([]);
    setDone(r.late ? "交上去了 (已过截止日, 发起人那边会标逾期)" : "交上去了");
    void refreshForms();
  });

  const decline = () => run(async () => {
    await formDecline(formId, reason);
    setDone("已告诉发起人你不填");
    setDeclining(false);
    void refreshForms();
  });

  const askXiaonian = () => {
    void formDraft(formId, values).catch(() => {});   // 先把手上改的存住, 小鲶在它上面补
    startChat(prefillChatKey(formId), prefillPrompt(rec));
    onClose();
  };

  return (
    <Overlay title={`《${rec.form.title}》`} onClose={onClose}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <span style={muted}>{rec.owner} 发起{rec.form.deadline ? ` · ${rec.form.deadline} 截止` : ""}</span>
        <StateTag state={state} />
      </div>
      {rec.form.note && <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{rec.form.note}</div>}
      {rec.remind_text !== undefined && rec.reminded_at && !rec.submitted && (
        <div style={{ ...muted, color: "var(--status-warn, #d97706)" }}>发起人催了{rec.remind_text ? `: ${rec.remind_text}` : ""}</div>
      )}
      {closed && <div style={muted}>发起人已经结束收集, 不能再交了。</div>}
      {rec.declined && <div style={muted}>你回了不填: {rec.declined.reason}</div>}

      {!closed && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Btn kind="ghost" disabled={busy} onClick={askXiaonian}>🐟 让小鲶先填</Btn>
          <span style={muted}>小鲶用你电脑上的邮件、文件、知识库起草, 找不到的空着; 只存草稿, 交不交你定。</span>
        </div>
      )}

      {rec.form.fields.map((f) => (
        <FieldInput key={f.key} field={f} values={values} readOnly={closed} source={sources[f.key]}
                    onChange={(k, v) => setValues((old) => ({ ...old, [k]: v }))} />
      ))}

      {problems.length > 0 && <ErrorLine>{problems.join("; ")}</ErrorLine>}
      {error && <ErrorLine>{error}</ErrorLine>}
      {done && <div style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{done}</div>}

      {!closed && !declining && (
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <Btn kind="ghost" disabled={busy} onClick={() => setDeclining(true)}>不填</Btn>
          <div style={{ display: "flex", gap: 8 }}>
            <Btn kind="ghost" disabled={busy} onClick={saveDraft}>存草稿</Btn>
            <Btn kind="primary" disabled={busy} onClick={submit}>{rec.submitted ? "改了再交" : "提交"}</Btn>
          </div>
        </div>
      )}
      {declining && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="说一句为什么不填 (会发给发起人)" style={inputStyle} />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn kind="ghost" onClick={() => setDeclining(false)}>算了</Btn>
            <Btn kind="primary" disabled={busy || !reason.trim()} onClick={decline}>告诉发起人</Btn>
          </div>
        </div>
      )}
      {rec.submitted && <span style={muted}>上次交于 {rec.submitted.at.slice(0, 16).replace("T", " ")}</span>}
    </Overlay>
  );
}
