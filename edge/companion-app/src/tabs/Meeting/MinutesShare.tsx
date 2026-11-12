/**
 * 纪要的分享 (11/12 鸿波「是不是应该有个分享或者邮件」): 复制 / 发邮件 / 发到协同群 / 导出 Word。
 *
 * 发出去都要员工自己点:
 *  · 发邮件: 打开跟「邮件」页同一个写信面板 (ComposeCore) —— 主题、正文 (纪要纯文本)、附件 (纪要 Word) 填好,
 *    收件人员工自己填; 存草稿 / 发送跟「邮件」页一样 (发送要点两次确认)。
 *  · 发到协同群: 选群、看一眼要发的文字, 点「发送」才发 (群消息只在员工点了之后才发, CENTRAL-EDGE-DATA-BOUNDARY 第 4 条)。
 * 收件人 (11/12 第二版): 说话人改过真名的, 按名字去知识库找邮箱 (wiki_people.py) 自动填; 找不到 / 一个名字对上两个邮箱的
 * 在上面列出来让员工补。收件人框里打字也能从知识库给候选 (RecipientInput)。填进去的员工都看得到、能删, 发送照旧员工点。
 */

/** 说话人名字 → 收件人: 只对上一个邮箱的填进去; 没找到 / 对上好几个的另列 */
export function recipientsFromSpeakers(names: string[], matches: Record<string, string[]>) {
  const filled: { name: string; email: string }[] = [];
  const missing: string[] = [];
  const ambiguous: { name: string; emails: string[] }[] = [];
  for (const n of names) {
    const es = matches[n] ?? [];
    if (es.length === 1) { if (!filled.some((f) => f.email === es[0])) filled.push({ name: n, email: es[0] }); }
    else if (es.length > 1) ambiguous.push({ name: n, emails: es });
    else missing.push(n);
  }
  return { filled, missing, ambiguous };
}
import { useEffect, useState } from "react";

import { ModalShell } from "../../components/ModalShell";
import { openFile, revealInFinder } from "../../lib/tauri_app";
import { emailAccountsFetch } from "../../lib/tauri_briefing";
import { roomList, roomSend, type RoomSummary } from "../../lib/tauri_colleague_rooms";
import type { EmailAccountItem } from "../../lib/tauri_cron";
import { meetingMinutesDocx, meetingMinutesStage, meetingSpeakers, type MeetingMeta } from "../../lib/tauri_meeting";
import { wikiPeopleEmails } from "../../lib/tauri_wiki_people";
import { useAgentStore } from "../../store/agent";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import ComposeCore from "../Email/components/ComposeCore";
import { minutesPlainText } from "./meetingHelpers";

const ROOM_TEXT_MAX = 4000;   // 跟 tool-bridge colleague_rooms.TEXT_MAX 一致

export function minutesSubject(meta: MeetingMeta): string {
  return `${meta.title || "会议"} 会议纪要 (${meta.created_at.slice(0, 10)})`;
}

export default function MinutesShare({ meta, markdown, onNote, onError }: {
  meta: MeetingMeta; markdown: string; onNote: (text: string) => void; onError: (text: string) => void;
}) {
  const [dialog, setDialog] = useState<"email" | "room" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const text = minutesPlainText(markdown);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      onNote("纪要已复制, 可以直接贴到微信或群里");
    } catch (e) {
      onError(`复制失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  async function exportWord() {
    setBusy("word");
    try {
      const r = await meetingMinutesDocx(meta.id);
      await revealInFinder(r.path).catch(() => openFile(r.path));
      onNote(`已存成 Word: ${r.name}`);
    } catch (e) {
      onError(`存 Word 失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <Btn kind="ghost" onClick={() => void copy()}>复制</Btn>
      <Btn kind="ghost" onClick={() => setDialog("email")}>发邮件</Btn>
      <Btn kind="ghost" onClick={() => setDialog("room")}>发到协同群</Btn>
      <Btn kind="ghost" disabled={busy === "word"} onClick={() => void exportWord()}>{busy === "word" ? "生成中…" : "导出 Word"}</Btn>
      {dialog === "email" && (
        <EmailDialog meta={meta} body={text} onClose={() => setDialog(null)}
                     onDone={(t) => { setDialog(null); onNote(t); }} />
      )}
      {dialog === "room" && (
        <RoomDialog meta={meta} body={text} onClose={() => setDialog(null)}
                    onDone={(t) => { setDialog(null); onNote(t); }} />
      )}
    </>
  );
}

function EmailDialog({ meta, body, onClose, onDone }: {
  meta: MeetingMeta; body: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const agentName = useAgentStore((s) => s.name);
  const personality = useAgentStore((s) => s.personality);
  const [account, setAccount] = useState<string | undefined>(undefined);
  const [to, setTo] = useState("");
  const [who, setWho] = useState<ReturnType<typeof recipientsFromSpeakers> | null>(null);
  const [named, setNamed] = useState(true);
  const [attachment, setAttachment] = useState<{ path: string; name: string; size: number } | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 账号: 默认那个; 附件: 纪要 Word (生成失败就不带附件, 正文照样有纪要)
    // 收件人: 说话人改过的真名 → 知识库里找邮箱
    const people = meetingSpeakers(meta.id).then(async (sp) => {
      const names = [...new Set(Object.values(sp).map((n) => n.trim()).filter(Boolean))];
      if (!names.length) return { names, matches: {} as Record<string, string[]> };
      return { names, matches: (await wikiPeopleEmails(names)).matches };
    });
    void Promise.allSettled([emailAccountsFetch(), meetingMinutesDocx(meta.id), people]).then(([accs, docx, ppl]) => {
      if (ppl.status === "fulfilled") {
        setNamed(ppl.value.names.length > 0);
        const r = recipientsFromSpeakers(ppl.value.names, ppl.value.matches);
        setWho(r);
        setTo(r.filled.map((f) => f.email).join(", "));
      }
      if (accs.status === "fulfilled") {
        try {
          const list = JSON.parse(accs.value) as EmailAccountItem[];
          setAccount(list.find((a) => a.is_default)?.address || list[0]?.address);
        } catch { /* 读不到账号就让 ComposeCore 用缺省 */ }
      }
      if (docx.status === "fulfilled") setAttachment(docx.value);
      else setError(`没生成 Word 附件 (${String(docx.reason)}), 正文里有纪要全文`);
      setReady(true);
    });
  }, [meta.id]);

  return (
    <ModalShell onClose={onClose} maxWidth={760} themed>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, color: "var(--catfish-text)" }}>
        <strong style={{ fontSize: 15 }}>发邮件 · {meta.title}</strong>
        <RecipientNote named={named} who={who} />
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
          收件人框里打名字, 会从知识库里给候选。可以先存草稿, 也可以直接发送 —— 发送要点两次确认。
        </span>
        {error && <span style={{ fontSize: 12, color: "var(--status-warn)" }}>{error}</span>}
        {!ready ? <span style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>正在准备…</span> : (
          <div style={{ display: "flex", flexDirection: "column", minHeight: 420, maxHeight: "70vh", overflowY: "auto" }}>
            <ComposeCore
              isOpen
              onClose={onClose}
              initialTo={to}
              initialSubject={minutesSubject(meta)}
              initialBody={body}
              initialAttachments={attachment ? [attachment] : []}
              inReplyToMsgId={null}
              account={account}
              originalMessage={null}
              agentName={agentName}
              agentPersonality={personality}
              resetKey={`meeting-${meta.id}`}
              onSaveDraftSuccess={() => onDone("已存进邮件草稿箱, 去「邮件」或邮件 App 里检查后发送")}
              onSendSuccess={() => onDone("邮件已发送")}
            />
          </div>
        )}
      </div>
    </ModalShell>
  );
}

function RecipientNote({ named, who }: { named: boolean; who: ReturnType<typeof recipientsFromSpeakers> | null }) {
  const muted = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.6 } as const;
  if (!named) return <span style={muted}>说话人还是「说话人 1、2……」, 没法按名字找邮箱 —— 在会议页把说话人改成真名并保存, 再点「发邮件」就会自动填。</span>;
  if (!who) return null;
  return (
    <span style={{ ...muted, display: "flex", flexDirection: "column", gap: 2 }}>
      {who.filled.length > 0 && <span style={{ color: "var(--status-ok)" }}>已按说话人从知识库填好: {who.filled.map((f) => f.name).join("、")}</span>}
      {who.ambiguous.map((a) => (
        <span key={a.name} style={{ color: "var(--status-warn)" }}>{a.name} 在知识库里有 {a.emails.length} 个邮箱 ({a.emails.join(" / ")}), 没自动填, 请选一个填进去</span>
      ))}
      {who.missing.length > 0 && <span>知识库里没找到邮箱: {who.missing.join("、")} —— 请手动填, 或在知识库里给他们的人物页补上邮箱</span>}
    </span>
  );
}

function RoomDialog({ meta, body, onClose, onDone }: {
  meta: MeetingMeta; body: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null);
  const [roomId, setRoomId] = useState("");
  const [text, setText] = useState(() => (body.length > ROOM_TEXT_MAX ? `${body.slice(0, ROOM_TEXT_MAX - 40)}\n…(全文见附件 Word)` : body));
  const [withWord, setWithWord] = useState(body.length > ROOM_TEXT_MAX);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    roomList()
      .then((r) => {
        const mine = r.rooms.filter((x) => x.my_state === "joined");
        setRooms(mine);
        if (mine.length) setRoomId(mine[0].room_id);
      })
      .catch((e) => { setRooms([]); setError(String(e)); });
  }, []);

  async function send() {
    setSending(true);
    setError(null);
    try {
      const files = withWord ? [await meetingMinutesStage(meta.id)] : undefined;
      const r = await roomSend(roomId, text.trim(), [], null, files);
      const name = rooms?.find((x) => x.room_id === roomId)?.name ?? "";
      onDone(`已发到群「${name}」${r.failed.length ? ` (${r.failed.length} 人暂时没送到, 会自动重发)` : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const tooLong = text.length > ROOM_TEXT_MAX;
  return (
    <ModalShell onClose={onClose} maxWidth={640} themed>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, color: "var(--catfish-text)", fontSize: 13 }}>
        <strong style={{ fontSize: 15 }}>发到协同群 · {meta.title}</strong>
        {rooms === null ? <span style={{ color: "var(--catfish-text-muted)" }}>正在读群列表…</span>
          : rooms.length === 0 ? <span style={{ color: "var(--catfish-text-muted)" }}>你还没加入任何协同群 —— 先在「协同」页建一个群或接受邀请。</span>
          : (
            <>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                发到哪个群
                <select aria-label="选群" value={roomId} onChange={(e) => setRoomId(e.target.value)} style={inputStyle}>
                  {rooms.map((r) => <option key={r.room_id} value={r.room_id}>{r.name} ({r.joined} 人)</option>)}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                要发的文字 (可以改)
                <textarea aria-label="要发的文字" value={text} rows={10} onChange={(e) => setText(e.target.value)}
                          style={{ ...inputStyle, resize: "vertical", lineHeight: 1.6, fontFamily: "inherit" }} />
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input type="checkbox" checked={withWord} onChange={(e) => setWithWord(e.target.checked)} />
                同时附上纪要 Word 文件
              </label>
              {tooLong && <span style={{ color: "var(--status-warn)", fontSize: 12 }}>一条群消息最多 {ROOM_TEXT_MAX} 字, 现在 {text.length} 字 —— 删短一点, 全文让大家看附件。</span>}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <Btn kind="ghost" onClick={onClose}>取消</Btn>
                <Btn kind="primary" disabled={sending || !roomId || !text.trim() || tooLong} onClick={() => void send()}>
                  {sending ? "发送中…" : "发送"}
                </Btn>
              </div>
            </>
          )}
        {error && <ErrorLine>{error}</ErrorLine>}
      </div>
    </ModalShell>
  );
}
