/**
 * BL-SESSION-MGMT C (5/15) — Dashboard 清理短 session 卡片
 *
 * 仪表盘里加一个按钮: 自动找消息数 ≤3 且距今 ≤7 天的 session, 弹窗预览清单,
 * 员工 confirm 后一键软删. 304 个会话累积下来 80% 是 debugging 短 session,
 * 这个按钮一下子清干净.
 *
 * 软删 = 标 deleted_at, 30 天内可手动 restore (走 sessionRestore Tauri 命令).
 */
import { useState } from "react";
import { sessionsBulkDeleteShort, type BulkDeleteResult } from "../../lib/tauri";

export function SessionCleanupCard() {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<BulkDeleteResult | null>(null);
  const [maxMessages, setMaxMessages] = useState(3);
  const [maxAgeHours, setMaxAgeHours] = useState(168);  // 7 天
  const [error, setError] = useState<string | null>(null);
  const [lastDeleted, setLastDeleted] = useState<number | null>(null);

  const doPreview = async () => {
    setLoading(true);
    setError(null);
    setLastDeleted(null);
    try {
      const r = await sessionsBulkDeleteShort({
        maxMessages,
        maxAgeHours,
        preview: true,
      });
      setPreview(r);
    } catch (e) {
      setError(`预览失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  const doDelete = async () => {
    if (!preview || preview.total === 0) return;
    if (
      !confirm(
        `确定要软删 ${preview.total} 个短 session?\n` +
        `(30 天内可恢复, 不破坏数据)`
      )
    ) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await sessionsBulkDeleteShort({
        maxMessages,
        maxAgeHours,
        preview: false,
      });
      setLastDeleted(r.total);
      setPreview(null);  // 清掉 preview 不让用户重复点
    } catch (e) {
      setError(`删除失败: ${e}`);
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

      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <button
          type="button"
          onClick={doPreview}
          disabled={loading}
          style={{
            padding: "6px 12px",
            background: "var(--catfish-bg)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            cursor: loading ? "wait" : "pointer",
            fontSize: 13,
            color: "var(--catfish-text)",
          }}
        >
          {loading && !preview ? "查询中..." : "预览要删的 session"}
        </button>
        {preview && preview.total > 0 && (
          <button
            type="button"
            onClick={doDelete}
            disabled={loading}
            style={{
              padding: "6px 12px",
              background: "var(--catfish-error, #dc2626)",
              border: "1px solid var(--catfish-error, #dc2626)",
              borderRadius: 4,
              cursor: loading ? "wait" : "pointer",
              fontSize: 13,
              color: "white",
              fontWeight: 500,
            }}
          >
            软删 {preview.total} 个
          </button>
        )}
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--catfish-error, #dc2626)", marginTop: 8 }}>
          {error}
        </div>
      )}

      {lastDeleted !== null && (
        <div style={{ fontSize: 12, color: "var(--catfish-success, #059669)", marginTop: 8 }}>
          ✓ 已软删 {lastDeleted} 个 session (30 天内可手动 restore)
        </div>
      )}

      {preview && (
        <div style={{ marginTop: 12, fontSize: 12, color: "var(--catfish-text-muted)" }}>
          {preview.total === 0 ? (
            <span>没找到符合条件的 session — 你 sidebar 已经很干净 ✓</span>
          ) : (
            <>
              <div>
                找到 <strong>{preview.total}</strong> 个候选
                {preview.total > preview.sessionIds.length &&
                  ` (显示前 ${preview.sessionIds.length} 个)`}
              </div>
              <ul
                style={{
                  marginTop: 4,
                  paddingLeft: 16,
                  maxHeight: 140,
                  overflowY: "auto",
                  fontFamily: "var(--catfish-font-mono, ui-monospace, monospace)",
                  fontSize: 11,
                }}
              >
                {preview.sessionIds.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
