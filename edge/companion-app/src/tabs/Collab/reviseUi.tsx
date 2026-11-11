/**
 * 群里一起改文档 (11/11, docs/DOC-REVISE-MERGE-PLAN.md) 的小零件:
 *   · ReviseStartDialog 「请大家修改」: 选请谁改、截止、说明。文件来自群里已有的文件, 或输入框旁「📝」新选的。
 *   · ReviseStartButton 输入框旁「📝」: 选一份文件 → 暂存 → 打开上面那个弹层。
 *   · useReviseDetect   拖进 / 选进输入框的文件带某一轮的记号 → 输入框上方提示「这是《…》v1 的修改, 交回给张三?」。
 *   · hiddenReviseStatus 卡片就在眼前时, 「谁在改 / 谁交了」不再单独一行 (写在卡片上了)。
 */
import { useRef, useState, type ChangeEvent } from "react";

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import { fmtNote, reviseDetect, reviseReturn, reviseStart } from "../../lib/tauri_room_revise";
import { Overlay, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import { stageFiles, type PickedFile } from "./RoomsDelivery";

const MERGEABLE: Record<string, string> = { docx: "docx", xlsx: "xlsx", pptx: "pptx", md: "text", markdown: "text", txt: "text", csv: "csv" };
const OLD: Record<string, string> = { doc: "docx", xls: "xlsx", ppt: "pptx", wps: "docx", et: "xlsx", dps: "pptx" };

export function fmtOfName(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return MERGEABLE[ext] ?? "whole";
}

/** 卡片在眼前时不单独显示的状态行 (合并版那条有文件, 一直显示) */
export function hiddenReviseStatus(messages: RoomMessage[]): Set<string> {
  const cards = new Set(messages.filter((m) => m.kind === "revise" && m.revise).map((m) => m.revise!.rev_id));
  return new Set(messages.filter((m) => m.kind === "revise_status" && m.revise_status && m.revise_status.state !== "merged"
                                        && cards.has(m.revise_status.rev_id)).map((m) => m.msg_id));
}

export type ReviseSource =
  | { msg_id: string; file_id: string; name: string; doc_id?: string; version?: number }
  | { stage: { name: string; stage_id: string } };

function sourceName(s: ReviseSource): string {
  return "stage" in s ? s.stage.name : s.name;
}

export function ReviseStartDialog({ roomId, me, members, source, onClose, onDone }: {
  roomId: string; me: string; members: RoomMember[]; source: ReviseSource;
  onClose: () => void; onDone: (hint: string) => void;
}) {
  const name = sourceName(source);
  const others = members.filter((m) => m.state === "joined" && m.email !== me);
  const [title, setTitle] = useState(name.replace(/\.[^.]+$/, "").replace(/ v\d+( 修订版)?$/, ""));
  const [chosen, setChosen] = useState<Set<string>>(new Set(others.map((m) => m.email)));
  const [deadline, setDeadline] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  const fmt = fmtOfName(name);
  const continuing = !("stage" in source) && source.doc_id;
  async function go() {
    setBusy(true);
    setError(null);
    try {
      const r = await reviseStart(roomId, {
        ...("stage" in source ? { files: [source.stage] } : { msg_id: source.msg_id, file_id: source.file_id, doc_id: source.doc_id }),
        editors: [...chosen], deadline: deadline || null, note, title,
      });
      onDone(r.hint ?? `已发出《${title}》, 请 ${chosen.size} 位同事修改; 大家交回后点卡片上的「合并」`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Overlay title={continuing ? `在 v${(source as { version?: number }).version ?? ""} 上再请大家修改` : "请大家修改"} onClose={onClose} width={520}>
      <span style={{ fontSize: 13 }}>📄 {name}</span>
      <span style={{ ...muted, color: fmt === "whole" ? "var(--status-warn, #d97706)" : undefined }}>
        {OLD[ext] ? `.${ext} 是老格式: 先另存为 .${OLD[ext]} 再发, 才能自动合并; 现在这样交回后只能整份选用。` : fmtNote(fmt)}
      </span>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
        文档名 (群里和合并版文件名都用它)
        <input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} style={inputStyle} />
      </label>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
        请谁改
        {others.length === 0 && <span style={muted}>群里还没有别人加入</span>}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px" }}>
          {others.map((m) => (
            <label key={m.email} style={{ display: "flex", gap: 4, alignItems: "center", cursor: "pointer" }} title={m.email}>
              <input type="checkbox" checked={chosen.has(m.email)}
                     onChange={(e) => setChosen((s) => { const n = new Set(s); if (e.target.checked) n.add(m.email); else n.delete(m.email); return n; })} />
              {m.name || m.email.split("@")[0]}
            </label>
          ))}
        </div>
      </div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
        截止 (可不填)
        <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} style={{ ...inputStyle, width: 180 }} />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
        说明 (可不填)
        <textarea value={note} maxLength={500} rows={2} placeholder="如: 重点看第三章报价" onChange={(e) => setNote(e.target.value)}
                  style={{ ...inputStyle, resize: "vertical" }} />
      </label>
      <span style={muted}>大家各自下载、用自己电脑上的软件改, 改完点「交回」—— 交回稿只发给你, 群里只显示谁交了; 合并在你电脑上做, 合好的新版本再发回群里。</span>
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy || !chosen.size || !title.trim()} onClick={() => void go()}>{busy ? "发送中…" : `发给 ${chosen.size} 人`}</Btn>
      </div>
    </Overlay>
  );
}

/** 输入框旁「📝」: 选一份文件请大家改 */
export function ReviseStartButton({ disabled, onStaged, onError }: {
  disabled?: boolean; onStaged: (s: ReviseSource) => void; onError: (e: string) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [prep, setPrep] = useState<number | null>(null);
  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      const [st] = await stageFiles([{ name: f.name, size: f.size, file: f }], (d, t) => setPrep(Math.floor((d / t) * 100)));
      onStaged({ stage: st });
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setPrep(null);
    }
  }
  return (
    <>
      <input ref={ref} type="file" style={{ display: "none" }} aria-label="选一份文档请大家修改" onChange={(e) => void pick(e)} />
      <button type="button" disabled={disabled || prep !== null} onClick={() => ref.current?.click()}
              title="发一份文档请大家各自修改, 交回后系统合并 (Word / Excel / PPT / Markdown)"
              style={{ border: "1px solid var(--catfish-border)", background: "transparent", borderRadius: "var(--radius-sm, 6px)",
                       cursor: disabled ? "default" : "pointer", padding: "4px 8px", fontSize: 13, color: "inherit" }}>
        {prep !== null ? `${prep}%` : "📝"}
      </button>
    </>
  );
}

const OOXML = /\.(docx|xlsx|pptx)$/i;

/** 选进 / 拖进输入框的文件里, 有没有某一轮请我改的交回稿 (带记号) */
export function useReviseDetect(roomId: string, onReturned: (text: string) => void) {
  const [found, setFound] = useState<{ file: PickedFile; stage_id: string; rev_id: string; title: string; version?: number; owner_name?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function check(files: PickedFile[]) {
    const f = files.find((x) => OOXML.test(x.name) && x.size <= 50 * 1024 * 1024);
    if (!f) return;
    try {
      const [st] = await stageFiles([f], () => undefined);
      const r = await reviseDetect(roomId, st.stage_id, f.name);
      if (r.rev_id) setFound({ file: f, stage_id: st.stage_id, rev_id: r.rev_id, title: r.title ?? "", version: r.version, owner_name: r.owner_name });
    } catch {
      /* 认不出就当普通文件发 */
    }
  }
  async function giveBack(confirmUnchanged = false): Promise<PickedFile | null> {
    if (!found) return null;
    setBusy(true);
    setError(null);
    try {
      const r = await reviseReturn(roomId, found.rev_id, { stage_id: found.stage_id, name: found.file.name, confirm_unchanged: confirmUnchanged });
      if (r.need_confirm === "unchanged") {
        setError("这份跟原版一样, 一点没改。确定交回「看过, 没改」? 再点一次「交回」。");
        setFound({ ...found });
        return null;
      }
      const f = found.file;
      setFound(null);
      onReturned(`已交回《${found.title}》给 ${found.owner_name ?? "发起人"}`);
      return f;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(false);
    }
  }
  const banner = (onRemove: (f: PickedFile) => void) => found && (
    <div style={{ ...muted, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "4px 8px", borderRadius: 6,
                  background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))", border: "1px solid var(--catfish-cyan)", color: "inherit" }}>
      <span>📝 {found.file.name} 是《{found.title}》v{found.version} 的修改 —— 交回给 {found.owner_name}?</span>
      <Btn kind="primary" disabled={busy} onClick={() => void giveBack(Boolean(error?.includes("一点没改"))).then((f) => f && onRemove(f))}>
        {busy ? "交回中…" : "交回"}
      </Btn>
      <Btn kind="ghost" onClick={() => { setFound(null); setError(null); }}>当普通文件发</Btn>
      {error && <span style={{ color: "var(--status-warn, #d97706)" }}>{error}</span>}
    </div>
  );
  return { check, banner };
}
