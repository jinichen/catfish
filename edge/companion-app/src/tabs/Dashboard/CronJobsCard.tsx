/** P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到") — Dashboard cron 监控卡.
 *
 * 数据来源:
 *   - jobs list: 直读 ~/.hermes/cron/jobs.json (cron_jobs_list)
 *   - 历史输出: 直读 ~/.hermes/cron/output/<id>/*.md (cron_job_outputs/output_read)
 *   - 操作 (pause/resume/delete): P26 RESTful endpoint (cron_job_pause/resume/delete)
 *
 * 30 秒自动刷新 (cron 状态变化慢, 不像 chat task 5s).
 *
 * 失败 row 显红 + tooltip 真错信息. 点击 row 展开看历史输出列表.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  cronJobsList,
  cronJobOutputs,
  cronJobOutputRead,
  cronJobPause,
  cronJobResume,
  cronJobDelete,
  type CronJob,
  type CronOutputMeta,
} from "../../lib/tauri";

const REFRESH_MS = 30_000;
const OUTPUT_PREVIEW_LIMIT = 5;

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const ts = new Date(iso).getTime();
    const now = Date.now();
    const diff = now - ts;
    if (diff < 0) {
      // 未来
      const futureMs = -diff;
      if (futureMs < 60_000) return "马上";
      if (futureMs < 3_600_000) return `${Math.round(futureMs / 60_000)} 分钟后`;
      if (futureMs < 86_400_000) return `${Math.round(futureMs / 3_600_000)} 小时后`;
      return `${Math.round(futureMs / 86_400_000)} 天后`;
    }
    if (diff < 60_000) return "刚刚";
    if (diff < 3_600_000) return `${Math.round(diff / 60_000)} 分钟前`;
    if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} 小时前`;
    if (diff < 7 * 86_400_000) return `${Math.round(diff / 86_400_000)} 天前`;
    return new Date(iso).toLocaleString("zh-CN", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

function statusIcon(job: CronJob): string {
  if (!job.enabled) return "⏸";
  // P27 (6/25): 真重试中 (attempt 1-3, exhausted=false) → 黄色 ↻
  if (
    job.last_status === "error" &&
    (job.catfish_retry_attempt ?? 0) > 0 &&
    !job.catfish_retry_exhausted
  ) {
    return "↻";
  }
  if (job.last_status === "error") return "❌";
  if (job.last_status === "ok") return "✓";
  return "○"; // 还没跑过
}

function statusColor(job: CronJob): string {
  if (!job.enabled) return "var(--catfish-text-muted)";
  // P27 重试中 → 黄
  if (
    job.last_status === "error" &&
    (job.catfish_retry_attempt ?? 0) > 0 &&
    !job.catfish_retry_exhausted
  ) {
    return "#d97706"; // amber-600
  }
  if (job.last_status === "error") return "#d9534f";
  if (job.last_status === "ok") return "#16a34a";
  return "var(--catfish-text-muted)";
}

export default function CronJobsCard() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const refreshing = useRef(false);

  const refresh = async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const data = await cronJobsList();
      setJobs(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      refreshing.current = false;
    }
  };

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  // P27 (6/25): failCount 真分 2 类 — retrying (5/10/15 重试中, 黄) vs hardFail (用尽/无重试, 红)
  const retryingCount = useMemo(
    () =>
      jobs.filter(
        (j) =>
          j.enabled &&
          j.last_status === "error" &&
          (j.catfish_retry_attempt ?? 0) > 0 &&
          !j.catfish_retry_exhausted,
      ).length,
    [jobs],
  );
  const failCount = useMemo(
    () =>
      jobs.filter(
        (j) =>
          j.enabled &&
          j.last_status === "error" &&
          // 真重试中真不算硬失败 (黄色 retry badge 独立显)
          !(
            (j.catfish_retry_attempt ?? 0) > 0 && !j.catfish_retry_exhausted
          ),
      ).length,
    [jobs],
  );
  const pausedCount = useMemo(
    () => jobs.filter((j) => !j.enabled).length,
    [jobs],
  );

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated, rgba(0,0,0,0.02))",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        // P3.5.105.1 (6/25 鸿波 catch "定时任务框太长"): 固定高度 + 列表内滚动.
        // 不让 cron job 数量爆炸把整 Dashboard 撑死.
        maxHeight: 380,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* 标题 + 统计 + 刷新 — sticky 顶部不跟着滚 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 12px 8px 12px",
          borderBottom: "1px solid var(--catfish-border-soft, rgba(0,0,0,0.05))",
          background: "var(--catfish-bg-elevated, rgba(0,0,0,0.02))",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <strong style={{ fontSize: 14 }}>⏰ 定时任务 ({jobs.length})</strong>
          {failCount > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "#fff",
                background: "#d9534f",
                padding: "1px 6px",
                borderRadius: 10,
                fontWeight: 600,
              }}
              title="有任务上次跑失败了 (重试已用尽 / 无重试)"
            >
              {failCount} 失败
            </span>
          )}
          {/* P27 (6/25): 真重试中 — 5/10/15 分钟自动重试链未走完 */}
          {retryingCount > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "#fff",
                background: "#d97706",
                padding: "1px 6px",
                borderRadius: 10,
                fontWeight: 600,
              }}
              title="有任务正在 5/10/15 分钟自动重试中"
            >
              {retryingCount} 重试中
            </span>
          )}
          {pausedCount > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
              }}
            >
              {pausedCount} 已暂停
            </span>
          )}
        </div>
        <button
          onClick={() => void refresh()}
          title="刷新"
          style={{
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            padding: "2px 8px",
            cursor: "pointer",
            fontSize: 11,
          }}
        >
          ↻
        </button>
      </div>

      {/* 滚动区: 错误 / loading / empty / jobs 列表全在这, 真超长时只这块滚 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "8px 12px 12px 12px",
        }}
      >
        {/* 错误 */}
        {error && (
          <div
            style={{
              fontSize: 11,
              color: "#d9534f",
              background: "rgba(217, 83, 79, 0.1)",
              padding: "4px 8px",
              borderRadius: 4,
              marginBottom: 6,
            }}
          >
            ✗ {error}
          </div>
        )}

        {/* loading / empty */}
        {loading && jobs.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            加载中…
          </div>
        )}
        {!loading && !error && jobs.length === 0 && (
          <div
            style={{
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              padding: "8px 0",
              textAlign: "center",
            }}
          >
            还没有定时任务. 跟鲶鱼说"每天 9 点提醒我…"即可注册.
          </div>
        )}

        {/* jobs 列表 */}
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {jobs.map((job) => (
            <CronJobRow
              key={job.id}
              job={job}
              expanded={expandedId === job.id}
              onToggle={() =>
                setExpandedId((cur) => (cur === job.id ? null : job.id))
              }
              onChanged={refresh}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

interface RowProps {
  job: CronJob;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}

function CronJobRow({ job, expanded, onToggle, onChanged }: RowProps) {
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  // P3.5.184 (7/6 鸿波军规审判): Tauri WebView 严格 window.confirm() 静默 null
  // 严格 → 严格永不 pass → 严格 delete API 严格永不 call. 严格历史多处 comment
  // verify: ChatSidebar.tsx:526 / DetailPane.tsx:102,555 / HermesMemoryCard.tsx:100 /
  // PrivacyCard.tsx:228 / SessionCleanupCard.tsx:114. 严格 fix: 两步点击 pattern.
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const handlePause = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    setOpError(null);
    try {
      await cronJobPause(job.id, "用户从 Dashboard 暂停");
      onChanged();
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleResume = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    setOpError(null);
    try {
      await cronJobResume(job.id);
      onChanged();
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    // P3.5.184 (7/6 鸿波军规审判): 严格 window.confirm() Tauri WebView 静默 null
    // 严格 (Apple 安全策略 + WebView 默认禁), 严格 老逻辑 `if (!confirm(...)) return`
    // 严格永不 pass → 严格 delete API 严格从未调过. 严格 fix 走 两步点击 (Notion/
    // Linear 模式, ChatSidebar.tsx:526 已验证 pattern): 首点 → confirmingDelete=true
    // 严格 按钮变 "确定?", 2 秒内再点 → 真删, 2 秒超时 severity 自动 reset.
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      setTimeout(() => setConfirmingDelete(false), 2000);
      return;
    }
    setConfirmingDelete(false);
    setBusy(true);
    setOpError(null);
    try {
      await cronJobDelete(job.id);
      onChanged();
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const schedule = job.schedule_display || job.schedule?.display || "—";
  const completed = job.repeat?.completed ?? 0;
  const isError = job.enabled && job.last_status === "error";
  // P27 (6/25): 真重试中 = 失败 + attempt > 0 + 未 exhausted
  const retryAttempt = job.catfish_retry_attempt ?? 0;
  const isRetrying = isError && retryAttempt > 0 && !job.catfish_retry_exhausted;
  const isRetryExhausted = isError && (job.catfish_retry_exhausted ?? false);

  return (
    <li
      style={{
        borderBottom: "1px solid var(--catfish-border-soft, rgba(0,0,0,0.05))",
      }}
    >
      <div
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          padding: "6px 4px",
          cursor: "pointer",
          // P27: 重试中黄色, 失败/已用尽红色
          background: isRetrying
            ? "rgba(217, 119, 6, 0.06)"
            : isError
            ? "rgba(217, 83, 79, 0.05)"
            : "transparent",
          gap: 8,
        }}
        title={
          isRetrying
            ? `重试中 ${retryAttempt}/3 · ${job.last_error || "上次跑失败"}`
            : isRetryExhausted
            ? `重试 3 次都失败 · ${job.last_error || ""}`
            : job.last_error || `${schedule} · 已跑 ${completed} 次`
        }
      >
        <span
          style={{
            fontSize: 14,
            color: statusColor(job),
            flexShrink: 0,
            width: 16,
            textAlign: "center",
          }}
        >
          {statusIcon(job)}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 500,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              color: !job.enabled ? "var(--catfish-text-muted)" : undefined,
            }}
          >
            {job.name}
          </div>
          <div
            style={{
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              marginTop: 2,
            }}
          >
            {schedule} · 上次 {fmtRelative(job.last_run_at)}
            {isError && (
              <span
                style={{
                  color: isRetrying ? "#d97706" : "#d9534f",
                  marginLeft: 6,
                }}
              >
                · {(job.last_error || "").slice(0, 40)}
                {(job.last_error || "").length > 40 ? "…" : ""}
              </span>
            )}
            {/* P27 真重试中 — 黄色显当前 attempt + 下次 5/10/15 分钟 */}
            {isRetrying && (
              <span
                style={{ color: "#d97706", marginLeft: 6, fontWeight: 500 }}
              >
                · 重试 {retryAttempt}/3, {fmtRelative(job.next_run_at)}
              </span>
            )}
            {/* P27 真用尽 3 次 — 灰色提示等下次 schedule */}
            {isRetryExhausted && (
              <span style={{ color: "var(--catfish-text-muted)", marginLeft: 6 }}>
                · 重试 3 次都失败, 等下次 {fmtRelative(job.next_run_at)}
              </span>
            )}
            {job.last_status === "ok" && (
              <span style={{ marginLeft: 6 }}>
                · 下次 {fmtRelative(job.next_run_at)}
              </span>
            )}
            {completed > 0 && (
              <span style={{ marginLeft: 6 }}>· {completed} 次</span>
            )}
          </div>
        </div>
        {/* 操作按钮 */}
        <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          {job.enabled ? (
            <button
              onClick={handlePause}
              disabled={busy}
              title="暂停"
              style={btnStyle}
            >
              ⏸
            </button>
          ) : (
            <button
              onClick={handleResume}
              disabled={busy}
              title="恢复"
              style={btnStyle}
            >
              ▶
            </button>
          )}
          <button
            onClick={handleDelete}
            disabled={busy}
            title={confirmingDelete ? "再点一次真删" : "删除 (点两次确认)"}
            style={{
              ...btnStyle,
              color: confirmingDelete ? "white" : "#d9534f",
              background: confirmingDelete ? "#d9534f" : btnStyle.background,
              fontSize: confirmingDelete ? 10 : btnStyle.fontSize,
              fontWeight: confirmingDelete ? 600 : undefined,
              minWidth: confirmingDelete ? 40 : btnStyle.minWidth,
            }}
          >
            {confirmingDelete ? "确定?" : "🗑"}
          </button>
        </div>
      </div>
      {opError && (
        <div
          style={{
            fontSize: 10,
            color: "#d9534f",
            padding: "0 8px 4px 28px",
          }}
        >
          ✗ {opError}
        </div>
      )}
      {expanded && <OutputsExpansion jobId={job.id} />}
    </li>
  );
}

const btnStyle: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  borderRadius: 3,
  padding: "1px 6px",
  fontSize: 11,
  cursor: "pointer",
  minWidth: 22,
};

function OutputsExpansion({ jobId }: { jobId: string }) {
  const [outputs, setOutputs] = useState<CronOutputMeta[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [openTs, setOpenTs] = useState<string | null>(null);
  const [openContent, setOpenContent] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    cronJobOutputs(jobId, OUTPUT_PREVIEW_LIMIT)
      .then((data) => {
        if (!cancelled) {
          setOutputs(data);
          setErr(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const openFull = async (ts: string) => {
    if (openTs === ts) {
      setOpenTs(null);
      setOpenContent(null);
      return;
    }
    setOpenTs(ts);
    setOpenContent(null);
    try {
      const content = await cronJobOutputRead(jobId, ts);
      setOpenContent(content);
    } catch (e) {
      setOpenContent(`✗ 读失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div
      style={{
        background: "rgba(0,0,0,0.02)",
        padding: "6px 12px 8px 28px",
        fontSize: 11,
      }}
    >
      {loading && <div style={{ color: "var(--catfish-text-muted)" }}>加载历史…</div>}
      {err && <div style={{ color: "#d9534f" }}>✗ {err}</div>}
      {outputs && outputs.length === 0 && (
        <div style={{ color: "var(--catfish-text-muted)" }}>
          没历史输出 (没跑过 / 已清)
        </div>
      )}
      {outputs && outputs.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {outputs.map((o) => (
            <li key={o.timestamp} style={{ marginBottom: 4 }}>
              <button
                onClick={() => void openFull(o.timestamp)}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  padding: "2px 0",
                  fontSize: 11,
                  color: "var(--catfish-accent, #0d9488)",
                  textDecoration: openTs === o.timestamp ? "underline" : "none",
                }}
              >
                {openTs === o.timestamp ? "▼" : "▶"} {o.timestamp}{" "}
                ({Math.round(o.size_bytes / 1024)} KB)
              </button>
              <div
                style={{
                  color: "var(--catfish-text-muted)",
                  paddingLeft: 14,
                  fontSize: 10,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {o.snippet}
              </div>
              {openTs === o.timestamp && openContent !== null && (
                <pre
                  style={{
                    background: "var(--catfish-bg, #fff)",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 4,
                    padding: 8,
                    fontSize: 10,
                    margin: "4px 0 4px 14px",
                    maxHeight: 300,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {openContent}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
