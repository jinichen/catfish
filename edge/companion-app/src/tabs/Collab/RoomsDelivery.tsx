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
import { createContext, useContext, useEffect, useRef, useState, type ChangeEvent } from "react";

import { openFile, revealInFinder } from "../../lib/tauri_app";
import { roomFileRetry, roomFileSave, roomResend, roomStage, type Delivery, type RoomFile, type RoomMember } from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";
import { _splitFilename } from "../Email/components/helpers";

const MAX_FILES = 5;
/** 11/07 合计 5MB 以内走邮筒分块; 超过走中央暂存一份 (单个 / 合计 200MB, 跟中央默认上限一致) */
const MAX_FILE = 200 * 1024 * 1024;
const MAX_TOTAL = 200 * 1024 * 1024;
const PART = 1024 * 1024;

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

/** 「等对方打开新版 / 等中央升级后自动补发」这种不用员工管的, 只挂在群里最后一条有这情况的消息下面
 *  (RoomsPanel 给出那条的 msg_id)。原来每条消息下面都重复同一句, 版本越多越吵 (11/13)。
 *  没送到 / 正在发 照旧每条都显示 —— 那是这一条自己的事。没有 Provider (单独渲染一条) 时全显示。 */
export const QuietDeliveryContext = createContext<string | null | undefined>(undefined);
const QUIET: Delivery["state"][] = ["nokey", "too_old"];
/** 群里最后一条带「自动补发」的消息。纯函数, 测试用。 */
export function lastQuietDelivery(messages: { msg_id: string; delivery?: Delivery[] }[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].delivery?.some((r) => QUIET.includes(r.state))) return messages[i].msg_id;
  }
  return null;
}

export function DeliveryLine({ roomId, msgId, rows, members, hasFiles, align, onChanged }: {
  roomId: string; msgId: string; rows: Delivery[] | undefined; members: RoomMember[]; hasFiles?: boolean;
  align: "flex-start" | "flex-end" | "center"; onChanged: () => void;
}) {
  const quietAt = useContext(QuietDeliveryContext);
  const all = rows;
  if (quietAt !== undefined && quietAt !== msgId) rows = rows?.filter((r) => !QUIET.includes(r.state));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [delivered, setDelivered] = useState(false);
  const had = useRef(Boolean(all?.length));
  useEffect(() => {
    const now = Boolean(all?.length);
    if (had.current && !now) {   // 刚补发成功: 「已送到」亮 3 秒
      setDelivered(true);
      const t = setTimeout(() => setDelivered(false), 3000);
      had.current = now;
      return () => clearTimeout(t);
    }
    had.current = now;
  }, [all]);
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

/** 文件旁边那句状态 (收好了 / 只在本机的不写)。纯函数, 测试用。 */
export function fileState(f: RoomFile): string {
  const prog = `${f.got ?? 0}/${f.n}`;
  switch (f.big) {
    case "uploading": return `上传中 ${prog}`;
    case "waiting": return "等对方传完";
    case "downloading": return `接收中 ${prog}`;
    case "gone": return "中央已删 (超过 7 天没取), 收不到了";
    case "failed": return "没收下来";
    case "sent": case "local": case "ready": return "";
    default: return f.have ? "" : `接收中 ${prog}`;
  }
}

/** 消息里的文件: 一个一行。mine = 我发的 (气泡是主色, 字是白的)。 */
export function RoomFileList({ roomId, files, mine, onRevise, tagOf }: {
  roomId: string; files: RoomFile[]; mine: boolean;
  /** 文件名前面的小标签 + 悬停说明 (合并版卡片: 干净版 / 修订版 —— 两个文件名几乎一样, 截断后分不出来) */
  tagOf?: (f: RoomFile) => { tag: string; tip?: string } | undefined;
  /** 11/11 「请大家修改」: 拿这份文件发起一轮一起改 (只在群里的聊天消息上有) */
  onRevise?: (f: RoomFile) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, string>>({});
  async function retry(f: RoomFile) {
    setErr(null);
    try { await roomFileRetry(roomId, f.id); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
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
          {tagOf?.(f) && (
            <span title={tagOf(f)!.tip} style={{ flex: "0 0 auto", padding: "0 6px", borderRadius: 8, fontSize: 11, lineHeight: "18px",
                                                 border: "1px solid var(--catfish-border)", color: "var(--catfish-text-muted)" }}>
              {tagOf(f)!.tag}
            </span>
          )}
          {/* 名字太长省中间、留结尾 + 扩展名 (同一批文件常常只有结尾不同) */}
          <span style={{ minWidth: 0, display: "flex", maxWidth: 300 }} title={f.name}>
            <span style={{ flex: "0 0 auto" }}>📄&nbsp;</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{_splitFilename(f.name)[0]}</span>
            {_splitFilename(f.name)[1] && <span style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}>{_splitFilename(f.name)[1]}</span>}
          </span>
          <span style={{ opacity: 0.75 }}>{sizeLabel(f.size)}</span>
          {f.have && (
            <>
              <button type="button" style={btn} onClick={() => void open(f)}>打开</button>
              <button type="button" style={btn} onClick={() => void open(f, true)}>在文件夹中显示</button>
              {onRevise && <button type="button" style={btn} onClick={() => onRevise(f)} title="请群里的同事各自修改这份文件, 交回后系统合并">请大家修改</button>}
            </>
          )}
          {fileState(f) && (
            <span style={{ opacity: 0.75 }} title={f.error}>
              {fileState(f)}
              {f.big === "failed" && (
                <button type="button" style={{ ...btn, marginLeft: 6 }} onClick={() => void retry(f)}>重试</button>
              )}
            </span>
          )}
        </div>
      ))}
      {err && <span style={{ fontSize: 11, color: mine ? "inherit" : "var(--status-warn, #d97706)" }}>{err}</span>}
    </div>
  );
}

export interface PickedFile { name: string; size: number; file: File }

/** 挑文件 (📎 / 拖进来 / 粘贴): 先只记下来, 发送时再一段段传给本机 (stageFiles)。超上限当场说。 */
export async function pickFiles(list: FileList | File[], already: PickedFile[]): Promise<{ files: PickedFile[]; error?: string }> {
  const files = [...already];
  for (const f of Array.from(list)) {
    if (files.length >= MAX_FILES) return { files, error: `一条消息最多带 ${MAX_FILES} 个文件` };
    if (!f.size) return { files, error: `${f.name} 是空文件` };
    if (f.size > MAX_FILE) return { files, error: `${f.name} 太大 (单个最多 ${MAX_FILE / 1024 / 1024}MB) —— 更大的用网盘` };
    if (files.reduce((n, x) => n + x.size, 0) + f.size > MAX_TOTAL) {
      return { files, error: `一条消息合计最多 ${MAX_TOTAL / 1024 / 1024}MB (${f.name} 放不下) —— 分几条发` };
    }
    files.push({ name: f.name, size: f.size, file: f });
  }
  return { files };
}

function b64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** 发送前: 每个文件 1MB 一段传给本机暂存 (RPC 一行最多 16MB)。onProgress(已传字节, 总字节)。 */
export async function stageFiles(files: PickedFile[], onProgress: (done: number, total: number) => void,
                                 stage = roomStage): Promise<{ name: string; stage_id: string }[]> {
  const total = files.reduce((n, f) => n + f.size, 0);
  let done = 0;
  const out = [];
  for (const f of files) {
    let id: string | null = null;
    let offset = 0;
    while (offset < f.size) {
      const part = new Uint8Array(await f.file.slice(offset, offset + PART).arrayBuffer());
      const r = await stage(id, offset, b64(part));
      id = r.stage_id;
      done += r.size - offset;
      offset = r.size;          // ok=false 时 size 是本机已有的, 从那接着传
      onProgress(done, total);
    }
    out.push({ name: f.name, stage_id: id as string });
  }
  return out;
}

export function AttachButton({ disabled, onPick }: { disabled?: boolean; onPick: (files: FileList) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" multiple style={{ display: "none" }} aria-label="选文件发到群里"
             onChange={(e: ChangeEvent<HTMLInputElement>) => { if (e.target.files?.length) onPick(e.target.files); e.target.value = ""; }} />
      <button type="button" disabled={disabled} onClick={() => ref.current?.click()} title={`发文件 (最多 ${MAX_FILES} 个, 合计 ${MAX_TOTAL / 1024 / 1024}MB; 加密后经中央转交, 中央看不到内容, 大家取完就删)`}
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
