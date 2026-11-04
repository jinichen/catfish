/** 11/4: 「我的记忆」里的「待你确认的整理建议」。
 *
 * 鸿波问「MEMORY.md 快满了为什么没有自动处理」—— 小鲶后台回顾会提合并 / 删旧条目的建议, 但 hermes
 * 不许后台自己删改记忆, 建议存着等人批; 原来界面上没地方看, 一直压着, 记忆只增不减。
 * 这里列出 USER.md 和 MEMORY.md 的建议, 员工点「同意」才改 (hermes 只改建议钉住的那条原文, 那条变了就拒绝)。
 */
import { useCallback, useEffect, useState } from "react";

import {
  hermesMemoryPendingList,
  hermesMemoryPendingResolve,
  type MemoryPendingItem,
  type MemoryPendingOp,
} from "../../lib/tauri";

const REFRESH_MS = 30_000;
const CLIP = 160;

const clip = (s: string) => (s.length > CLIP ? `${s.slice(0, CLIP)}…` : s);

function OpLine({ op }: { op: MemoryPendingOp }) {
  const muted = { color: "var(--catfish-text-muted)" };
  if (op.action === "add") return <div>➕ 新记一条: {clip(op.content)}</div>;
  if (op.action === "remove") return <div>🗑 删掉: <span style={muted}>{clip(op.entry)}</span></div>;
  if (op.action === "patch") {
    return (
      <div>
        ✏️ 改一处: 「<s style={muted}>{clip(op.old_text)}</s>」→「{clip(op.content)}」
        <div style={{ ...muted, fontSize: 11 }}>所在那条: {clip(op.entry)}</div>
      </div>
    );
  }
  return (
    <div>
      ✏️ 把 <span style={muted}>{clip(op.entry)}</span>
      <br />
      改成: {clip(op.content)}
    </div>
  );
}

export function describeTarget(t: MemoryPendingItem["target"]) {
  return t === "user" ? "USER · 员工本人" : "MEMORY · 项目 / 技术";
}

export default function MemoryPendingPanel({ onChanged, nearlyFull }: { onChanged: () => void; nearlyFull: boolean }) {
  const [items, setItems] = useState<MemoryPendingItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await hermesMemoryPendingList();
      setItems(r.items ?? []);
    } catch {
      setItems((cur) => cur ?? []); // tool-bridge 没起来时不挡记忆卡片
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const resolve = async (ids: string[], approve: boolean) => {
    setBusy(ids.length > 1 ? "all" : ids[0]);
    setError(null);
    const failed: string[] = [];
    for (const id of ids) {
      try {
        await hermesMemoryPendingResolve(id, approve);
      } catch (e) {
        failed.push(e instanceof Error ? e.message : String(e));
      }
    }
    if (failed.length) setError(ids.length > 1 ? `${failed.length} 条没改成: ${failed[0]}` : failed[0]);
    setBusy(null);
    await load();
    onChanged();
  };

  if (!items) return null;
  if (items.length === 0) {
    return nearlyFull ? (
      <div style={{ fontSize: 11, color: "var(--catfish-orange)", padding: "4px 0" }}>
        记忆快满了 —— 点右上角「🧹 整理记忆」找重复的, 或删掉用不上的条目。
      </div>
    ) : null;
  }

  const usable = items.filter((i) => !i.stale).map((i) => i.id);
  const staleIds = items.filter((i) => i.stale).map((i) => i.id);
  const btn = (primary: boolean) => ({
    background: primary ? "var(--catfish-cyan)" : "transparent",
    color: primary ? "white" : "var(--catfish-text)",
    border: primary ? "none" : "1px solid var(--catfish-border)",
    borderRadius: 3,
    padding: "2px 8px",
    fontSize: 11,
    cursor: busy ? "default" : "pointer",
  });

  return (
    <div
      aria-label="待你确认的整理建议"
      style={{
        padding: "8px 12px",
        background: "var(--catfish-bg)",
        border: "1px dashed var(--catfish-orange)",
        borderRadius: 6,
        fontSize: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
        <strong>待你确认的整理建议 ({items.length})</strong>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", flex: 1 }}>
          小鲶整理出来的, 你点同意才改
        </span>
        {usable.length > 1 && (
          <button type="button" disabled={!!busy} style={btn(true)} onClick={() => void resolve(usable, true)}>
            {busy === "all" ? "处理中…" : `全部同意 (${usable.length})`}
          </button>
        )}
        {staleIds.length > 1 && (
          <button type="button" disabled={!!busy} style={btn(false)} onClick={() => void resolve(staleIds, false)}>
            放弃过期的 ({staleIds.length})
          </button>
        )}
      </div>
      {error && <div style={{ color: "var(--status-err)", fontSize: 11, marginBottom: 4 }}>{error}</div>}
      {items.map((it) => (
        <div
          key={it.id}
          style={{ padding: "6px 0", borderTop: "1px dotted var(--catfish-border)", opacity: busy === it.id ? 0.4 : 1 }}
        >
          <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginBottom: 2 }}>
            {describeTarget(it.target)}
            {it.created_at ? ` · ${new Date(it.created_at * 1000).toLocaleDateString("zh-CN")}` : ""}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, lineHeight: 1.5, wordBreak: "break-word" }}>
            {it.ops.map((op, i) => <OpLine key={i} op={op} />)}
          </div>
          {it.stale && (
            <div style={{ fontSize: 11, color: "var(--catfish-orange)", marginTop: 2 }}>
              要改的那条记忆已经变了, 这条建议用不上了
            </div>
          )}
          <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
            {!it.stale && (
              <button type="button" disabled={!!busy} style={btn(true)} onClick={() => void resolve([it.id], true)}>
                同意
              </button>
            )}
            <button type="button" disabled={!!busy} style={btn(false)} onClick={() => void resolve([it.id], false)}>
              不要
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
