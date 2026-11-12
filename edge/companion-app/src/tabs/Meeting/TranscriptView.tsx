/**
 * 转写原文 + 改错字 (11/12 鸿波「内容是不是还应该可以修正」)。
 *
 * 点一句右边的「改」就地改; 改过的那句标「改过」, 鼠标停上去看识别出来的原话, 清空保存 = 改回原话。
 * 改出来的新词 (人名 / 术语) 给一排按钮「记成常用热词」—— 以后新建会议热词框自动带上, 识别更准。
 * 改过的转写在「重新生成」纪要时就会用上。
 *
 * 11/12 (鸿波「不能播放录音, 怎么来修正识别内容」): 每句前面 ▶ 听这一句 (从本机录音里截, meetingPlayer.ts);
 * 改的时候编辑框旁边也有「▶ 听这句」, 边听边改。再点 = 停; 一次只放一句。
 */
import { useEffect, useState } from "react";

import {
  formatClock, meetingHotwordsRemember, meetingTranscriptEdit, speakerName,
  type Transcript, type TranscriptSegment,
} from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { changedWords } from "./meetingHelpers";
import { onPlayingChange, playingKey, stopClip, toggleClip } from "./meetingPlayer";

const linkBtn = { border: "none", background: "transparent", cursor: "pointer", padding: "0 4px", fontSize: 11,
                  color: "var(--catfish-text-muted)" } as const;

export default function TranscriptView({ meetingId, transcript, names, colors }: {
  meetingId: string; transcript: Transcript; names: Record<string, string>; colors: string[];
}) {
  const [segments, setSegments] = useState<TranscriptSegment[]>(transcript.segments);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [suggest, setSuggest] = useState<string[]>([]);
  const [remembered, setRemembered] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const edited = segments.filter((s) => s.orig_text).length;
  const [playing, setPlaying] = useState<string | null>(playingKey());
  const [playError, setPlayError] = useState<string | null>(null);
  useEffect(() => onPlayingChange(setPlaying), []);
  useEffect(() => () => stopClip(), []);   // 离开这场会议就停
  async function play(i: number) {
    setPlayError(null);
    try {
      await toggleClip(`${meetingId}#${i}`, meetingId, segments[i]);
    } catch (e) {
      setPlayError(e instanceof Error ? e.message : String(e));
    }
  }
  const isPlaying = (i: number) => playing === `${meetingId}#${i}`;

  function start(i: number) {
    setEditing(i);
    setDraft(segments[i].text);
    setError(null);
  }
  async function save(i: number) {
    setSaving(true);
    setError(null);
    const before = segments[i].orig_text ?? segments[i].text;
    try {
      const seg = await meetingTranscriptEdit(meetingId, i, draft);
      setSegments(segments.map((s, j) => (j === i ? seg : s)));
      setEditing(null);
      const words = seg.orig_text ? changedWords(before, seg.text) : [];
      setSuggest(words.filter((w) => !remembered.includes(w)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }
  async function remember(w: string) {
    try {
      await meetingHotwordsRemember([w]);
      setRemembered([...remembered, w]);
      setSuggest(suggest.filter((x) => x !== w));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
        转写原文 · 点 ▶ 听那一句, 识别错的字点右边的「改」{edited > 0 && ` · 已改 ${edited} 句, 重新生成纪要时会用改后的`}
      </div>
      {suggest.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", fontSize: 12 }}>
          <span>记成常用热词 (以后新会议自动带上, 识别更准):</span>
          {suggest.map((w) => (
            <button key={w} type="button" onClick={() => void remember(w)}
                    style={{ ...linkBtn, fontSize: 12, padding: "2px 8px", border: "1px solid var(--catfish-cyan)", borderRadius: 4,
                             color: "var(--catfish-cyan)" }}>＋ {w}</button>
          ))}
          <button type="button" style={linkBtn} onClick={() => setSuggest([])}>不用</button>
        </div>
      )}
      {remembered.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--status-ok)" }}>✓ 已记成常用热词: {remembered.join("、")}</div>
      )}
      <div style={{ maxHeight: 420, overflowY: "auto", fontSize: 13, lineHeight: 1.6 }}>
        {segments.map((seg, i) => (
          <div key={i} className="meeting-seg"
               style={{ display: "flex", gap: 4, alignItems: "flex-start", padding: "1px 0", borderRadius: 4,
                        background: isPlaying(i) ? "var(--catfish-bg-cream, rgba(14,95,102,0.12))" : undefined }}>
            <button type="button" aria-label={isPlaying(i) ? `停止第 ${i + 1} 句` : `听第 ${i + 1} 句`} title={isPlaying(i) ? "停" : "听这一句"}
                    onClick={() => void play(i)}
                    style={{ ...linkBtn, fontSize: 11, padding: "2px 4px", color: isPlaying(i) ? "var(--catfish-cyan)" : "var(--catfish-text-muted)" }}>
              {isPlaying(i) ? "■" : "▶"}
            </button>
            <div style={{ flex: 1, minWidth: 0 }}>
              <span style={{ color: "var(--catfish-text-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{formatClock(seg.start)} </span>
              <span style={{ color: colors[seg.spk % colors.length], fontWeight: 600 }}>{speakerName(seg.spk, names)}: </span>
              {editing === i ? (
                <span style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                  <textarea aria-label="改这一句" value={draft} rows={2} autoFocus onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }}
                            style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit", fontSize: 13 }} />
                  <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <Btn kind="ghost" onClick={() => void play(i)}>{isPlaying(i) ? "■ 停" : "▶ 听这句"}</Btn>
                    <Btn kind="primary" disabled={saving} onClick={() => void save(i)}>{saving ? "保存中…" : "保存"}</Btn>
                    <Btn kind="ghost" disabled={saving} onClick={() => setEditing(null)}>取消</Btn>
                    {seg.orig_text && <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>原话: {seg.orig_text} (清空保存 = 改回原话)</span>}
                  </span>
                </span>
              ) : (
                <>
                  {seg.text}
                  {seg.orig_text && (
                    <span title={`识别出来的原话: ${seg.orig_text}`}
                          style={{ marginLeft: 6, fontSize: 10, color: "var(--catfish-cyan)", border: "1px solid var(--catfish-cyan)",
                                   borderRadius: 3, padding: "0 4px", whiteSpace: "nowrap" }}>改过</span>
                  )}
                </>
              )}
            </div>
            {editing !== i && (
              <button type="button" style={linkBtn} title="改这一句的错字" aria-label={`改第 ${i + 1} 句`} onClick={() => start(i)}>改</button>
            )}
          </div>
        ))}
      </div>
      {playError && <ErrorLine>放不了: {playError}</ErrorLine>}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
