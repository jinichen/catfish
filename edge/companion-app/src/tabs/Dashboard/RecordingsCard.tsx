/** BL-RECMODE-DASHBOARD-UI (#75, 5/25): "我的录屏" 卡 — Companion Dashboard.
 *
 * 配套 #74 BL-RECMODE-NO-AUTO-DELETE: backend 撤了 cleanup daemon, 这里给员工
 * 列录屏 + 在 Finder 打开 + 手动删. 哲学: "中央不存 = 中央不管, 本机数据员工主权".
 *
 * 配套 #77 PrivacyCard: 隐私自查 + 录屏管理 = 完整的"员工主权"卖点.
 *
 * UI 设计 (跟 AuditCard / PrivacyCard 同 pattern):
 *   - 卡头标 "📹 我的录屏" + 一句副标 ("100% 本机 · catfish 不自动删")
 *   - 主体表格: session_id / 时间 / 大小 / 含 skill / 操作 (📁 在 Finder / 🗑️ 删)
 *   - 空状态友好提示 (RecMode opt-in, 没录过很正常)
 *   - 删按钮二次确认 + 删后 optimistic UI 更新 (不重 fetch, 直接 filter 掉)
 *
 * 故意不做:
 *   - 不显示截图缩略图 (含敏感, UI 一旦显示就有截屏泄漏风险, 想看进 Finder)
 *   - 不允许员工"批量删", 也不提供"自动定期删"开关 (违反 #74 哲学)
 *   - 不显示 path 全文 (太长污染表格, Finder 按钮直接定位就行)
 */

import * as React from "react";

import * as recApi from "../../lib/recordings";

export default function RecordingsCard() {
  const [recordings, setRecordings] = React.useState<recApi.RecordingMeta[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [deletingId, setDeletingId] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await recApi.listRecordings();
      setRecordings(r);
      setError(null);
    } catch (e) {
      setError(String(e));
      setRecordings([]);
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  const totalBytes = (recordings || []).reduce((s, r) => s + r.size_bytes, 0);

  const handleShowInFinder = async (path: string) => {
    try {
      await recApi.showInFinder(path);
    } catch (e) {
      // 不是致命错, 别 throw — 比如非 macOS / 路径越界, 给员工看错
      setError(String(e));
    }
  };

  const handleDelete = async (rec: recApi.RecordingMeta) => {
    const sizeMB = (rec.size_bytes / 1024 / 1024).toFixed(1);
    const kept = rec.kept_forever ? " (该 session 被标了 ⭐ 永久保留)" : "";
    const ok = window.confirm(
      `真删 ${rec.session_id}?${kept}\n` +
        `会从你硬盘删除 ${sizeMB} MB. 不可恢复.`,
    );
    if (!ok) return;
    setDeletingId(rec.session_id);
    try {
      await recApi.deleteRecording(rec.session_id);
      // optimistic: 直接 filter 掉, 不重 fetch (delete 已 atomic 落盘)
      setRecordings((rs) => (rs ? rs.filter((r) => r.session_id !== rec.session_id) : rs));
    } catch (e) {
      setError(`删除失败: ${e}`);
      // 失败重 fetch 一次同步真实状态
      void reload();
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ margin: 0 }}>📹 我的录屏</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          100% 本机 · catfish 不自动删 · 你想清自己点
        </span>
        {recordings !== null && recordings.length > 0 && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 12,
              color: "var(--catfish-text-muted)",
            }}
          >
            合计 {fmtBytes(totalBytes)} · {recordings.length} 个 session
          </span>
        )}
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--status-err, #c93a3a)", marginBottom: 8 }}>
          {error}
        </div>
      )}

      {recordings === null && <div style={{ fontSize: 13 }}>加载中…</div>}

      {recordings !== null && recordings.length === 0 && !error && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          没有录屏. RecMode 是 opt-in — 你在工作台点 🎬 录屏按钮开了才会录,
          录的内容保存在你电脑 <code>~/.catfish/recordings/</code>, 不上传中央.
        </div>
      )}

      {recordings !== null && recordings.length > 0 && (
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 12,
          }}
        >
          <thead>
            <tr style={{ textAlign: "left", color: "var(--catfish-text-muted)" }}>
              <th style={{ padding: "4px 8px 4px 0", fontWeight: 400 }}>Session</th>
              <th style={{ padding: "4px 8px", fontWeight: 400 }}>时间</th>
              <th style={{ padding: "4px 8px", fontWeight: 400, textAlign: "right" }}>大小</th>
              <th style={{ padding: "4px 8px", fontWeight: 400 }}>生成的 skill</th>
              <th style={{ padding: "4px 0", fontWeight: 400, width: 110, textAlign: "right" }}>
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {recordings.map((r) => {
              const isDeleting = deletingId === r.session_id;
              return (
                <tr
                  key={r.session_id}
                  style={{
                    borderTop: "1px solid var(--catfish-border)",
                    opacity: isDeleting ? 0.5 : 1,
                  }}
                >
                  <td style={{ padding: "6px 8px 6px 0", fontFamily: "monospace" }}>
                    {r.session_id}
                    {r.kept_forever && (
                      <span title="员工勾过永久保留" style={{ marginLeft: 4 }}>
                        ⭐
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--catfish-text-muted)" }}>
                    {fmtTime(r.started_at)}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {fmtBytes(r.size_bytes)}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--catfish-text-muted)" }}>
                    {r.skill_drafts.length === 0 ? (
                      <span style={{ opacity: 0.6 }}>—</span>
                    ) : (
                      <code style={{ fontSize: 11 }}>{r.skill_drafts.join(", ")}</code>
                    )}
                  </td>
                  <td style={{ padding: "6px 0", textAlign: "right" }}>
                    <button
                      type="button"
                      onClick={() => void handleShowInFinder(r.path)}
                      disabled={isDeleting}
                      title="在 Finder 打开"
                      style={btnStyle}
                    >
                      📁
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(r)}
                      disabled={isDeleting}
                      title="删除该录屏 (不可恢复)"
                      style={{ ...btnStyle, marginLeft: 4 }}
                    >
                      {isDeleting ? "…" : "🗑️"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 10 }}>
        想看具体内容? 点 📁 在 Finder 打开. 录屏 = 截图 + meta.json + 可能生成的
        skill_draft/. 跑 <code>catfish privacy-audit</code> 一并查中央 metadata.
      </p>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  padding: "2px 8px",
  cursor: "pointer",
  fontSize: 13,
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtTime(unixSec: number): string {
  if (!unixSec || unixSec <= 0) return "(无时间)";
  const d = new Date(unixSec * 1000);
  return d.toLocaleString();
}
