/**
 * 群聊里的小鲶 (11/15)。小鲶不是群成员 —— 群消息端到端加密、只在各人电脑上, 小鲶只替自己的主人干活 (GROUP-CHAT-PLAN §5):
 *   · 输入框里打「@小鲶 …」: 这句话不发到群里, 交给自己的小鲶 (room/assist kind=ask, 读本机群记录 + 群聊功能说明),
 *     先查本机资料 (知识库 / 文件 / 邮件, tool-bridge local_lookup), 回答下面列依据; 回答只显示在自己这边 (不进群记录, 换群 / 关掉就没了); 觉得有用点「发到群里」或「放进输入框」改了再发。
 *   · 「🐟 小鲶」菜单: 总结 / 整理待办 / 起草回复 (原来就有, 从 RoomsPanel 挪过来)。
 *   · 成员栏、@ 菜单里给小鲶一个固定位置, 标明「你的助手 · 只有你看得到」, 不算成员。
 */
import { useEffect, useRef, useState } from "react";

import { roomAssist, type AskSource, type AssistKind, type RoomMessage } from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";
import { Btn, ErrorLine } from "./roomLinkUi";

export const XIAONIAN_AT = "@小鲶";
const XIAONIAN_HINT = "你的助手 · 只有你看得到";

/** 输入框以「@小鲶」开头 = 问小鲶: 返回问题 (可能是空串); 不是问小鲶返回 null */
export function askQuestion(text: string): string | null {
  const t = text.trimStart();
  if (!t.startsWith(XIAONIAN_AT)) return null;
  return t.slice(XIAONIAN_AT.length).replace(/^[\s,，:：]+/, "").trim();
}

/** 发到群里的样子: 同事没看到问题, 标明是小鲶答的 */
export function postText(a: Ask, max: number): string {
  return `🐟 小鲶: ${a.answer ?? ""}`.slice(0, max);
}

export type Ask = { id: number; q: string; about?: string; answer?: string; error?: string; sources?: AskSource[] };

/** 「依据: 知识库「…」· 文件「…」」(前 5 条) */
export function sourcesLine(src: AskSource[] | undefined): string {
  if (!src?.length) return "";
  return "依据 (你电脑上的): " + src.slice(0, 5).map((s) => `${s.kind === "本机文件" ? "文件" : s.kind}「${s.title}」`).join(" · ");
}

export function useAskXiaonian(roomId: string) {
  const [asks, setAsks] = useState<Ask[]>([]);
  const seq = useRef(0);
  useEffect(() => { setAsks([]); }, [roomId]);   // 只在这个群里、这次打开时看得到
  const patch = (id: number, p: Partial<Ask>) => setAsks((l) => l.map((a) => (a.id === id ? { ...a, ...p } : a)));
  function ask(q: string, replyTo: RoomMessage | null, about?: string) {
    const id = ++seq.current;
    setAsks((l) => [...l, { id, q, about }]);
    roomAssist(roomId, "ask", q, replyTo?.msg_id ?? null)
      .then((r) => patch(id, { answer: r.text, sources: r.sources }))
      .catch((e) => patch(id, { error: e instanceof Error ? e.message : String(e) }));
  }
  const dismiss = (id: number) => setAsks((l) => l.filter((a) => a.id !== id));
  return { asks, ask, dismiss };
}

/** 问答卡: 放在聊天记录最下面, 虚线框 + 「只有你看得到」, 跟群消息区分开 */
export function AskCards({ asks, onPost, onEdit, onDismiss, busy }: {
  asks: Ask[];
  onPost: (a: Ask) => void;
  onEdit: (a: Ask) => void;
  onDismiss: (id: number) => void;
  busy?: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  const last = asks.at(-1);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [asks.length, last?.answer, last?.error]);
  if (!asks.length) return null;
  return (
    <>
      {asks.map((a) => (
        <div key={a.id} data-testid="xiaonian-ask"
             style={{ alignSelf: "stretch", border: "1px dashed var(--catfish-cyan)", borderRadius: "var(--radius-sm)", padding: "8px 10px",
                      display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ ...muted, minWidth: 0 }}>
              你问 🐟 小鲶{a.about ? ` (关于 ${a.about} 那条)` : ""}: <span style={{ color: "var(--catfish-text)" }}>{a.q}</span>
            </span>
            <span style={{ ...muted, whiteSpace: "nowrap", flexShrink: 0 }}>🔒 只有你看得到</span>
          </div>
          {a.error ? <ErrorLine>{a.error}</ErrorLine>
            : a.answer === undefined ? <span style={muted}>🐟 小鲶在查你电脑上的资料和群记录…</span>
            : <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{a.answer}</div>}
          {a.answer !== undefined && sourcesLine(a.sources) && <span style={muted}>{sourcesLine(a.sources)}</span>}
          <span style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            {a.answer !== undefined && <Btn kind="ghost" disabled={busy} onClick={() => onEdit(a)}>放进输入框</Btn>}
            {a.answer !== undefined && <Btn kind="ghost" disabled={busy} onClick={() => onPost(a)}>发到群里</Btn>}
            <Btn kind="ghost" onClick={() => onDismiss(a.id)}>{a.answer === undefined && !a.error ? "不等了" : "关掉"}</Btn>
          </span>
        </div>
      ))}
      <div ref={end} />
    </>
  );
}

const menuItem = { textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: "6px 8px",
                   fontSize: 13, color: "inherit", borderRadius: 4 } as const;
const popup = { position: "absolute", bottom: "100%", background: "var(--catfish-bg-elevated)", zIndex: 5,
                border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 4,
                display: "flex", flexDirection: "column", color: "var(--catfish-text, inherit)" } as const;

/** 「🐟 小鲶」菜单 */
export function XiaonianMenu({ replyLabel, onPick, onAsk }: {
  replyLabel?: string;
  onPick: (k: Exclude<AssistKind, "ask">) => void;
  onAsk: () => void;
}) {
  const items = [["summary", "总结一下最近的讨论"], ["todos", "整理成待办 (谁 / 做什么 / 截止)"],
                 ["reply", replyLabel ? `起草回复 ${replyLabel} 的那条` : "帮我起草回复 (输入框里可写要求)"]] as const;
  return (
    <div style={{ ...popup, right: 0, minWidth: 240 }}>
      <button type="button" onClick={onAsk} className="room-menu-item" style={menuItem}>问小鲶一个问题 (只有你看得到)</button>
      {items.map(([k, label]) => (
        <button key={k} type="button" onClick={() => onPick(k)} className="room-menu-item" style={menuItem}>{label}</button>
      ))}
      <span style={{ ...muted, padding: "4px 8px" }}>小鲶读你电脑上的群记录; 起草的放进输入框, 发不发你定。</span>
    </div>
  );
}

/** @ 菜单最上面一项 */
export function XiaonianAtItem({ onPick }: { onPick: () => void }) {
  return (
    <button type="button" onClick={onPick} className="room-menu-item" style={{ ...menuItem, padding: "4px 8px" }}>
      🐟 @小鲶 <span style={muted}>{XIAONIAN_HINT}</span>
    </button>
  );
}

/** 成员栏最上面: 小鲶不算成员, 但放在这里让人知道它在 */
export function XiaonianMemberRow({ onAsk }: { onAsk?: () => void }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 4 }}
         title="小鲶不是群成员: 它在你电脑上, 只替你干活, 别人看不到你问了什么。在输入框打「@小鲶 问题」问它">
      <span style={{ fontSize: 12, whiteSpace: "nowrap" }}>🐟 小鲶</span>
      {onAsk ? (
        <button type="button" onClick={onAsk}
                style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 11, color: "var(--catfish-cyan)", whiteSpace: "nowrap" }}>
          {XIAONIAN_HINT}
        </button>
      ) : <span style={{ ...muted, whiteSpace: "nowrap" }}>{XIAONIAN_HINT}</span>}
    </div>
  );
}
