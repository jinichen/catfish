/** Dashboard 卡 — "鲶鱼对你的印象" (BL-E16 五一 sprint 5/3 晚)
 *
 * 设计立场: 鲶鱼"记得"你的事, 员工**必须**能看到 + 删除, 否则就 creepy.
 *   - 显示最近 5 条 employee_journal 总结 (LLM 已经在每次 chat 看到这些)
 *   - 显示"今天第 N 次 / 距上次 N 天 N 小时"
 *   - "清空印象" 按钮 (rm journal + meta) — 隐私逃生口
 *
 * 不调 gateway, 直接读本机文件 (Tauri command).
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useAgentStore } from "../../store/agent";

interface JournalEntry {
  title: string;
  body: string;
}

interface RelationView {
  recent_entries: JournalEntry[];
  last_chat_human: string | null;
  today_count: number | null;
  journal_size_bytes: number;
}

export default function RelationCard() {
  // BL-E11 后续: 标题 + 提示语用员工自定义名
  const agentName = useAgentStore((s) => s.name);
  const [view, setView] = useState<RelationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const v = await invoke<RelationView>("relation_summary");
      setView(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const forget = async () => {
    setBusy(true);
    try {
      await invoke("relation_forget");
      setConfirming(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
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
        <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
          <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
          {agentName}对你的印象
        </h3>
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

      {!view && !error && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>读取中…</div>}

      {view && (
        <>
          {/* 时间感 */}
          {(view.last_chat_human || view.today_count) && (
            <div
              style={{
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                marginBottom: "var(--space-3)",
                lineHeight: 1.6,
              }}
            >
              {view.last_chat_human && (
                <div>距上次找我: <strong style={{ color: "var(--catfish-text)" }}>{view.last_chat_human}前</strong></div>
              )}
              {view.today_count !== null && view.today_count > 0 && (
                <div>今天第 <strong style={{ color: "var(--catfish-text)" }}>{view.today_count}</strong> 次找我</div>
              )}
            </div>
          )}

          {/* 最近条目 */}
          {view.recent_entries.length === 0 ? (
            <div
              style={{
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                fontStyle: "italic",
                padding: "var(--space-3) 0",
              }}
            >
              还没有印象 — 多跟我聊几次, 我会记住你的工作.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {view.recent_entries.map((e, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 12,
                    background: "var(--catfish-bg-cream)",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: "var(--radius-sm)",
                    padding: "8px 10px",
                  }}
                >
                  <div style={{ fontWeight: 500, color: "var(--catfish-text)", marginBottom: 4 }}>
                    {e.title}
                  </div>
                  <div
                    style={{
                      color: "var(--catfish-text-muted)",
                      lineHeight: 1.5,
                      maxHeight: 60,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      display: "-webkit-box",
                      WebkitBoxOrient: "vertical",
                      WebkitLineClamp: 3,
                    }}
                  >
                    {e.body || "(空)"}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* footer: 大小 + 清空 */}
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
            <span>共 {(view.journal_size_bytes / 1024).toFixed(1)} KB</span>
            {!confirming ? (
              <button
                type="button"
                onClick={() => setConfirming(true)}
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
                清空印象
              </button>
            ) : (
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button
                  type="button"
                  onClick={forget}
                  disabled={busy}
                  style={{
                    background: "var(--status-err)",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "white",
                    cursor: busy ? "default" : "pointer",
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  {busy ? "清空中…" : "确认"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "var(--catfish-text-muted)",
                    cursor: busy ? "default" : "pointer",
                  }}
                >
                  算了
                </button>
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
