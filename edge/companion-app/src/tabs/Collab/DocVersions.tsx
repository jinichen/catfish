/**
 * 一份文档在群里的每一版 (11/11): 发起时的那份 + 每次合并版。打开任意一版; 跟上一版对比 (Word / Excel / PPT 出一份
 * 带修订痕迹的文件, 文本出对比文本), 写到下载目录用默认程序打开。
 *
 * 11/12: 最新一版能「发邮件」(VersionEmail.tsx); 合并出 vN 后又从 vN 发起的新一轮 (kind=restart) 挂在 vN 下面写
 * 「基于 vN 发起新一轮」, 不再单独列成第二个「vN · 发起」(看着像版本号重复)。
 */
import { useEffect, useState } from "react";

import { openFile } from "../../lib/tauri_app";
import { roomFileSave } from "../../lib/tauri_colleague_rooms";
import { reviseDiff, reviseVersions, type ReviseVersion } from "../../lib/tauri_room_revise";
import { Overlay, muted } from "./formsUi";
import VersionEmail from "./VersionEmail";
import { ErrorLine } from "./roomLinkUi";
import { timeLabel } from "./RoomsPanel";
import { fmtOfName } from "./reviseUi";

const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 12, padding: 0, color: "var(--catfish-cyan)" } as const;

export default function DocVersions({ roomId, docId, me = "", onClose }: { roomId: string; docId: string; me?: string; onClose: () => void }) {
  const [vs, setVs] = useState<ReviseVersion[] | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mailing, setMailing] = useState<ReviseVersion | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    reviseVersions(roomId, docId).then((r) => { setVs(r.versions); setTitle(r.title); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [roomId, docId]);
  async function run(key: string, fn: () => Promise<string>) {
    setBusy(key);
    setError(null);
    try {
      await openFile(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  const comparable = vs?.[0]?.files[0] ? fmtOfName(vs[vs.length - 1].files[0]?.name ?? "") !== "whole" : false;
  return (
    <Overlay title={`《${title || "文档"}》的版本`} onClose={onClose} width={560}>
      {!vs && !error && <span style={muted}>读取中…</span>}
      {error && <ErrorLine>{error}</ErrorLine>}
      {note && <span style={{ fontSize: 12, color: "var(--status-ok)" }}>✓ {note}</span>}
      {vs?.map((v, i) => {
        if (v.kind === "restart") return null;   // 挂在它那一版下面
        const prev = vs.slice(i + 1).find((x) => x.version < v.version);
        const restarts = vs.filter((x) => x.kind === "restart" && x.version === v.version);
        const latest = vs.find((x) => x.kind !== "restart") === v;
        return (
          <div key={v.msg_id} style={{ display: "flex", flexDirection: "column", gap: 4, padding: "6px 0", borderBottom: "1px solid var(--catfish-border)" }}>
            <span style={{ fontSize: 13 }}>
              <strong>v{v.version}</strong> · {timeLabel(v.at)} · {v.kind === "start" ? `${v.by} 发起` : `${v.by} 合并`}
            </span>
            {v.text && v.kind === "merged" && <span style={muted}>{v.text.split("\n").slice(1).join(" · ")}</span>}
            <span style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
              {v.files.map((f) => (
                <button key={f.id} type="button" style={link} disabled={busy !== null}
                        onClick={() => void run(f.id, async () => (await roomFileSave(roomId, f.id)).path)}>
                  {busy === f.id ? "打开中…" : `打开 ${f.name}`}
                </button>
              ))}
              {prev && comparable && (
                <button type="button" style={link} disabled={busy !== null}
                        onClick={() => void run(`d${v.version}`, async () => (await reviseDiff(roomId, docId, prev.version, v.version)).path)}>
                  {busy === `d${v.version}` ? "生成中…" : `跟 v${prev.version} 对比`}
                </button>
              )}
            </span>
            {restarts.map((r) => (
              <span key={r.msg_id} style={muted}>
                ↳ {timeLabel(r.at)} · {r.by} 基于 v{v.version} 发起新一轮修改{r.editors?.length ? ` (请了 ${r.editors.map((e) => e.name).join("、")})` : ""}
              </span>
            ))}
            {latest && v.files.length > 0 && (
              <span>
                <button type="button" style={{ ...link, fontWeight: 600 }} onClick={() => { setNote(null); setMailing(v); }}>✉ 发邮件 (最新一版)</button>
              </span>
            )}
          </div>
        );
      })}
      <span style={muted}>每一版都在群消息里; 过了群记录保留期限就看不到了。旧版本要发邮件: 点「打开」旁边的文件, 在文件夹里找到再附进邮件。</span>
      {mailing && (
        <VersionEmail roomId={roomId} title={title} version={mailing} me={me} onClose={() => setMailing(null)}
                      onDone={(t) => { setMailing(null); setNote(t); }} />
      )}
    </Overlay>
  );
}
