/**
 * 群里一起改文档 (11/11) 的三种消息:
 *   revise         卡片「📝 请修改《…》」+ 原文件 + 每人状态。被请的人: 下载修改 / 交回 / 交别的文件 / 不改了;
 *                  发起人: 合并 / 催一下 / 结束 / 版本。状态从 revise_progress 取 (群消息一变就重取)。
 *   revise_return  交回稿 (只有发起人和交回的人自己看得到): 谁交回的 + 文件 + 留言。
 *   revise_status  合并版那条 (带新版本文件, 一直显示, 能「接着改这一版」); 其它状态只在卡片不在眼前时单独一行。
 */
import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { refreshRevisePending } from "../../lib/colleagueRoomsStore";
import { openFile } from "../../lib/tauri_app";
import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import {
  PERSON_STATE_WORD, reviseClose, reviseDecline, reviseOpen, reviseProgress, reviseRemind, reviseReturn,
  type ReviseProgress, type RevisePerson, type SavedElsewhere,
} from "../../lib/tauri_room_revise";
import { muted } from "./formsUi";
import { Confirm } from "./RoomsExtras";
import { DeliveryLine, RoomFileList, stageFiles } from "./RoomsDelivery";
import { labelOf, shortWhen, timeLabel } from "./RoomsPanel";
import type { ReviseSource } from "./reviseUi";

const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 11, padding: 0, color: "var(--catfish-cyan)" } as const;
const oneLine = { ...muted, alignSelf: "center", maxWidth: "90%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

export default function RoomReviseMessage({ m, me, members, roomId, tick, onChanged, onNotice, onMerge, onVersions, onRevise }: {
  m: RoomMessage; me: string; members: RoomMember[]; roomId: string;
  /** 群消息变了 (有人交回 / 状态变了) 就重取进度 */
  tick: number;
  onChanged: () => void; onNotice: (text: string) => void;
  onMerge: (revId: string) => void; onVersions: (docId: string) => void; onRevise: (s: ReviseSource) => void;
}) {
  const mine = m.from === me;
  if (m.kind === "revise" && m.revise) {
    return <ReviseCardView m={m} me={me} members={members} roomId={roomId} tick={tick} onChanged={onChanged} onNotice={onNotice}
                           onMerge={onMerge} onVersions={onVersions} />;
  }
  if (m.kind === "revise_return" && m.revise_return) {
    const r = m.revise_return;
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
        <span style={muted}>
          {mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)} · {mine ? "只发给发起人" : "只发给你, 群里其他人看不到"}
        </span>
        <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      border: "1px dashed var(--catfish-border)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.03))" }}>
          {r.unchanged ? "👀 " : "📨 "}{m.text}
          {m.files?.length ? <RoomFileList roomId={roomId} files={m.files} mine={false} /> : null}
        </div>
        <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} align={mine ? "flex-end" : "flex-start"} onChanged={onChanged} />
      </div>
    );
  }
  const st = m.revise_status;
  if (st?.state === "merged") {
    const clean = (m.files ?? []).find((f) => !f.name.includes("修订版")) ?? m.files?.[0];
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
        <span style={muted}>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
        <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      border: "1px solid var(--status-ok, #059669)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.03))" }}>
          {m.text}
          {m.files?.length ? <RoomFileList roomId={roomId} files={m.files} mine={false} /> : null}
          <span style={{ display: "flex", gap: 10, marginTop: 4 }}>
            <button type="button" style={link} onClick={() => onVersions(st.doc_id)}>版本记录</button>
            {clean?.have && (
              <button type="button" style={link}
                      onClick={() => onRevise({ msg_id: m.msg_id, file_id: clean.id, name: clean.name, doc_id: st.doc_id, version: st.version })}>
                在这一版上再请大家改
              </button>
            )}
          </span>
        </div>
        {m.files?.some((f) => f.name.includes("修订版")) && (
          <span style={muted}>「修订版」打开就能看到谁改了哪里, 可以在 Word / WPS 里一条条接受或拒绝</span>
        )}
        <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} align={mine ? "flex-end" : "flex-start"} onChanged={onChanged} />
      </div>
    );
  }
  const icon = st?.state === "returned" ? "📨" : st?.state === "declined" ? "↩️" : st?.state === "remind" ? "⏰" : st?.state === "closed" ? "📝" : "✏️";
  return <span style={oneLine} title={m.text}>{icon} {m.text} · {timeLabel(m.at)}</span>;
}

function ReviseCardView({ m, me, members, roomId, tick, onChanged, onNotice, onMerge, onVersions }: {
  m: RoomMessage; me: string; members: RoomMember[]; roomId: string; tick: number;
  onChanged: () => void; onNotice: (text: string) => void; onMerge: (revId: string) => void; onVersions: (docId: string) => void;
}) {
  const c = m.revise!;
  const mine = c.owner === me;
  const [pr, setPr] = useState<ReviseProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ask, setAsk] = useState<null | "unchanged" | "open" | "decline" | "close">(null);
  /** 员工已经点过「确认」的 (文件开着 / 没改), 再点交回时带上 */
  const [okd, setOkd] = useState<{ open?: boolean; unchanged?: boolean }>({});
  const [elsewhere, setElsewhere] = useState<SavedElsewhere | null>(null);
  const [more, setMore] = useState(false);
  const [showPeople, setShowPeople] = useState(false);
  const [reason, setReason] = useState("");
  const [picked, setPicked] = useState<{ name: string; stage_id: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let alive = true;
    reviseProgress(roomId, c.rev_id).then((p) => { if (alive) setPr(p); }).catch((e) => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [roomId, c.rev_id, tick]);

  async function run<T>(fn: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      setPr(await reviseProgress(roomId, c.rev_id));
      onChanged();
      void refreshRevisePending();   // 协同事项里的「请你修改」马上跟着变
      return r;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    const r = await run(() => reviseOpen(roomId, c.rev_id));
    if (r) {
      await openFile(r.path).catch(() => undefined);
      onNotice(r.again ? "打开了你之前下载的那份 (接着改)"
        : `已下载到 ${r.path.split("/").slice(-2).join("/")} 并打开; 改完存盘, 回这里点「交回」`
          + " (用 Keynote / Pages / Numbers 打开的, 改完要「文件 → 导出为」Office 格式, 覆盖这个文件)");
    }
  }
  async function giveBack(flags: { open?: boolean; unchanged?: boolean } = {}, stage = picked) {
    const r = await run(() => reviseReturn(roomId, c.rev_id, { confirm_unchanged: Boolean(flags.unchanged), confirm_open: Boolean(flags.open),
                                                               ...(stage ? stage : {}) }));
    if (!r) return;
    if (r.need_confirm) { setOkd(flags); setElsewhere(r.saved_elsewhere ?? null); setAsk(r.need_confirm); return; }
    setAsk(null);
    setOkd({});
    setPicked(null);
    onNotice(`已交回《${c.title}》, 只发给了 ${labelOf(members, c.owner)}`);
  }
  async function pickOther(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      const [st] = await stageFiles([{ name: f.name, size: f.size, file: f }], () => undefined);
      setPicked(st);
      await giveBack({}, st);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const all: RevisePerson[] = pr?.people ?? c.editors.map((e) => ({ email: e, name: labelOf(members, e), state: "pending" as const, at: null, late: false }));
  const people = all.filter((p) => !p.self);   // 发起人自己改的那份在合并面板里选, 不在卡片上
  const done = people.filter((p) => p.state === "returned" || p.state === "unchanged").length;
  const my = mine ? undefined : people.find((p) => p.email === me);
  const ended = pr?.closed;
  const waiting = people.filter((p) => p.state === "pending" || p.state === "editing").length;
  const lateCount = (pr?.people ?? []).filter((p) => p.after_merge).length;
  const side = mine ? "flex-end" : "flex-start";
  // 11/11 鸿波「太复杂了」: 合并版发出、也没有迟到的 → 收成一行, 不再挂按钮
  if (pr?.merged && !lateCount) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: side, gap: 2 }}>
        <span style={{ ...muted, display: "flex", gap: 8 }}>
          <span>📝 《{c.title}》已出 v{pr.merged.version}</span>
          <button type="button" style={link} onClick={() => onVersions(c.doc_id)}>版本记录</button>
        </span>
      </div>
    );
  }
  const returned = my && (my.state === "returned" || my.state === "unchanged");
  // 每种状态一个主按钮, 其余收进「更多」
  const primary: { label: string; run: () => void; off?: boolean } | null = ended ? null
    : mine ? (pr?.can_merge ? { label: lateCount ? `合并迟到的 ${lateCount} 份` : pr.all_in ? "都交了, 合并" : `合并 (${done})`, run: () => onMerge(c.rev_id) }
              : waiting > 0 ? { label: "催一下", run: () => void run(() => reviseRemind(roomId, c.rev_id)) } : null)
    : my && my.state !== "declined" && !returned
      ? (pr?.local_path ? { label: "交回", run: () => void giveBack() } : { label: "下载修改", run: () => void download() })
      : null;
  const extra: [string, () => void][] = [];
  if (!ended && my && my.state !== "declined") {
    if (pr?.local_path) extra.push(["接着改", () => void download()]);
    if (returned && pr?.local_path) extra.push(["再交一次", () => void giveBack()]);
    extra.push(["交别的文件", () => fileRef.current?.click()]);
    if (!returned) extra.push(["不改了", () => setAsk("decline")]);
  }
  if (!ended && mine) {
    if (pr?.can_merge && waiting > 0) extra.push(["催一下", () => void run(() => reviseRemind(roomId, c.rev_id))]);
    extra.push(["结束", () => setAsk("close")]);
  }
  extra.push(["版本", () => onVersions(c.doc_id)]);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: side, gap: 2 }}>
      <span style={{ ...muted, display: "flex", flexWrap: "wrap", gap: 8, justifyContent: side, maxWidth: "90%" }}>
        <span>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
        <button type="button" style={{ ...link, color: "inherit" }} onClick={() => setShowPeople((v) => !v)} title="点开看每个人的状态">
          已交 {done}/{people.length}{ended ? " · 已结束" : c.deadline ? ` · ${shortWhen(c.deadline)} 截止` : ""}{returned ? " · 我交了" : ""} {showPeople ? "▴" : "▾"}
        </button>
        {showPeople && people.map((p) => (
          <span key={p.email} title={p.email + (p.late ? " · 逾期交回" : "")}
                style={{ color: p.state === "returned" ? "var(--status-ok, #059669)" : p.late ? "var(--status-warn, #d97706)" : undefined }}>
            {p.email === me ? "我" : p.name} {PERSON_STATE_WORD[p.state]}{p.late ? " (逾期)" : ""}
          </span>
        ))}
      </span>
      <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: mine ? "var(--catfish-cyan)" : "var(--catfish-bg-subtle, rgba(0,0,0,0.05))", color: mine ? "white" : "inherit",
                    border: my && (my.state === "pending" || my.state === "editing") && !ended ? "1px solid var(--status-warn, #d97706)" : "1px solid transparent" }}>
        {m.text}
        {m.files?.length ? <RoomFileList roomId={roomId} files={m.files} mine={mine} /> : null}
      </div>
      <span style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: side }}>
        {primary && (
          <button type="button" disabled={busy || primary.off} onClick={primary.run}
                  style={{ border: "1px solid var(--catfish-cyan)", background: "transparent", color: "var(--catfish-cyan)", borderRadius: 6,
                           cursor: "pointer", fontSize: 12, padding: "2px 10px", fontWeight: 600 }}>
            {primary.label}
          </button>
        )}
        <button type="button" style={{ ...link, color: "var(--catfish-text-muted)" }} onClick={() => setMore((v) => !v)}>{more ? "收起" : "更多"}</button>
        {more && extra.map(([label, fn]) => (
          <button key={label} type="button" style={link} disabled={busy} onClick={fn}>{label}</button>
        ))}
        <input ref={fileRef} type="file" style={{ display: "none" }} aria-label="选改好的文件交回" onChange={(e) => void pickOther(e)} />
      </span>
      {error && <span style={{ ...muted, color: "var(--status-warn, #d97706)", maxWidth: "78%" }}>{error}</span>}
      <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} hasFiles={Boolean(m.files?.length)}
                    align={mine ? "flex-end" : "flex-start"} onChanged={onChanged} />
      {ask === "unchanged" && elsewhere && (
        <Confirm title={`改动存在了 ${elsewhere.app} 文件里`}
                 text={`你是用 ${elsewhere.app} 改的: 改动存进了「${elsewhere.saved.split("/").pop()}」, 下载的那份没变。`
                   + `在 ${elsewhere.app} 里点「文件 → 导出为 → ${elsewhere.office}」, 存到「${elsewhere.target}」覆盖它, 再回来点「交回」。`}
                 ok="不用了, 就当没改" busy={busy} onClose={() => setAsk(null)} onOk={() => void giveBack({ ...okd, unchanged: true })} />
      )}
      {ask === "unchanged" && !elsewhere && (
        <Confirm title="跟原版一样" text={`这份《${c.title}》跟原版一样, 一点没改。交回「看过, 没改」?`} ok="确定" busy={busy}
                 onClose={() => setAsk(null)} onOk={() => void giveBack({ ...okd, unchanged: true })} />
      )}
      {ask === "open" && (
        <Confirm title="文件还开着" text={`《${c.title}》还在 Word / WPS 里开着 —— 改了没存的话, 交上去的是没改的旧内容。先存好并关掉文件再交; 已经存好了也可以直接交。`}
                 ok="已经存好了, 交" busy={busy} onClose={() => setAsk(null)} onOk={() => void giveBack({ ...okd, open: true })} />
      )}
      {ask === "close" && (
        <Confirm title="结束修改" text={`结束《${c.title}》这一轮? 之后大家不能再交回 (已经交的还能合并)。`} ok="结束" busy={busy}
                 onClose={() => setAsk(null)} onOk={() => void run(() => reviseClose(roomId, c.rev_id)).then(() => setAsk(null))} />
      )}
      {ask === "decline" && (
        <div style={{ display: "flex", gap: 6, alignItems: "center", maxWidth: "78%" }}>
          <input value={reason} maxLength={200} placeholder="说一句原因 (可不填)" onChange={(e) => setReason(e.target.value)}
                 style={{ fontSize: 12, padding: "3px 6px", border: "1px solid var(--catfish-border)", borderRadius: 4,
                          background: "var(--catfish-bg)", color: "var(--catfish-text)" }} />
          <button type="button" style={link} disabled={busy}
                  onClick={() => void run(() => reviseDecline(roomId, c.rev_id, reason)).then(() => setAsk(null))}>确定不改</button>
          <button type="button" style={{ ...link, color: "var(--catfish-text-muted)" }} onClick={() => setAsk(null)}>算了</button>
        </div>
      )}
    </div>
  );
}
