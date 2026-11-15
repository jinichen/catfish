/**
 * 会中小鲶 (11/15, docs/MEETING-MINUTES-PLAN.md §8)。
 *
 * 录音中 (右边会议详情): 实时文字 (边录边转, 落后几秒到半分钟, 不分说话人) + 「问小鲶」输入框 + 小鲶的卡片。
 *   · 问小鲶: 随时问, 小鲶看到目前为止的会中文字 + 知识库 + 上次同系列会议; 点实时文字里的一句 = 问这句
 *   · 主动提示 (默认开, 可以关): 每 3 分钟看一眼新说的话, 有决议 / 待办 / 没人答的问题 / 跟资料对不上 / 可以追问的才出卡
 *   · 每条可以「记到纪要」或「忽略」; 记到纪要的生成纪要时带上, 纪要末尾附「会中小鲶」一节
 * 会后 (LiveNotes): 纪要旁边列出会中的问答和提示, 可以改记不记、删掉。
 * 只在这一页开着时才主动提示 (切走了就停, 回来接着从上次看到的地方看)。
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import type { MeetingMeta } from "../../lib/tauri_meeting";
import {
  HINT_LABEL, clock, liveStatusText, meetingLiveAsk, meetingLiveHint, meetingLiveHintsSet, meetingLiveNoteDelete,
  meetingLiveNoteSet, meetingLiveNotes, meetingLiveRead, quoteLine,
  type LiveLine, type LiveNote, type LiveState, type NoteState,
} from "../../lib/tauri_meeting_live";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";

export const HINT_EVERY_MS = 3 * 60 * 1000;
const POLL_MS = 2000;
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const small: CSSProperties = { border: "none", background: "transparent", cursor: "pointer", padding: "0 4px", fontSize: 12 };

function useNotes(meetingId: string) {
  const [notes, setNotes] = useState<LiveNote[]>([]);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    meetingLiveNotes(meetingId).then((d) => setNotes(d.notes)).catch(() => undefined);
  }, [meetingId]);
  useEffect(reload, [reload]);
  const run = async (p: Promise<{ notes: LiveNote[] }>) => {
    try {
      setNotes((await p).notes);
      setError(null);
      return true;
    } catch (e) {
      setError(String(e));
      return false;
    }
  };
  return {
    notes, setNotes, error, setError, reload,
    set: (noteId: string, itemId: string | null, state: NoteState) => run(meetingLiveNoteSet(meetingId, noteId, itemId, state)),
    remove: (noteId: string, itemId: string | null = null) => run(meetingLiveNoteDelete(meetingId, noteId, itemId)),
  };
}

type NotesApi = ReturnType<typeof useNotes>;

function PinBtn({ state, onSet }: { state: NoteState; onSet: (s: NoteState) => void }) {
  return state === "pinned"
    ? <button type="button" style={{ ...small, color: "var(--status-ok)" }} title="点一下不记了" onClick={() => onSet("new")}>✓ 已记到纪要</button>
    : <button type="button" style={{ ...small, color: "var(--catfish-cyan)" }} onClick={() => onSet("pinned")}>记到纪要</button>;
}

/** 卡片: 问答一张, 一次提示一张 (里面几条) */
function NoteCards({ api, after }: { api: NotesApi; after?: boolean }) {
  const shown = [...api.notes].reverse();
  if (!shown.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {shown.map((n) => (
        <div key={n.id} data-testid={`live-note-${n.kind}`}
             style={{ border: "1px dashed var(--catfish-cyan)", borderRadius: "var(--radius-sm)", padding: "8px 10px", fontSize: 13,
                      display: "flex", flexDirection: "column", gap: 4 }}>
          {n.kind === "ask" ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={muted}>[{clock(n.at)}] 你问 🐟 小鲶: <span style={{ color: "var(--catfish-text)" }}>{n.q}</span></span>
                <span style={{ display: "flex", flexShrink: 0 }}>
                  <PinBtn state={n.state} onSet={(s) => void api.set(n.id, null, s)} />
                  <button type="button" style={{ ...small, color: "var(--catfish-text-muted)" }} onClick={() => void api.remove(n.id)}>删</button>
                </span>
              </div>
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{n.answer}</div>
            </>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={muted}>🐟 小鲶提示 · {clock(n.from)}–{clock(n.at)}</span>
                {after && <button type="button" style={{ ...small, color: "var(--catfish-text-muted)" }} onClick={() => void api.remove(n.id)}>整张删掉</button>}
              </div>
              {n.items.filter((it) => !(after && it.state === "ignored")).map((it) => (
                <div key={it.id} style={{ display: "flex", gap: 8, alignItems: "baseline", opacity: it.state === "ignored" ? 0.45 : 1 }}>
                  <span style={{ ...muted, flexShrink: 0, minWidth: 72 }}>{HINT_LABEL[it.type]}</span>
                  <span style={{ flex: 1, minWidth: 0, textDecoration: it.state === "ignored" ? "line-through" : undefined }}>{it.text}</span>
                  <span style={{ display: "flex", flexShrink: 0 }}>
                    {it.state === "ignored"
                      ? <button type="button" style={{ ...small, color: "var(--catfish-text-muted)" }} onClick={() => void api.set(n.id, it.id, "new")}>恢复</button>
                      : <>
                          <PinBtn state={it.state} onSet={(s) => void api.set(n.id, it.id, s)} />
                          {after
                            ? <button type="button" style={{ ...small, color: "var(--catfish-text-muted)" }} onClick={() => void api.remove(n.id, it.id)}>删</button>
                            : <button type="button" style={{ ...small, color: "var(--catfish-text-muted)" }} onClick={() => void api.set(n.id, it.id, "ignored")}>忽略</button>}
                        </>}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/** 录音中 */
export default function LiveAssist({ meta }: { meta: MeetingMeta }) {
  const id = meta.id;
  const [lines, setLines] = useState<LiveLine[]>([]);
  const [state, setState] = useState<LiveState | null>(null);
  const [q, setQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [hintsOn, setHintsOn] = useState(meta.live_hints ?? true);
  const [hinting, setHinting] = useState(false);
  const [hintNote, setHintNote] = useState<string | null>(null);
  const [mountedAt] = useState(() => Date.now());
  const api = useNotes(id);
  const since = useRef(0);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    since.current = 0;
    setLines([]);
    let alive = true;
    const tick = () => meetingLiveRead(id, since.current).then((r) => {
      if (!alive) return;
      setState(r.state);
      if (r.lines.length) {
        since.current = r.total;
        setLines((l) => [...l, ...r.lines].slice(-600));
      }
    }).catch(() => undefined);
    void tick();
    const t = window.setInterval(() => void tick(), POLL_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, [id]);

  useEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, state?.partial]);

  // 主动提示: 开着才看; 每 3 分钟一次, 上一次没回来不叠
  const hintBusy = useRef(false);
  useEffect(() => {
    if (!hintsOn) return;
    const t = window.setInterval(async () => {
      if (hintBusy.current) return;
      hintBusy.current = true;
      setHinting(true);
      try {
        const r = await meetingLiveHint(id);
        if (r.note) {
          const note = r.note;
          api.setNotes((ns) => [...ns, note]);
        }
        setHintNote(null);
      } catch (e) {
        setHintNote(`这次没提示成: ${String(e)}`);
      } finally {
        hintBusy.current = false;
        setHinting(false);
      }
    }, HINT_EVERY_MS);
    return () => window.clearInterval(t);
  }, [id, hintsOn]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function ask() {
    const question = q.trim();
    if (!question || asking) return;
    setAsking(true);
    api.setError(null);
    try {
      const r = await meetingLiveAsk(id, question);
      api.setNotes((ns) => [...ns, r.note]);
      setQ("");
    } catch (e) {
      api.setError(String(e));
    } finally {
      setAsking(false);
    }
  }

  function toggleHints(on: boolean) {
    setHintsOn(on);
    void meetingLiveHintsSet(id, on).catch(() => undefined);
  }

  const status = liveStatusText(state, lines.length, (Date.now() - mountedAt) / 1000);
  const two = lines.some((l) => l.track === "system");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <strong style={{ fontSize: 13 }}>实时文字</strong>
        <span style={muted}>边录边转, 晚几秒到半分钟; 不分说话人, 会后转写会分 · 点一句可以问小鲶</span>
      </div>
      <div ref={box} data-testid="live-lines"
           onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
           style={{ height: 220, overflowY: "auto", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)",
                    padding: "6px 10px", fontSize: 13, lineHeight: 1.7, background: "var(--catfish-bg)" }}>
        {lines.map((l, i) => (
          <div key={`${l.start}-${i}`} role="button" tabIndex={0} title="问小鲶这句"
               onClick={() => { setQ(quoteLine(l)); input.current?.focus(); }}
               onKeyDown={(e) => { if (e.key === "Enter") { setQ(quoteLine(l)); input.current?.focus(); } }}
               className="room-menu-item" style={{ cursor: "pointer", borderRadius: 4, padding: "0 4px" }}>
            <span style={{ ...muted, marginRight: 6 }}>{clock(l.start)}</span>
            {two && <span style={{ ...muted, marginRight: 4 }}>{l.track === "system" ? "对方" : "这边"}</span>}
            {l.text}
          </div>
        ))}
        {state?.partial && <div style={{ color: "var(--catfish-text-muted)", padding: "0 4px" }}>{state.partial}…</div>}
        {!lines.length && !state?.partial && <div style={muted}>还没有文字 —— 有人说话后几秒到半分钟出来。</div>}
      </div>
      {status && <div style={muted}>{status}</div>}

      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea ref={input} value={q} rows={2} maxLength={500} onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void ask(); } }}
                  placeholder="问小鲶: 「刚才说的暂停通知流程是什么?」「对方说的这个标准是不是旧版?」(只有你看得到)"
                  style={{ ...inputStyle, flex: 1, resize: "none" }} />
        <Btn kind="primary" disabled={asking || !q.trim()} onClick={() => void ask()}>{asking ? "小鲶在想…" : "🐟 问小鲶"}</Btn>
      </div>
      <label style={{ ...muted, display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
        <input type="checkbox" checked={hintsOn} onChange={(e) => toggleHints(e.target.checked)} />
        小鲶主动提示: 每 3 分钟看一眼新说的话, 有决议 / 待办 / 没人答的问题 / 跟资料对不上的才出卡
        {hinting && " · 在看…"}
      </label>
      <div style={muted}>会中文字只在你电脑上; 问小鲶和主动提示时, 这段文字经公司网关给大模型 (跟生成纪要同一条路), 录音不出本机。</div>
      {hintNote && <div style={muted}>{hintNote}</div>}
      {api.error && <ErrorLine>{api.error}</ErrorLine>}
      <NoteCards api={api} />
    </div>
  );
}

/** 会后: 会中的问答和提示 (没有就不显示) */
export function LiveNotes({ meetingId, onChanged }: { meetingId: string; onChanged?: () => void }) {
  const api = useNotes(meetingId);
  const [open, setOpen] = useState(false);
  if (!api.notes.length) return null;
  const pinned = api.notes.reduce((n, x) => n + (x.kind === "ask" ? Number(x.state === "pinned") : x.items.filter((i) => i.state === "pinned").length), 0);
  const wrapped: NotesApi = {
    ...api,
    set: async (...a) => { const ok = await api.set(...a); if (ok) onChanged?.(); return ok; },
    remove: async (...a) => { const ok = await api.remove(...a); if (ok) onChanged?.(); return ok; },
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
              style={{ ...small, padding: 0, alignSelf: "flex-start", color: "var(--catfish-text)", fontSize: 13, fontWeight: 600 }}>
        {open ? "▾" : "▸"} 会中小鲶 ({api.notes.length} 张卡, {pinned} 条记到纪要)
      </button>
      {open && (
        <>
          <div style={muted}>记到纪要的, 生成纪要时会带上, 纪要末尾附「会中小鲶」一节; 在这里改了, 已生成的纪要跟着改 (按模版写的那种要重新生成)。</div>
          {api.error && <ErrorLine>{api.error}</ErrorLine>}
          <NoteCards api={wrapped} after />
        </>
      )}
    </div>
  );
}
