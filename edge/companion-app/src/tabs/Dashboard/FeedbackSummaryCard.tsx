/** Dashboard 卡 — "你给小鲶的反馈" (BL-MM6 五一 sprint 5/5 晚)
 *
 * 跟 ChatMessage 下面的 👍/👎/改 按钮配套. 这张卡:
 *   - 总数 + 三种 feedback 比例 (👍 N · 👎 N · 改 N)
 *   - 最近 5 条 negative (👎 / 改) 评论 — 让员工 review 自己提过啥意见
 *   - 文件大小 + 路径
 *   - "清空 feedback" 按钮 (隐私逃生口)
 *
 * 不调 gateway, 直接读 ~/.catfish/feedback.jsonl (Tauri command).
 */

import { useEffect, useState } from "react";
import { useAgentStore } from "../../store/agent";
import {
  feedbackSummary,
  feedbackClear,
  type FeedbackSummary,
  type FeedbackEvent,
} from "../../lib/tauri";

function humanTime(ts: number): string {
  if (!ts || ts <= 0) return "时间未知";
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

function kindLabel(kind: string): { emoji: string; text: string; color: string } {
  if (kind === "thumb_up") return { emoji: "👍", text: "好评", color: "var(--catfish-cyan)" };
  if (kind === "thumb_down") return { emoji: "👎", text: "不好", color: "var(--status-warn, orange)" };
  if (kind === "edit") return { emoji: "✏️", text: "想要不一样", color: "var(--catfish-text)" };
  return { emoji: "?", text: kind, color: "var(--catfish-text-muted)" };
}

export default function FeedbackSummaryCard() {
  const agentName = useAgentStore((s) => s.name);
  const [view, setView] = useState<FeedbackSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const v = await feedbackSummary();
      setView(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const clearAll = async () => {
    setBusy(true);
    try {
      await feedbackClear();
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
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
            <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
            你给{agentName}的反馈
          </h3>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            👍/👎/改 历史 (我会看, 越用越懂你)
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

      {view && view.total === 0 && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            fontStyle: "italic",
            padding: "var(--space-3) 0",
          }}
        >
          还没反馈过我 — 我说话时, 消息下方有 👍/👎/改 按钮,
          你点一下我会记住你的偏好, 之后回话照着改.
        </div>
      )}

      {view && view.total > 0 && (
        <>
          {/* 总数 + 三种比例 */}
          <div
            style={{
              display: "flex",
              gap: "var(--space-3)",
              marginBottom: "var(--space-3)",
              fontSize: 13,
            }}
          >
            <Stat emoji="👍" count={view.thumb_up} color="var(--catfish-cyan)" />
            <Stat emoji="👎" count={view.thumb_down} color="var(--status-warn, orange)" />
            <Stat emoji="✏️ 改" count={view.edit} color="var(--catfish-text)" />
          </div>

          {/* 最近 negative 评论 */}
          {view.recent_negative.length > 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                maxHeight: 240,
                overflowY: "auto",
                paddingRight: 4,
              }}
            >
              <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 2 }}>
                最近的"不好" / "想改"反馈:
              </div>
              {view.recent_negative.map((ev, i) => (
                <NegEntry key={i} ev={ev} />
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
            <span>共 {view.total} 条 · {(view.file_size_bytes / 1024).toFixed(1)} KB</span>
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
                清空反馈
              </button>
            ) : (
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button
                  type="button"
                  onClick={clearAll}
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

function Stat({ emoji, count, color }: { emoji: string; count: number; color: string }) {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "baseline",
        gap: 6,
      }}
    >
      <span style={{ fontSize: 16 }}>{emoji}</span>
      <span style={{ fontSize: 18, fontWeight: 600, color }}>{count}</span>
    </div>
  );
}

function NegEntry({ ev }: { ev: FeedbackEvent }) {
  const k = kindLabel(ev.kind);
  return (
    <div
      style={{
        fontSize: 12,
        background: "var(--catfish-bg-cream)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "6px 10px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ color: k.color, fontSize: 11 }}>
          {k.emoji} {k.text}
        </span>
        <span style={{ fontSize: 10, color: "var(--catfish-text-muted)" }}>
          {humanTime(ev.ts)}
        </span>
      </div>
      {ev.comment ? (
        <div style={{ color: "var(--catfish-text)", lineHeight: 1.5 }}>{ev.comment}</div>
      ) : (
        <div style={{ color: "var(--catfish-text-muted)", fontStyle: "italic", fontSize: 11 }}>
          (没写理由)
        </div>
      )}
      {ev.preview && (
        <div
          style={{
            marginTop: 4,
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            paddingLeft: 8,
            borderLeft: "2px solid var(--catfish-border)",
          }}
        >
          原文: {ev.preview.length > 80 ? ev.preview.slice(0, 80) + "…" : ev.preview}
        </div>
      )}
    </div>
  );
}
