/**
 * 合并面板 (11/11, 发起人): 在本机把大家交回的修改合起来 —— 先看结果, 冲突一处处定完, 再「发出合并版」。
 *
 * 上面一段是自动合了谁几处、谁看过没改、提醒 (格式调整没合进来之类)。下面每个冲突一张卡:
 * 原文 + 每个人的改法 + (两人改的是同一段不同地方时) 按字拼好的建议 + 小鲶建议 (要点一下才去问大模型) + 保留原文 + 我自己改。
 * 每点一下就存 (关了面板再开还在)。冲突没定完「发出」是灰的。
 */
import { useEffect, useState } from "react";

import {
  reviseDecide, reviseMerge, revisePublish, reviseSuggest, type ReviseAnalysis, type ReviseConflict, type ReviseDecision,
} from "../../lib/tauri_room_revise";
import { Overlay, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

const box = { fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word", padding: "6px 8px", borderRadius: 6,
              border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)", maxHeight: 180, overflowY: "auto" } as const;

export default function ReviseMergePanel({ roomId, revId, title, onClose, onDone }: {
  roomId: string; revId: string; title: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const [an, setAn] = useState<ReviseAnalysis | null>(null);
  const [dec, setDec] = useState<Record<string, ReviseDecision>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    reviseMerge(roomId, revId).then((a) => { setAn(a); setDec(a.decisions); }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [roomId, revId]);

  async function decide(cid: string, pick: string | null, text?: string) {
    setDec((d) => { const n = { ...d }; if (pick === null) delete n[cid]; else n[cid] = { pick, ...(text !== undefined ? { text } : {}) }; return n; });
    try {
      await reviseDecide(roomId, revId, cid, pick, text);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  async function publish() {
    setBusy(true);
    setError(null);
    try {
      const r = await revisePublish(roomId, revId, note);
      onDone(`《${title}》v${r.version} 合并版已发到群里` + (r.warnings.length ? ` (${r.warnings.length} 条提醒见合并记录)` : ""));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const names = an?.names ?? {};
  const left = an ? an.conflicts.filter((c) => !dec[c.cid]).length : 0;
  const parts = an ? Object.entries(an.changes).filter(([w, n]) => n && w !== "__merged__") : [];
  return (
    <Overlay title={`合并《${title}》`} onClose={onClose} width={820}>
      {!an && !error && <span style={muted}>正在合并大家交回的修改…</span>}
      {error && <ErrorLine>{error}</ErrorLine>}
      {an && (
        <>
          {an.late && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>这批是合并版发出之后才交回的: 跟上一版合并稿再合一次, 出下一版。</span>}
          <div style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
            <span>
              {parts.length ? <>自动合并: {parts.map(([w, n]) => `${names[w] ?? w} ${n} 处`).join("、")}</> : "没有能自动合的改动"}
              {an.conflicts.length ? <> · <strong>{an.conflicts.length} 处要你定</strong></> : " · 没有冲突"}
            </span>
            {an.same.length > 0 && <span style={muted}>看过没改: {an.same.map((w) => names[w] ?? w).join("、")}</span>}
            {an.warnings.map((x) => <span key={x} style={{ ...muted, color: "var(--status-warn, #d97706)" }}>⚠ {x}</span>)}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: "56vh", overflowY: "auto", paddingRight: 4 }}>
            {an.conflicts.map((c, i) => (
              <ConflictCard key={c.cid} n={i + 1} c={c} d={dec[c.cid]} roomId={roomId} revId={revId}
                            onPick={(p, t) => void decide(c.cid, p, t)} />
            ))}
          </div>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
            附一句 (可不填, 跟合并版一起发到群里)
            <input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} style={inputStyle} />
          </label>
          <span style={muted}>Word / Excel / PPT 会出两份: 「修订版」里每处修改标着是谁改的 (Word 里可一条条接受 / 拒绝), 另一份是改好的干净版。</span>
          <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8 }}>
            {left > 0 && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>还有 {left} 处没定</span>}
            <Btn kind="ghost" onClick={onClose}>先不发</Btn>
            <Btn kind="primary" disabled={busy || left > 0} onClick={() => void publish()}>{busy ? "生成中…" : "发出合并版"}</Btn>
          </div>
        </>
      )}
    </Overlay>
  );
}

function ConflictCard({ n, c, d, roomId, revId, onPick }: {
  n: number; c: ReviseConflict; d?: ReviseDecision; roomId: string; revId: string; onPick: (pick: string | null, text?: string) => void;
}) {
  const [sug, setSug] = useState(c.suggest ?? null);
  const [asking, setAsking] = useState(false);
  const [own, setOwn] = useState(d?.pick === "text" ? d.text ?? "" : c.combined ?? c.options[0]?.text ?? "");
  const [ownOpen, setOwnOpen] = useState(d?.pick === "text" && d.text !== c.combined && d.text !== sug?.text);
  async function ask() {
    setAsking(true);
    try {
      setSug(await reviseSuggest(roomId, revId, c.cid));
    } catch (e) {
      setSug({ text: null, reason: e instanceof Error ? e.message : String(e) });
    } finally {
      setAsking(false);
    }
  }
  const textual = c.kind === "text" && c.custom !== false;
  const radio = (key: string, label: string, body?: string, pick = key, text?: string) => {
    const on = d?.pick === pick && (pick !== "text" || d.text === text);
    return (
      <label key={key} style={{ display: "flex", gap: 6, alignItems: "flex-start", cursor: "pointer", fontSize: 12 }}>
        <input type="radio" name={c.cid} checked={on} onChange={() => onPick(pick, text)} style={{ marginTop: 3 }} />
        <span style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: on ? 600 : 400 }}>{label}</span>
          {body !== undefined && <span style={box}>{body || "(删掉了)"}</span>}
        </span>
      </label>
    );
  };
  return (
    <div style={{ border: `1px solid ${d ? "var(--catfish-border)" : "var(--status-warn, #d97706)"}`, borderRadius: 8, padding: 10,
                  display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{n}. {c.where}{d ? " ✓" : ""}</span>
      {c.kind !== "whole" && <><span style={muted}>原文</span><span style={{ ...box, opacity: 0.8 }}>{c.base || "(原来没有)"}</span></>}
      {c.combined && textual && radio("combined", "两人改的是不同地方, 拼起来是这样 (推荐)", c.combined, "text", c.combined)}
      {textual && (sug?.text ? radio("sug", "小鲶建议", sug.text, "text", sug.text) : (
        <span style={{ ...muted, display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" disabled={asking} onClick={() => void ask()}
                  style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, fontSize: 12, color: "var(--catfish-cyan)" }}>
            {asking ? "🐟 小鲶在看…" : "🐟 让小鲶给个兼顾的写法"}
          </button>
          {sug?.reason && <span>{sug.reason}</span>}
        </span>
      ))}
      {c.options.map((o) => radio(o.key, `用 ${o.names.join("、")} 的`, c.kind === "whole" ? undefined : o.text))}
      {c.kind !== "whole" && radio("base", "保留原文", undefined, "base")}
      {textual && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <button type="button" onClick={() => setOwnOpen((v) => !v)}
                  style={{ alignSelf: "flex-start", border: "none", background: "transparent", cursor: "pointer", padding: 0, fontSize: 12,
                           color: "var(--catfish-cyan)" }}>{ownOpen ? "收起" : "我自己改"}</button>
          {ownOpen && (
            <>
              <textarea value={own} rows={3} onChange={(e) => setOwn(e.target.value)} style={{ ...inputStyle, resize: "vertical", fontSize: 12 }} />
              <span><Btn kind="ghost" disabled={!own.trim()} onClick={() => onPick("text", own)}>用我写的</Btn></span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
