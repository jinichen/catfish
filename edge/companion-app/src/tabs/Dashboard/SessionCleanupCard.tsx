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
import { useState, useEffect } from "react";
import { sessionsBulkDeleteShort, type BulkDeleteResult } from "../../lib/tauri";
import { invoke } from "@tauri-apps/api/core";

// BL-DASHBOARD-UI-CLEANUP-V3 (5/16 鸿波 '可恢复怎么恢复'): 把上次软删持久化到
// localStorage, Companion 重启仍能看到"上次软删的 N 个 [恢复]" 按钮. 防"30 秒
// 倒计时过后无路径恢复" 的虚假承诺.
const LAST_DELETED_LS_KEY = "session_cleanup_last_deleted";

interface LastDeletedSnap {
  count: number;
  ids: string[];
  ts: number;  // 删的时间 epoch, 显示"5 分钟前删的"
}

function readLastDeleted(): LastDeletedSnap | null {
  try {
    const raw = localStorage.getItem(LAST_DELETED_LS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LastDeletedSnap;
    // 超 30 天的删除记录清掉 (后端也过期不能 restore 了)
    if (Date.now() - parsed.ts > 30 * 86400_000) {
      localStorage.removeItem(LAST_DELETED_LS_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeLastDeleted(snap: LastDeletedSnap | null): void {
  try {
    if (snap === null) {
      localStorage.removeItem(LAST_DELETED_LS_KEY);
    } else {
      localStorage.setItem(LAST_DELETED_LS_KEY, JSON.stringify(snap));
    }
  } catch {
    // localStorage 满/禁 silent fail, 不影响 UI
  }
}

function formatTimeAgo(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

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
  // BL-DASHBOARD-UI-CLEANUP-V3: lastDeleted 持久化 localStorage, 关 app 重启仍在
  const [lastDeleted, setLastDeleted] = useState<LastDeletedSnap | null>(
    () => readLastDeleted()
  );

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

  const doDelete = async () => {
    if (!candidates || candidates.total === 0) return;
    // BL-DASHBOARD-UI-CLEANUP-V2: 去 confirm() (Tauri window 不弹, bug 来源).
    setLoading(true);
    setError(null);
    try {
      const r = await sessionsBulkDeleteShort({
        maxMessages,
        maxAgeHours,
        preview: false,
      });
      // V3: 持久化 lastDeleted, 关 app 重启仍能恢复
      const snap: LastDeletedSnap = {
        count: r.total,
        ids: r.sessionIds,
        ts: Date.now(),
      };
      setLastDeleted(snap);
      writeLastDeleted(snap);
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
      writeLastDeleted(null);
      void fetchCandidates();  // 撤销后重新 fetch 候选
    } catch (e) {
      setError(`撤销失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  const doDismiss = () => {
    // BL-DASHBOARD-UI-CLEANUP-V3: 员工显式说"我知道了不恢复" → 清 banner
    setLastDeleted(null);
    writeLastDeleted(null);
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

      {/* BL-DASHBOARD-UI-CLEANUP-V3: 删完显示 banner, 撤销按钮永远在 (不倒计时)
          + dismiss 按钮让员工显式关. localStorage 持久化, 关 app 重启仍在. */}
      {lastDeleted && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 12px",
            background: "var(--catfish-bg)",
            border: "1px solid var(--catfish-success, #059669)",
            borderRadius: 6,
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          <span style={{ color: "var(--catfish-success, #059669)", flex: 1 }}>
            ✓ {formatTimeAgo(lastDeleted.ts)}软删了 {lastDeleted.count} 个 session
          </span>
          <button
            type="button"
            onClick={doUndo}
            disabled={loading}
            style={{
              padding: "4px 12px",
              background: "transparent",
              border: "1px solid var(--catfish-cyan)",
              borderRadius: 4,
              cursor: loading ? "wait" : "pointer",
              fontSize: 12,
              color: "var(--catfish-cyan)",
              fontWeight: 500,
            }}
          >
            {loading ? "恢复中…" : "全部恢复"}
          </button>
          <button
            type="button"
            onClick={doDismiss}
            title="我知道了, 不恢复"
            style={{
              padding: "4px 8px",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              fontSize: 14,
              color: "var(--catfish-text-muted)",
              lineHeight: 1,
            }}
          >
            ✕
          </button>
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
