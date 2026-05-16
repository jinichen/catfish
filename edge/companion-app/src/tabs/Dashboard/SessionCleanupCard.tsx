/**
 * BL-SESSION-MGMT C (5/15) → BL-DASHBOARD-UI-CLEANUP-V2 (5/16 鸿波)
 *
 * 5/16 鸿波 audit: 预览按钮没显示 message 内容意义在哪 + confirm() 弹窗失效
 * 删除按钮无反应 + 流程"预览 → 软删" 复杂没必要.
 *
 * 改: 1 步删 + 30 秒撤销窗口. ID 格式化展示 (后端没返 message 内容, P2 再改).
 *
 * 软删 = 标 deleted_at, 30 天内可手动 restore (走 sessionRestore Tauri 命令).
 */
import { useState, useEffect, useRef } from "react";
import { sessionsBulkDeleteShort, type BulkDeleteResult } from "../../lib/tauri";
import { invoke } from "@tauri-apps/api/core";

/** 解析 hermes session id 成可读时间.
 *
 * 格式:
 *   "20260516_090035_d0c276"     → "5/16 09:00 (本地)"
 *   "cron_9b9c31be4749_20260516_090035" → "5/16 09:00 (cron)"
 *   其它 → 原样返
 */
function formatSessionId(id: string): string {
  const isCron = id.startsWith("cron_");
  const match = id.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})\d*/);
  if (!match) return id;
  const [, , mo, d, h, m] = match;
  return `${parseInt(mo, 10)}/${parseInt(d, 10)} ${h}:${m}${isCron ? " (cron)" : ""}`;
}

export function SessionCleanupCard() {
  const [loading, setLoading] = useState(false);
  const [candidates, setCandidates] = useState<BulkDeleteResult | null>(null);
  const [maxMessages, setMaxMessages] = useState(3);
  const [maxAgeHours, setMaxAgeHours] = useState(168);  // 7 天
  const [error, setError] = useState<string | null>(null);
  const [lastDeleted, setLastDeleted] = useState<{ count: number; ids: string[] } | null>(null);
  const [undoSecondsLeft, setUndoSecondsLeft] = useState(0);
  const undoTimerRef = useRef<number | null>(null);

  // BL-DASHBOARD-UI-CLEANUP-V2: 进卡片自动 fetch 候选, 不需要员工点"预览"
  const fetchCandidates = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await sessionsBulkDeleteShort({
        maxMessages,
        maxAgeHours,
        preview: true,
      });
      setCandidates(r);
    } catch (e) {
      setError(`查询失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  // mount + 参数变化时自动 refresh
  useEffect(() => {
    void fetchCandidates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxMessages, maxAgeHours]);

  // 撤销倒计时
  useEffect(() => {
    if (undoSecondsLeft <= 0) {
      if (undoTimerRef.current) {
        clearInterval(undoTimerRef.current);
        undoTimerRef.current = null;
      }
      return;
    }
    undoTimerRef.current = window.setTimeout(() => {
      setUndoSecondsLeft((s) => s - 1);
    }, 1000);
    return () => {
      if (undoTimerRef.current) {
        clearTimeout(undoTimerRef.current);
      }
    };
  }, [undoSecondsLeft]);

  const doDelete = async () => {
    if (!candidates || candidates.total === 0) return;
    // BL-DASHBOARD-UI-CLEANUP-V2: 去 confirm() (Tauri window 不弹, bug 来源).
    // 一键删 + 30 秒撤销窗口兜底.
    setLoading(true);
    setError(null);
    try {
      const r = await sessionsBulkDeleteShort({
        maxMessages,
        maxAgeHours,
        preview: false,
      });
      setLastDeleted({ count: r.total, ids: r.sessionIds });
      setUndoSecondsLeft(30);  // 30 秒撤销窗口
      setCandidates(null);
    } catch (e) {
      setError(`删除失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  const doUndo = async () => {
    if (!lastDeleted || lastDeleted.ids.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      // BL-DASHBOARD-UI-CLEANUP-V2: Rust session_restore 签名是 (id: String),
      // Tauri invoke 参数对应 `id`, 不是 sessionId.
      for (const id of lastDeleted.ids) {
        await invoke("session_restore", { id });
      }
      setLastDeleted(null);
      setUndoSecondsLeft(0);
      void fetchCandidates();  // 撤销后重新 fetch 候选
    } catch (e) {
      setError(`撤销失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        padding: 16,
        fontFamily: "var(--catfish-font-system, -apple-system, sans-serif)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
          清理短 session
        </h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          软删, 30 天内可恢复
        </span>
      </div>
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 12 }}>
        自动找消息数少 + 距今近的测试/废弃 session, 一键软删
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, fontSize: 13 }}>
        <label>
          消息数 ≤
          <input
            type="number"
            value={maxMessages}
            onChange={(e) => setMaxMessages(Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 3)))}
            min={1}
            max={20}
            style={{
              width: 50,
              marginLeft: 4,
              padding: "2px 6px",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
            }}
          />
        </label>
        <label>
          距今 ≤
          <input
            type="number"
            value={maxAgeHours}
            onChange={(e) => setMaxAgeHours(Math.max(1, Math.min(8760, parseInt(e.target.value, 10) || 168)))}
            min={1}
            max={8760}
            style={{
              width: 60,
              marginLeft: 4,
              padding: "2px 6px",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
            }}
          />
          小时
        </label>
      </div>

      {/* BL-DASHBOARD-UI-CLEANUP-V2: 主操作 — 1 个按钮 (没"预览" 步骤) */}
      {loading && !candidates && !lastDeleted && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>查询中…</div>
      )}

      {/* 删完: 显示成功 + 撤销 (30 秒倒计时) */}
      {lastDeleted && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "8px 12px",
            background: "var(--catfish-bg)",
            borderRadius: 6,
            fontSize: 12,
            marginBottom: 8,
          }}
        >
          <span style={{ color: "var(--catfish-success, #059669)", flex: 1 }}>
            ✓ 已软删 {lastDeleted.count} 个 session
          </span>
          {undoSecondsLeft > 0 ? (
            <button
              type="button"
              onClick={doUndo}
              disabled={loading}
              style={{
                padding: "4px 10px",
                background: "transparent",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 12,
                color: "var(--catfish-cyan)",
              }}
            >
              撤销 ({undoSecondsLeft}s)
            </button>
          ) : (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              30 天内仍可手动 restore
            </span>
          )}
        </div>
      )}

      {/* 候选列表 + 软删按钮 */}
      {candidates && !lastDeleted && (
        <>
          {candidates.total === 0 ? (
            <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
              ✓ 没找到符合条件的 session — sidebar 已经很干净
            </div>
          ) : (
            <>
              <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 8 }}>
                找到 <strong style={{ color: "var(--catfish-text)" }}>{candidates.total}</strong> 个候选
                {candidates.total > candidates.sessionIds.length &&
                  ` (列前 ${candidates.sessionIds.length} 个)`}
              </div>
              <ul
                style={{
                  margin: "0 0 12px 0",
                  paddingLeft: 16,
                  maxHeight: 200,
                  overflowY: "auto",
                  fontSize: 12,
                  lineHeight: 1.7,
                }}
              >
                {candidates.sessionIds.map((id) => (
                  <li key={id} style={{ color: "var(--catfish-text)" }}>
                    {formatSessionId(id)}
                    <span style={{
                      marginLeft: 8,
                      color: "var(--catfish-text-muted)",
                      fontSize: 10,
                      fontFamily: "var(--catfish-font-mono, ui-monospace, monospace)",
                    }}>
                      {id.slice(-6)}
                    </span>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={doDelete}
                disabled={loading}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "var(--catfish-error, #dc2626)",
                  border: "none",
                  borderRadius: 6,
                  cursor: loading ? "wait" : "pointer",
                  fontSize: 13,
                  color: "white",
                  fontWeight: 500,
                }}
              >
                {loading ? "处理中…" : `软删 ${candidates.total} 个 (30 秒内可撤销)`}
              </button>
            </>
          )}
        </>
      )}

      {error && (
        <div style={{ fontSize: 12, color: "var(--catfish-error, #dc2626)", marginTop: 8 }}>
          {error}
        </div>
      )}
    </div>
  );
}
