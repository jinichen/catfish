/**
 * 群聊的「记录保留」和「群内搜索」(11/6, docs/GROUP-CHAT-PLAN.md §4.2 / §4.4)。从 RoomsPanel 拆出来, 守 800 行。
 *
 * 记录保留: 公司 (中央管理门户) / 群主 / 个人三层, 取最短, 只能往短里调。这里是「个人」那层 + 群主用的下拉。
 * 群内搜索: 搜本机记录 (正文包含关键词, 可按人筛), 点一条跳到那条前后。
 */
import { useEffect, useState } from "react";

import {
  roomSearch, roomSettingsGet, roomSettingsSet,
  type RetentionSettings, type RoomMember, type RoomMessage,
} from "../../lib/tauri_colleague_rooms";
import { Overlay, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

const CHOICES = [7, 14, 30, 60, 90, 180, 365, 730, 1095, 1825, 3650];

/** 能选的天数 (不超过上限)。 */
export function retentionChoices(ceiling: number): number[] {
  return CHOICES.filter((d) => d < ceiling);
}

export const daysLabel = (d: number) => (d % 365 === 0 ? `${d / 365} 年` : `${d} 天`);

/** 群主用: 本群记录保留几天 (空 = 跟公司规定 / 默认)。 */
export function RetentionSelect({ value, onChange }: { value: number | null; onChange: (v: number | null) => void }) {
  const [s, setS] = useState<RetentionSettings | null>(null);
  useEffect(() => { roomSettingsGet().then(setS).catch(() => setS(null)); }, []);
  if (!s) return <span style={muted}>读取保留期限…</span>;
  const capLabel = s.ceiling_source === "company" ? "公司规定" : "默认";
  return (
    <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
      本群记录在每个人电脑上保留
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
              style={{ ...inputStyle, width: "auto", padding: "4px 8px" }} aria-label="本群记录保留">
        <option value="">跟{capLabel} ({daysLabel(s.ceiling)})</option>
        {retentionChoices(s.ceiling).map((d) => <option key={d} value={d}>{daysLabel(d)}</option>)}
      </select>
    </label>
  );
}

/** 个人: 这台电脑上群聊记录最多留多久 (只能比公司 / 默认更短)。 */
export function RetentionDialog({ onClose }: { onClose: () => void }) {
  const [s, setS] = useState<RetentionSettings | null>(null);
  const [v, setV] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    roomSettingsGet().then((r) => { setS(r); setV(r.personal ? String(r.personal) : ""); })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  const next = v ? Number(v) : null;
  const shorter = s && next !== null && next < (s.personal ?? s.ceiling);

  async function save() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await roomSettingsSet(next);
      setS(r);
      setMsg(`${next ? `改成 ${daysLabel(next)}` : `改回跟${r.ceiling_source === "company" ? "公司规定" : "默认"}`}${r.removed ? `, 清掉 ${r.removed} 条过期记录` : ""}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title="群聊记录保留" onClose={onClose} width={520}>
      <span style={muted}>
        群聊记录只存在你这台电脑上, 到期自动删掉。公司规定 / 群主设的期限你只能再设短, 不能更长; 每个群取最短的那个。
        群里派出的任务和交回的结果在「协同事项」里另有记录, 不受影响。
      </span>
      {s && (
        <>
          <span style={{ fontSize: 13 }}>
            上限: {s.ceiling_source === "company" ? `公司规定 ${daysLabel(s.ceiling)}` : `默认 ${daysLabel(s.ceiling)} (公司没另外规定)`}
          </span>
          <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
            我这台电脑上最多留
            <select value={v} onChange={(e) => setV(e.target.value)} style={{ ...inputStyle, width: "auto", padding: "4px 8px" }} aria-label="我的记录保留">
              <option value="">不另设 (跟上限)</option>
              {retentionChoices(s.ceiling).map((d) => <option key={d} value={d}>{daysLabel(d)}</option>)}
            </select>
          </label>
          {shorter && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>改短会马上删掉 {daysLabel(next!)} 以前的群聊记录。</span>}
        </>
      )}
      {msg && <span style={{ fontSize: 12, color: "var(--catfish-cyan)" }}>{msg}</span>}
      {err && <ErrorLine>{err}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>关闭</Btn>
        <Btn kind="primary" disabled={busy || !s || next === (s?.personal ?? null)} onClick={() => void save()}>{busy ? "保存中…" : "保存"}</Btn>
      </div>
    </Overlay>
  );
}

/** 搜索命中的那段高亮。 */
export function highlight(text: string, q: string): (string | { hit: string })[] {
  if (!q) return [text];
  const out: (string | { hit: string })[] = [];
  const lower = text.toLowerCase(), k = q.toLowerCase();
  let i = 0;
  for (let j = lower.indexOf(k); j >= 0; j = lower.indexOf(k, i)) {
    if (j > i) out.push(text.slice(i, j));
    out.push({ hit: text.slice(j, j + q.length) });
    i = j + q.length;
  }
  if (i < text.length) out.push(text.slice(i));
  return out;
}

/** 群内搜索: 搜本机记录, 点一条跳过去。 */
export function SearchPanel({ roomId, members, onJump, onClose }: {
  roomId: string; members: RoomMember[]; onJump: (seq: number) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [who, setWho] = useState("");
  const [hits, setHits] = useState<(RoomMessage & { name: string })[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function run() {
    if (!q.trim()) return;
    setErr(null);
    try { setHits((await roomSearch(roomId, q.trim(), who || undefined)).results); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <div style={{ border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 8, display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
      <div style={{ display: "flex", gap: 6 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void run(); }} autoFocus
               placeholder="搜群里说过的话 (本机记录)" style={{ ...inputStyle, flex: 1, padding: "4px 8px" }} aria-label="群内搜索" />
        <select value={who} onChange={(e) => setWho(e.target.value)} style={{ ...inputStyle, width: "auto", padding: "4px 8px" }} aria-label="按人筛">
          <option value="">所有人</option>
          {members.map((m) => <option key={m.email} value={m.email}>{m.name || m.email.split("@")[0]}</option>)}
        </select>
        <Btn kind="primary" disabled={!q.trim()} onClick={() => void run()}>搜</Btn>
        <Btn kind="ghost" onClick={onClose}>收起</Btn>
      </div>
      {err && <ErrorLine>{err}</ErrorLine>}
      {hits && hits.length === 0 && <span style={muted}>没搜到 (只搜你电脑上还留着的记录)。</span>}
      {hits && hits.length > 0 && (
        <div style={{ maxHeight: 200, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
          {hits.map((h) => (
            <button key={h.msg_id} type="button" className="room-menu-item" onClick={() => h.seq && onJump(h.seq)}
                    style={{ textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: "4px 6px",
                             fontSize: 12, color: "inherit", borderRadius: 4 }}>
              <span style={muted}>{h.name} · {String(h.at).slice(5, 16).replace("T", " ")} · </span>
              {highlight(h.text.slice(0, 120), q.trim()).map((p, i) => typeof p === "string" ? p
                : <mark key={i} style={{ background: "rgba(240,112,58,0.3)", color: "inherit" }}>{p.hit}</mark>)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
