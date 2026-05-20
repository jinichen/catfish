/** 早安播报 — 工作计划 (TODO) 详情区 — 抽自 BriefingCard.tsx (5/20 拆分).
 *
 * v2 (5/20 BL-COMPANION-BRIEFING-V2 sub-task 1): 单条 TODO 点击展开 — 元数据
 * (source/line/section) + ✅ 标完成 / 🗑 删 快捷按钮真调 journalMarkTodoDone /
 * journalDeleteTodo. 乐观更新 (标完成 strike-through, 删了立即不显).
 *
 * LLM 推断 (line=0, section="(LLM 推断)") 的 TODO 没真行号, 按钮 disabled.
 */

import { useState } from "react";

import {
  journalDeleteTodo,
  journalMarkTodoDone,
  type JournalTodo,
} from "../../../lib/tauri";

export default function TodosDetailSection({
  todos,
  onChanged,
}: {
  todos: JournalTodo[];
  onChanged?: () => void;
}) {
  const regexTodos = todos.filter((t) => t.section !== "(LLM 推断)");
  const llmTodos = todos.filter((t) => t.section === "(LLM 推断)");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [optimisticDone, setOptimisticDone] = useState<Set<string>>(new Set());
  const [optimisticDeleted, setOptimisticDeleted] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const keyOf = (t: JournalTodo) => `${t.line}-${t.text}`;
  const toggle = (k: string) => {
    const next = new Set(expanded);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setExpanded(next);
  };

  const handleMarkDone = async (t: JournalTodo) => {
    const k = keyOf(t);
    if (busy.has(k)) return;
    if (t.line <= 0 || t.section === "(LLM 推断)") {
      setActionError("LLM 推断的 TODO 无 journal 行号, 无法标完成");
      return;
    }
    setBusy((b) => new Set(b).add(k));
    try {
      await journalMarkTodoDone(t.line, t.text.slice(0, 30));
      setOptimisticDone((s) => new Set(s).add(k));
      setActionError(null);
      onChanged?.();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy((b) => {
        const n = new Set(b);
        n.delete(k);
        return n;
      });
    }
  };

  const handleDelete = async (t: JournalTodo) => {
    const k = keyOf(t);
    if (busy.has(k)) return;
    if (t.line <= 0 || t.section === "(LLM 推断)") {
      setActionError("LLM 推断的 TODO 无 journal 行号, 无法删除");
      return;
    }
    setBusy((b) => new Set(b).add(k));
    try {
      await journalDeleteTodo(t.line, t.text.slice(0, 30));
      setOptimisticDeleted((s) => new Set(s).add(k));
      setActionError(null);
      onChanged?.();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy((b) => {
        const n = new Set(b);
        n.delete(k);
        return n;
      });
    }
  };

  return (
    <details
      open
      style={{
        marginTop: "var(--space-2)",
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 500,
          color: "var(--catfish-text)",
          marginBottom: 6,
        }}
      >
        ✅ 工作计划详情 · {todos.length} 件
        {llmTodos.length > 0 && (
          <span
            style={{
              fontSize: 10,
              marginLeft: 6,
              color: "var(--catfish-text-muted)",
              fontWeight: 400,
            }}
          >
            ({regexTodos.length} 显式 + {llmTodos.length} LLM 推)
          </span>
        )}
        <span style={{ marginLeft: 6, fontSize: 10, color: "var(--catfish-text-muted)", opacity: 0.6, fontWeight: 400 }}>
          (点条目展开 / 标完成 / 删)
        </span>
      </summary>
      {actionError && (
        <div
          style={{
            fontSize: 10,
            color: "#dc2626",
            background: "#fef2f2",
            padding: "4px 8px",
            borderRadius: 3,
            marginTop: 6,
            border: "1px solid #fecaca",
          }}
        >
          ⚠ {actionError}
        </div>
      )}
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: "8px 0 0 0",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {todos.map((t, i) => {
          const k = keyOf(t);
          const isOpen = expanded.has(k);
          const isBusy = busy.has(k);
          const isDone = optimisticDone.has(k);
          const isDeleted = optimisticDeleted.has(k);
          if (isDeleted) {
            return null;  // 乐观更新, 等 onChanged 重拉
          }
          return (
            <li
              key={`${t.line}-${i}`}
              style={{
                borderLeft:
                  t.section === "(LLM 推断)"
                    ? "2px dashed var(--catfish-cyan)"
                    : "2px solid var(--catfish-cyan-dim)",
              }}
            >
              <div
                onClick={() => toggle(k)}
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "baseline",
                  padding: "4px 6px",
                  cursor: "pointer",
                }}
                title={isOpen ? "点击收起" : "点击展开"}
              >
                <span style={{ color: "var(--catfish-text-muted)", fontSize: 9, minWidth: 10 }}>
                  {isOpen ? "▼" : "▶"}
                </span>
                <span style={{ color: "var(--catfish-text-muted)", fontSize: 11, minWidth: 16 }}>
                  {isDone ? "✓" : t.source === "checkbox" ? "☐" : "·"}
                </span>
                <span
                  style={{
                    flex: 1,
                    color: isDone ? "var(--catfish-text-muted)" : "var(--catfish-text)",
                    textDecoration: isDone ? "line-through" : undefined,
                    opacity: isDone ? 0.6 : 1,
                  }}
                >
                  {t.text}
                </span>
                {t.section && (
                  <span
                    style={{
                      fontSize: 10,
                      color: "var(--catfish-text-muted)",
                      opacity: 0.7,
                      maxWidth: 140,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {t.section}
                  </span>
                )}
              </div>
              {isOpen && (
                <div
                  style={{
                    padding: "6px 10px 8px 24px",
                    background: "var(--catfish-bg)",
                    fontSize: 10,
                    color: "var(--catfish-text-muted)",
                    lineHeight: 1.5,
                  }}
                >
                  <div>
                    📄 source: <code>{t.source}</code>
                    {t.line > 0 && <> · line: <code>{t.line}</code></>}
                    {t.section && <> · 段: <code>{t.section}</code></>}
                  </div>
                  <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
                    <button
                      type="button"
                      disabled={isBusy || isDone || t.line <= 0 || t.section === "(LLM 推断)"}
                      onClick={(e) => { e.stopPropagation(); void handleMarkDone(t); }}
                      style={{
                        background: isDone ? "transparent" : "var(--catfish-cyan)",
                        color: isDone ? "var(--catfish-text-muted)" : "#fff",
                        border: "1px solid var(--catfish-border)",
                        borderRadius: 3,
                        padding: "2px 8px",
                        cursor: isBusy ? "wait" : "pointer",
                        fontSize: 10,
                        fontFamily: "inherit",
                        opacity: (t.line <= 0 || t.section === "(LLM 推断)") ? 0.5 : 1,
                      }}
                      title={
                        t.line <= 0 || t.section === "(LLM 推断)"
                          ? "LLM 推断 TODO 没真行号 无法标完成"
                          : "标完成 (写 - [x] 到 journal)"
                      }
                    >
                      {isBusy ? "…" : isDone ? "✓ 已完成" : "✅ 标完成"}
                    </button>
                    <button
                      type="button"
                      disabled={isBusy || t.line <= 0 || t.section === "(LLM 推断)"}
                      onClick={(e) => { e.stopPropagation(); void handleDelete(t); }}
                      style={{
                        background: "transparent",
                        color: "#dc2626",
                        border: "1px solid #fecaca",
                        borderRadius: 3,
                        padding: "2px 8px",
                        cursor: isBusy ? "wait" : "pointer",
                        fontSize: 10,
                        fontFamily: "inherit",
                        opacity: (t.line <= 0 || t.section === "(LLM 推断)") ? 0.5 : 1,
                      }}
                      title="从 journal 删整行 (不可撤销)"
                    >
                      {isBusy ? "…" : "🗑 删"}
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
