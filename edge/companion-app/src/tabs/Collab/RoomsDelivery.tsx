/**
 * 群聊 11/07: 没送到的挂在消息下面 / 群文件 / 中央太旧的细横条 (从 RoomsPanel 拆出来, 守 800 行)。
 *
 * 原来投递失败是一行红字插在群名和聊天之间 (把聊天往下推), 写的是邮箱 + 原始报错, 跟哪条消息无关, 换个群就没了
 * (11/07 鸿波: 体验不好)。现在 (参照 hermes 群聊「准备连续失败就在群里说一句、给重试」):
 *   - 在出事的那条消息 / 系统提示下面一行小字, 按原因说人话; 原始报错放鼠标悬停里。
 *   - 等对方升级 / 连不上中央: tool-bridge 自动补发 (colleague_rooms_outbox.py), 这里只显示「…后自动补发」;
 *     实在发不出去才给「重发」。补发成功那行变「✓ 已送到」, 3 秒后消失。
 *   - 中央太旧是整个群的事: 群名下面一条细横条说一次, 不在每条消息上重复。
 * 群文件: 消息下面一个文件一行, 收齐了「打开」(写到 ~/Downloads/鲶鱼群文件/<群名>/ 再用默认程序开, 员工自己点的)。
 */
import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { openFile, revealInFinder } from "../../lib/tauri_app";
import { roomFileSave, roomResend, type Delivery, type RoomFile, type RoomMember } from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";

const MAX_FILES = 5;
const MAX_TOTAL = 5 * 1024 * 1024;

export function sizeLabel(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function names(rows: Delivery[], members: RoomMember[]): string {
  const ns = rows.map((r) => members.find((m) => m.email === r.email)?.name || r.email.split("@")[0]);
  return ns.length > 3 ? `${ns.slice(0, 3).join("、")} 等 ${ns.length} 人` : ns.join("、");
}

/** 一条消息下面的送达情况, 按状态分组成几句话。纯函数, 测试用。 */
export function deliveryLines(rows: Delivery[], members: RoomMember[], hasFiles = false):
  { state: Delivery["state"]; text: string; emails: string[] }[] {
  const order: Delivery["state"][] = ["failed", "nokey", "too_old", "sending"];
  return order.flatMap((state) => {
    const g = rows.filter((r) => r.state === state);
    if (!g.length) return [];
    const who = names(g, members);
    const text = {
      failed: `${who}没收到`,
      nokey: `${who}还没打开新版鲶鱼, 打开后自动补发`,
      too_old: `${who}还没收到, 等中央升级后自动补发`,
      sending: hasFiles ? `正在把文件发给 ${who}` : `正在重试发给 ${who}`,
    }[state];
    return [{ state, text, emails: g.map((r) => r.email) }];
  });
}

const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 11, padding: 0, color: "var(--catfish-cyan)" } as const;

export function DeliveryLine({ roomId, msgId, rows, members, hasFiles, align, onChanged }: {
  roomId: string; msgId: string; rows: Delivery[] | undefined; members: RoomMember[]; hasFiles?: boolean;
  align: "flex-start" | "flex-end" | "center"; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [delivered, setDelivered] = useState(false);
  const had = useRef(Boolean(rows?.length));
  useEffect(() => {
    const now = Boolean(rows?.length);
    if (had.current && !now) {   // 刚补发成功: 「已送到」亮 3 秒
      setDelivered(true);
      const t = setTimeout(() => setDelivered(false), 3000);
      had.current = now;
      return () => clearTimeout(t);
    }
    had.current = now;
  }, [rows]);
  if (delivered) return <span style={{ ...muted, alignSelf: align, color: "var(--status-ok, #059669)" }}>✓ 已送到</span>;
  if (!rows?.length) return null;
  async function resend(email?: string) {
    setBusy(true);
    setErr(null);
    try {
      await roomResend(roomId, msgId, email);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      onChanged();
    }
  }
  const raw = rows.filter((r) => r.error).map((r) => `${r.email}: ${r.error}`).join("\n");
  return (
    <span style={{ display: "flex", flexDirection: "column", alignItems: align, gap: 1, maxWidth: "90%" }} title={raw || undefined}>
      {deliveryLines(rows, members, hasFiles).map((l) => (
        <span key={l.state} style={{ ...muted, color: l.state === "failed" ? "var(--status-warn, #d97706)" : undefined,
                                     display: "flex", gap: 6, flexWrap: "wrap", justifyContent: align }}>
          <span>{l.state === "sending" ? "⏳ " : l.state === "failed" ? "⚠ " : ""}{l.text}</span>
          {l.state === "failed" && (
            <button type="button" style={link} disabled={busy} onClick={() => void resend(l.emails.length === 1 ? l.emails[0] : undefined)}>
              {busy ? "重发中…" : "重发"}
            </button>
          )}
        </span>
      ))}
      {err && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>{err}</span>}
    </span>
  );
}

/** 消息里的文件: 一个一行。mine = 我发的 (气泡是主色, 字是白的)。 */
export function RoomFileList({ roomId, files, mine }: { roomId: string; files: RoomFile[]; mine: boolean }) {
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, string>>({});
  async function open(f: RoomFile, reveal = false) {
    setErr(null);
    try {
      const path = saved[f.id] ?? (await roomFileSave(roomId, f.id)).path;
      setSaved((s) => ({ ...s, [f.id]: path }));
      await (reveal ? revealInFinder(path) : openFile(path));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  const btn = { ...link, color: mine ? "inherit" : "var(--catfish-cyan)", textDecoration: mine ? "underline" : "none" };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
      {files.map((f) => (
        <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12,
                                 padding: "4px 8px", borderRadius: 6, background: mine ? "rgba(255,255,255,0.16)" : "var(--catfish-bg-elevated)",
                                 border: mine ? "1px solid rgba(255,255,255,0.3)" : "1px solid var(--catfish-border)" }}>
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }} title={f.name}>
            📄 {f.name}
          </span>
          <span style={{ opacity: 0.75 }}>{sizeLabel(f.size)}</span>
          {f.have ? (
            <>
              <button type="button" style={btn} onClick={() => void open(f)}>打开</button>
              <button type="button" style={btn} onClick={() => void open(f, true)}>在文件夹中显示</button>
            </>
          ) : (
            <span style={{ opacity: 0.75 }}>接收中 {f.got ?? 0}/{f.n}</span>
          )}
        </div>
      ))}
      {err && <span style={{ fontSize: 11, color: mine ? "inherit" : "var(--status-warn, #d97706)" }}>{err}</span>}
    </div>
  );
}

export interface PickedFile { name: string; size: number; data: string }

async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** 挑文件 (📎): 读成 base64 交给 tool-bridge 分块加密发。超上限当场说。 */
export async function pickFiles(list: FileList | File[], already: PickedFile[]): Promise<{ files: PickedFile[]; error?: string }> {
  const files = [...already];
  for (const f of Array.from(list)) {
    if (files.length >= MAX_FILES) return { files, error: `一条消息最多带 ${MAX_FILES} 个文件` };
    if (files.reduce((n, x) => n + x.size, 0) + f.size > MAX_TOTAL) {
      return { files, error: `文件合计最多 ${MAX_TOTAL / 1024 / 1024}MB (${f.name} 放不下) —— 大文件用邮件或网盘` };
    }
    if (!f.size) return { files, error: `${f.name} 是空文件` };
    files.push({ name: f.name, size: f.size, data: await toBase64(f) });
  }
  return { files };
}

export function AttachButton({ disabled, onPick }: { disabled?: boolean; onPick: (files: FileList) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" multiple style={{ display: "none" }} aria-label="选文件发到群里"
             onChange={(e: ChangeEvent<HTMLInputElement>) => { if (e.target.files?.length) onPick(e.target.files); e.target.value = ""; }} />
      <button type="button" disabled={disabled} onClick={() => ref.current?.click()} title={`发文件 (最多 ${MAX_FILES} 个, 合计 5MB; 加密后经中央转交, 取走即清空)`}
              style={{ border: "1px solid var(--catfish-border)", background: "transparent", borderRadius: "var(--radius-sm, 6px)",
                       cursor: disabled ? "default" : "pointer", padding: "4px 8px", fontSize: 13, color: "inherit" }}>
        📎
      </button>
    </>
  );
}

export function PickedChips({ files, onRemove }: { files: PickedFile[]; onRemove: (i: number) => void }) {
  if (!files.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {files.map((f, i) => (
        <span key={`${f.name}-${i}`} style={{ ...muted, display: "flex", alignItems: "center", gap: 4, padding: "2px 8px",
                                              border: "1px solid var(--catfish-border)", borderRadius: 12, maxWidth: 260 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.name}>📄 {f.name}</span>
          <span>{sizeLabel(f.size)}</span>
          <button type="button" aria-label={`不发 ${f.name}`} onClick={() => onRemove(i)}
                  style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, color: "inherit" }}>✕</button>
        </span>
      ))}
    </div>
  );
}

/** 整个群的问题: 群名下面一条细横条 (不挤开聊天, 不是红字)。 */
export function IssueBar({ issue }: { issue?: "too_old" | null }) {
  if (issue !== "too_old") return null;
  return (
    <div style={{ ...muted, flexShrink: 0, padding: "3px 8px", borderRadius: "var(--radius-sm, 6px)",
                  background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))", color: "var(--status-warn, #d97706)" }}>
      中央端版本太旧, 有的消息暂时发不出 —— 请管理员升级中央端, 升级后会自动补发。
    </div>
  );
}
