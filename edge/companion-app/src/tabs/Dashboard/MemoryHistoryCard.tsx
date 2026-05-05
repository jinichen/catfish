/** Dashboard 卡 — "鲶鱼记的硬事实" (BL-MM4 v1 五一 sprint 5/5 晚)
 *
 * 跟 RelationCard 配对的隐私透明卡:
 *   - RelationCard: 鲶鱼对你**整体**的印象 (LLM 总结的 journal 条目)
 *   - 本卡:        鲶鱼记的**硬事实** (catfish_remember 写的 session_facts.json)
 *                 + 每个 key 的 revision history (BL-MM2 v2 schema)
 *
 * 设计立场:
 *   - 列每个 key + 当前值 + 已修订 N 次
 *   - 点击 key 行展开, 看完整时间线 (像 git log)
 *   - 单 key "忘掉这条" 按钮 / 全部 "清空记忆" 按钮 — 隐私逃生口
 *   - 显示文件大小 + 路径 (员工想 Finder 看的话)
 *
 * 不调 gateway, 直接读 ~/.catfish/session_facts.json (Tauri command).
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useAgentStore } from "../../store/agent";

interface Revision {
  value: string;
  ts: number;          // unix seconds
  prev_value: string | null;
}

interface KeySummary {
  key: string;
  current_value: string;
  revision_count: number;
  last_updated_at: number;
  revisions: Revision[];
}

interface MemoryHistoryView {
  keys: KeySummary[];
  file_size_bytes: number;
  file_path: string;
}

/** unix sec → 人类时间. 例 "刚刚" / "3 分钟前" / "昨天" / "2026-05-04". */
function humanTime(ts: number): string {
  if (!ts || ts <= 0) return "时间未知";
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  // 更早: 给绝对日期
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString().padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

export default function MemoryHistoryCard() {
  const agentName = useAgentStore((s) => s.name);
  const [view, setView] = useState<MemoryHistoryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [confirmingAll, setConfirmingAll] = useState(false);
  const [busyAll, setBusyAll] = useState(false);
  /** 单 key 删除确认状态: key → true 表示二次确认中 */
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const v = await invoke<MemoryHistoryView>("memory_history_summary");
      setView(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const clearKey = async (key: string) => {
    setBusyKey(key);
    try {
      await invoke("memory_history_clear_key", { key });
      setConfirmingKey(null);
      // 折叠这条 (它已没了)
      if (expandedKey === key) setExpandedKey(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKey(null);
    }
  };

  const forgetAll = async () => {
    setBusyAll(true);
    try {
      await invoke("memory_history_forget_all");
      setConfirmingAll(false);
      setExpandedKey(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAll(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
            <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
            {agentName}记的具体信息
          </h3>
          {/* 5/5 鸿波: 跟"对你的印象"区分清楚, 加副标题 */}
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            你明确告诉我的事实 (像便签贴 · 改了我会留旧版本)
          </span>
        </div>
        <button
          type="button"
          onClick={refresh}
          title="刷新"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--catfish-text-muted)",
            fontSize: 11,
            cursor: "pointer",
            padding: 4,
          }}
        >
          ↻
        </button>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          读取失败: {error}
        </div>
      )}

      {!view && !error && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>读取中…</div>
      )}

      {view && (
        <>
          {view.keys.length === 0 ? (
            <div
              style={{
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                fontStyle: "italic",
                padding: "var(--space-3) 0",
              }}
            >
              还没记任何信息 — 你跟我说&nbsp;
              <em>"EIS 用 http 不是 https"</em> 这种明确的事实时,
              我会自动记一张便签 · 之后改了我也会保留旧版本, 你随时能看.
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                maxHeight: 360,
                overflowY: "auto",
                paddingRight: 4,
              }}
            >
              {view.keys.map((k) => (
                <KeyRow
                  key={k.key}
                  summary={k}
                  expanded={expandedKey === k.key}
                  onToggle={() =>
                    setExpandedKey((cur) => (cur === k.key ? null : k.key))
                  }
                  confirming={confirmingKey === k.key}
                  onConfirmStart={() => setConfirmingKey(k.key)}
                  onConfirmCancel={() => setConfirmingKey(null)}
                  onConfirmDo={() => void clearKey(k.key)}
                  busy={busyKey === k.key}
                />
              ))}
            </div>
          )}

          {/* footer: 大小 + 全清 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginTop: "var(--space-3)",
              paddingTop: "var(--space-2)",
              borderTop: "1px solid var(--catfish-border)",
              fontSize: 11,
              color: "var(--catfish-text-muted)",
            }}
          >
            <span title={view.file_path}>
              {view.keys.length} 条便签 · 共 {(view.file_size_bytes / 1024).toFixed(1)} KB
            </span>
            {view.keys.length > 0 && (!confirmingAll ? (
              <button
                type="button"
                onClick={() => setConfirmingAll(true)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  fontSize: 11,
                  padding: "3px 8px",
                  color: "var(--catfish-text-muted)",
                  cursor: "pointer",
                }}
              >
                清空记忆
              </button>
            ) : (
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button
                  type="button"
                  onClick={forgetAll}
                  disabled={busyAll}
                  style={{
                    background: "var(--status-err)",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "white",
                    cursor: busyAll ? "default" : "pointer",
                    opacity: busyAll ? 0.6 : 1,
                  }}
                >
                  {busyAll ? "清空中…" : "确认清空全部"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingAll(false)}
                  disabled={busyAll}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "var(--catfish-text-muted)",
                    cursor: busyAll ? "default" : "pointer",
                  }}
                >
                  算了
                </button>
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** 单条 key — 折叠时显 key + 当前值 + 已修订N次, 展开时显时间线 + diff. */
function KeyRow({
  summary,
  expanded,
  onToggle,
  confirming,
  onConfirmStart,
  onConfirmCancel,
  onConfirmDo,
  busy,
}: {
  summary: KeySummary;
  expanded: boolean;
  onToggle: () => void;
  confirming: boolean;
  onConfirmStart: () => void;
  onConfirmCancel: () => void;
  onConfirmDo: () => void;
  busy: boolean;
}) {
  const SNIPPET_LEN = 80;
  const valueDisplay =
    summary.current_value.length > SNIPPET_LEN
      ? summary.current_value.slice(0, SNIPPET_LEN) + "…"
      : summary.current_value;

  return (
    <div
      style={{
        fontSize: 12,
        background: "var(--catfish-bg-cream)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 10px",
      }}
    >
      {/* 折叠头: key + 当前值 + revision 数 + 删除按钮 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
        }}
        onClick={onToggle}
        title="点击展开/收起时间线"
      >
        <span style={{ fontWeight: 500, color: "var(--catfish-text)", flexShrink: 0 }}>
          {summary.key}
        </span>
        <span
          style={{
            color: "var(--catfish-text-muted)",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {valueDisplay}
        </span>
        {summary.revision_count > 1 && (
          <span
            style={{
              color: "var(--catfish-cyan)",
              fontSize: 10,
              flexShrink: 0,
              padding: "1px 6px",
              background: "var(--catfish-cyan-dim)",
              borderRadius: 8,
            }}
            title={`已修订 ${summary.revision_count} 次`}
          >
            {summary.revision_count} 版
          </span>
        )}
        <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", flexShrink: 0 }}>
          {expanded ? "↑" : "↓"}
        </span>
      </div>

      {/* 展开: 时间线 + 每版本 prev_value diff */}
      {expanded && (
        <div
          style={{
            marginTop: 8,
            paddingTop: 8,
            borderTop: "1px dashed var(--catfish-border)",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {summary.revisions.map((r, i) => (
            <div key={i} style={{ fontSize: 11, lineHeight: 1.5 }}>
              <div style={{ color: "var(--catfish-text-muted)", fontSize: 10 }}>
                {i === 0 ? "🟢 最新" : `第 ${summary.revisions.length - i} 版`}
                {" · "}
                {humanTime(r.ts)}
              </div>
              <div style={{ color: "var(--catfish-text)", whiteSpace: "pre-wrap", marginTop: 2 }}>
                {r.value}
              </div>
              {r.prev_value && (
                <div
                  style={{
                    color: "var(--catfish-text-muted)",
                    fontSize: 10,
                    marginTop: 4,
                    paddingLeft: 8,
                    borderLeft: "2px solid var(--catfish-border)",
                  }}
                >
                  之前: {r.prev_value.length > 100 ? r.prev_value.slice(0, 100) + "…" : r.prev_value}
                </div>
              )}
            </div>
          ))}

          {/* 单 key 删除 */}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 6,
              marginTop: 4,
            }}
          >
            {!confirming ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onConfirmStart();
                }}
                style={{
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  fontSize: 10,
                  padding: "2px 6px",
                  color: "var(--catfish-text-muted)",
                  cursor: "pointer",
                }}
              >
                忘掉这条
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onConfirmDo();
                  }}
                  disabled={busy}
                  style={{
                    background: "var(--status-err)",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 10,
                    padding: "2px 6px",
                    color: "white",
                    cursor: busy ? "default" : "pointer",
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  {busy ? "删除中…" : "确认删除"}
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onConfirmCancel();
                  }}
                  disabled={busy}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 4,
                    fontSize: 10,
                    padding: "2px 6px",
                    color: "var(--catfish-text-muted)",
                    cursor: busy ? "default" : "pointer",
                  }}
                >
                  算了
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
