/** BL-EMPLOYEE-SELF-SERVE A4 Phase 2 (6/8 鸿波): 数据外发日志完整卡.
 *
 * Phase 1 是 PrivacyCard 的 inline panel (10 条 alert). Phase 2 升级独立卡:
 * - filter row (category / time range / url search)
 * - 表格 50 行 / 页 + 分页
 * - export CSV 按钮 (跟员工 sqlite3 命令行配套, 但更 UI-友好)
 * - 累计 stat 顶部 (上下行 KB / 总条数 / 9 天 GC 倒计时)
 *
 * 跟 manifesto 公理 2 (数据零出端) + 公理 1 (员工主权) 联动:
 * - 100% 员工本机 (~/.catfish/outbound_log.db)
 * - 中央不知道员工查没查 (没 endpoint)
 * - sqlite3 命令行**也能**看, UI 不是 lock-in
 */

import * as React from "react";

import {
  transparentLogQuery,
  transparentLogExportCsv,
  type TransparentLogEntry,
  type TransparentLogQueryResult,
} from "../../lib/tauri";

const PAGE_SIZE = 50;
// 6/8 BL-PRIVACY-SECTION-TABS (鸿波 6/8): OutboundLogCard 进 SectionTabs 后,
// "show / hide" 由 tab 选中接管, 这里**砍内部 collapsed**. tab 切到外发记录
// = 展开, 切走 = 不渲染. 双层 collapse 体验差.

const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部分类" },
  { value: "metering", label: "metering (额度/审计)" },
  { value: "advisory", label: "advisory (公司公告)" },
  { value: "identity", label: "identity (身份)" },
  { value: "chat", label: "chat (LLM 调用 ⚠ 含 prompt)" },
  { value: "health", label: "health (心跳/目录)" },
  { value: "other", label: "其它" },
];

const TIME_RANGE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部时间" },
  { value: "1h", label: "近 1 小时" },
  { value: "24h", label: "近 24 小时" },
  { value: "7d", label: "近 7 天" },
];

function timeRangeToIsoSince(range: string): string | undefined {
  if (!range) return undefined;
  const now = Date.now();
  let ms = 0;
  if (range === "1h") ms = 60 * 60 * 1000;
  else if (range === "24h") ms = 24 * 60 * 60 * 1000;
  else if (range === "7d") ms = 7 * 24 * 60 * 60 * 1000;
  if (!ms) return undefined;
  return new Date(now - ms).toISOString();
}

export default function OutboundLogCard() {
  const [category, setCategory] = React.useState("");
  const [timeRange, setTimeRange] = React.useState("");
  const [urlFilter, setUrlFilter] = React.useState("");
  const [urlInput, setUrlInput] = React.useState(""); // 解 debounce
  const [page, setPage] = React.useState(0);

  const [data, setData] = React.useState<TransparentLogQueryResult | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // url filter debounce 300ms
  React.useEffect(() => {
    const t = setTimeout(() => {
      setUrlFilter(urlInput);
      setPage(0); // 改 filter 回 page 0
    }, 300);
    return () => clearTimeout(t);
  }, [urlInput]);

  // 拉数据 — 6/8 BL-PRIVACY-SECTION-TABS 砍 collapsed 后, 永远拉 50 行.
  // 进 SectionTabs 后, tab 选中才渲染本 component, 不浪费.
  React.useEffect(() => {
    let cancelled = false;
    setBusy(true);
    (async () => {
      try {
        const since = timeRangeToIsoSince(timeRange);
        const result = await transparentLogQuery(
          since,
          category || undefined,
          urlFilter || undefined,
          PAGE_SIZE,
          page * PAGE_SIZE,
        );
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) setBusy(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [category, timeRange, urlFilter, page]);

  // toast 5s auto clear
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // 改 category / time / url 时, page 自动归 0 (由各 setX 内联负责)
  const onChangeCategory = (v: string) => {
    setCategory(v);
    setPage(0);
  };
  const onChangeTime = (v: string) => {
    setTimeRange(v);
    setPage(0);
  };

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const upKB = data ? (data.bytesUploadedTotal / 1024).toFixed(1) : "0";
  const downKB = data ? (data.bytesDownloadedTotal / 1024).toFixed(1) : "0";

  const [exportPath, setExportPath] = React.useState<string>(() => {
    const date = new Date().toISOString().slice(0, 10);
    return `/tmp/catfish-outbound-log-${date}.csv`;
  });
  const [showExport, setShowExport] = React.useState(false);

  const onExportRun = async () => {
    if (!exportPath.trim()) {
      setToast({ kind: "err", msg: "路径不能为空" });
      return;
    }
    if (exportPath.startsWith("~/")) {
      setToast({
        kind: "err",
        msg: "需要给绝对路径 (e.g. /Users/你/Desktop/x.csv)",
      });
      return;
    }
    setBusy(true);
    try {
      const since = timeRangeToIsoSince(timeRange);
      const count = await transparentLogExportCsv(
        exportPath.trim(),
        since,
        category || undefined,
        urlFilter || undefined,
      );
      setShowExport(false);
      setToast({ kind: "ok", msg: `✓ ${count} 条 → ${exportPath.trim()}` });
    } catch (e) {
      setToast({ kind: "err", msg: `导出失败: ${e}` });
    }
    setBusy(false);
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
      id="outbound-log-card"
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: "var(--space-3)",
          gap: 12,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 15 }}>📊 数据外发记录</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          ~/.catfish/outbound_log.db · sqlite3 也能查
        </span>
      </div>

      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        catfish 客户端跟中央服务的每个 HTTP 请求都自动记到本机 SQLite. 跟 manifesto
        公理 2 (数据零出端) 验证 — 员工**自己**审计中央实际收到什么, 不是"我们承诺".
      </div>

      {/* 累计 stat */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 12,
          marginBottom: "var(--space-3)",
        }}
      >
        <Stat label="共计请求" value={data ? String(data.total) : "—"} />
        <Stat label="累计上行" value={`${upKB} KB`} />
        <Stat label="累计下行" value={`${downKB} KB`} />
      </div>

      {/* filter row */}
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: "var(--space-3)",
          alignItems: "center",
        }}
      >
        <select
          value={category}
          onChange={(e) => onChangeCategory(e.target.value)}
          style={selectStyle}
        >
          {CATEGORY_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={timeRange}
          onChange={(e) => onChangeTime(e.target.value)}
          style={selectStyle}
        >
          {TIME_RANGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="URL 包含… (debounce 300ms)"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          style={{ ...selectStyle, flex: 1, minWidth: 160 }}
        />
        <button
          onClick={() => setShowExport((v) => !v)}
          style={btnSecondary}
          disabled={busy}
          title="按当前 filter 导出 CSV"
        >
          导出 CSV
        </button>
      </div>

      {/* 导出 inline panel */}
      {showExport && (
        <div
          style={{
            padding: 10,
            marginBottom: "var(--space-3)",
            border: "1px solid var(--catfish-cyan)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            fontSize: 12,
          }}
        >
          <div style={{ marginBottom: 6 }}>导出到 (.csv 绝对路径):</div>
          <input
            type="text"
            value={exportPath}
            onChange={(e) => setExportPath(e.target.value)}
            style={{
              width: "100%",
              padding: "4px 8px",
              fontSize: 12,
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "var(--catfish-bg-elevated)",
              color: "var(--catfish-text)",
              fontFamily: "monospace",
              marginBottom: 6,
            }}
          />
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 8 }}>
            导出当前 filter 下的全部 {data?.total ?? "?"} 条. 列: id / ts / method /
            url / status / 上下行 bytes / category / summary / error. payload 不入
            CSV (隐私 + 大, 想看用 sqlite3).
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => void onExportRun()} disabled={busy} style={btnPrimary}>
              {busy ? "导出中…" : "开始导出"}
            </button>
            <button onClick={() => setShowExport(false)} disabled={busy} style={btnSecondary}>
              取消
            </button>
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div
          style={{
            marginBottom: "var(--space-3)",
            padding: "6px 10px",
            fontSize: 12,
            background: "var(--catfish-bg)",
            border: `1px solid ${toast.kind === "ok" ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)"}`,
            color: toast.kind === "ok" ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)",
            borderRadius: 4,
          }}
        >
          {toast.msg}
        </div>
      )}

      {/* error */}
      {error && !busy && (
        <div
          style={{
            padding: 10,
            marginBottom: "var(--space-3)",
            border: "1px solid var(--status-err, #d9534f)",
            borderRadius: 4,
            color: "var(--status-err, #d9534f)",
            fontSize: 12,
          }}
        >
          查询失败: {error}
        </div>
      )}

      {/* table */}
      <div
        style={{
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          maxHeight: 480,
          overflow: "auto",
          fontFamily: "monospace",
          fontSize: 11,
          background: "var(--catfish-bg)",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead
            style={{
              position: "sticky",
              top: 0,
              background: "var(--catfish-bg-elevated)",
              zIndex: 1,
            }}
          >
            <tr>
              <th style={thStyle}>时间</th>
              <th style={thStyle}>方法</th>
              <th style={thStyle}>URL</th>
              <th style={thStyle}>类</th>
              <th style={thStyle}>状态</th>
              <th style={{ ...thStyle, textAlign: "right" }}>上↑</th>
              <th style={{ ...thStyle, textAlign: "right" }}>下↓</th>
            </tr>
          </thead>
          <tbody>
            {(data?.entries ?? []).map((e) => (
              <Row key={e.id} entry={e} />
            ))}
            {data && data.entries.length === 0 && !busy && (
              <tr>
                <td colSpan={7} style={{ ...tdStyle, color: "var(--catfish-text-muted)", textAlign: "center", padding: 16 }}>
                  当前 filter 下无记录.
                </td>
              </tr>
            )}
            {busy && !data && (
              <tr>
                <td colSpan={7} style={{ ...tdStyle, color: "var(--catfish-text-muted)", textAlign: "center", padding: 16 }}>
                  加载中…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 分页器 */}
      {data && totalPages > 1 && (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 8,
            marginTop: "var(--space-3)",
            fontSize: 12,
          }}
        >
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0 || busy}
            style={btnSecondary}
          >
            ← 上一页
          </button>
          <span style={{ color: "var(--catfish-text-muted)" }}>
            第 {page + 1} / {totalPages} 页 · 共 {data.total} 条
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1 || busy}
            style={btnSecondary}
          >
            下一页 →
          </button>
        </div>
      )}
    </div>
  );
}

function Row({ entry: e }: { entry: TransparentLogEntry }) {
  const path = e.url.replace(/^https?:\/\/[^/]+/, "") || e.url;
  const statusColor =
    e.status === undefined
      ? "var(--status-err, #d9534f)"
      : e.status >= 200 && e.status < 300
        ? "var(--catfish-cyan)"
        : e.status >= 400
          ? "var(--status-err, #d9534f)"
          : "var(--catfish-text)";
  return (
    <tr style={{ borderTop: "1px solid var(--catfish-border)" }}>
      <td style={tdStyle}>{e.tsRequest.slice(11, 19)}</td>
      <td style={tdStyle}>{e.method}</td>
      <td
        style={{
          ...tdStyle,
          maxWidth: 360,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={e.url}
      >
        {path}
      </td>
      <td style={tdStyle}>{e.category ?? "-"}</td>
      <td style={{ ...tdStyle, color: statusColor }}>
        {e.status ?? (e.error ? "ERR" : "?")}
      </td>
      <td style={{ ...tdStyle, textAlign: "right" }}>{e.requestBytes}</td>
      <td style={{ ...tdStyle, textAlign: "right" }}>{e.responseBytes}</td>
    </tr>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "8px 10px",
        background: "var(--catfish-bg)",
        border: "1px solid var(--catfish-border)",
        borderRadius: 4,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{value}</div>
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  padding: "4px 8px",
  fontSize: 12,
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  background: "var(--catfish-bg-elevated)",
  color: "var(--catfish-text)",
};

const btnPrimary: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  border: "1px solid var(--catfish-cyan)",
  borderRadius: 4,
  background: "var(--catfish-cyan)",
  color: "white",
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  background: "transparent",
  color: "var(--catfish-text)",
  cursor: "pointer",
};

const thStyle: React.CSSProperties = {
  padding: "6px 8px",
  textAlign: "left",
  fontWeight: 600,
  fontSize: 11,
  color: "var(--catfish-text-muted)",
};

const tdStyle: React.CSSProperties = {
  padding: "4px 8px",
  fontSize: 11,
  color: "var(--catfish-text)",
};
