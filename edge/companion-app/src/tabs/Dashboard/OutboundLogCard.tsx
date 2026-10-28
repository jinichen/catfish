/** BL-EMPLOYEE-SELF-SERVE A4 Phase 2 (6/8 鸿波): 数据外发日志完整卡.
 *
 * Phase 1 是 PrivacyCard 的 inline panel (10 条 alert). Phase 2 升级独立卡:
 * - filter row (category / time range / url search)
 * - 表格 50 行 / 页 + 分页
 * - export CSV 按钮 (跟员工 sqlite3 命令行配套, 但更 UI-友好)
 * - 累计 stat 顶部 (上下行 KB / 总条数 / 9 天 GC 倒计时)
 * - 10/24: 只记带内容出本机的 + 发给第三方的; 顶部按「发给谁、为什么」汇总
 * - 10/25: 存请求内容 (点一行看) + 指纹链防篡改 + 保留期本机设 (OutboundLogDetail.tsx)
 *
 * 跟 manifesto 公理 2 (数据零出端) + 公理 1 (员工主权) 联动:
 * - 100% 员工本机 (~/.catfish/outbound_log.db)
 * - 中央不知道员工查没查 (没 endpoint)
 * - sqlite3 命令行**也能**看, UI 不是 lock-in
 */

import * as React from "react";

import { IntegrityPanel, PayloadPanel } from "./OutboundLogDetail";
import { displayPurpose, mergeSummaryRows } from "../../lib/egressRule";

import {
  transparentLogQuery,
  transparentLogExportCsv,
  transparentLogSummary,
  type TransparentLogEntry,
  type TransparentLogQueryResult,
  type TransparentLogSummaryRow,
} from "../../lib/tauri";

const PAGE_SIZE = 50;
// 6/8 BL-PRIVACY-SECTION-TABS (鸿波 6/8): OutboundLogCard 进 SectionTabs 后,
// "show / hide" 由 tab 选中接管, 这里**砍内部 collapsed**. tab 切到外发记录
// = 展开, 切走 = 不渲染. 双层 collapse 体验差.

// 10/24: 只剩两类 —— 带内容去中央 / 发给第三方 (本机、拉取、心跳不再记)
const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部" },
  { value: "content", label: "带你的内容发给中央" },
  { value: "third_party", label: "发给第三方" },
];

const ORIGIN_LABEL: Record<string, string> = {
  hermes: "小鲶 (hermes)",
  "tool-bridge": "小鲶的工具",
  companion: "Companion 界面",
  "companion-bg": "Companion 后台",
};

const TIME_RANGE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部时间" },
  { value: "1h", label: "近 1 小时" },
  { value: "24h", label: "近 24 小时" },
  { value: "7d", label: "近 7 天" },
];

const BYTES_HINT = "单位: 字节 (实际传输的数据量, 含请求格式和流式包装)。中央配额日志的 in / out 是 token, 不是一个单位";

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
        记下所有<b>带着你的内容离开本机</b>的请求 (小鲶调模型、资料蒸馏和核对、工具、Companion 自己发的),
        和所有<b>发给第三方</b>的请求 (网页搜索、微信、天气…)。发往本机的、只拉数据的、心跳不记。
        连请求内容一起存在本机 (密钥类字段打码), 点一行就能看到当时发了什么; 回复不存。
      </div>
      {/* 10/28 鸿波「上下和 in、out 的含义不一样?」—— 跟中央配额日志对着看时最容易混 */}
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        上行 / 下行的单位是<b>字节</b>: 网线上实际传了多少数据, 连请求格式 (历史对话、工具说明) 和流式回复的分段包装一起算。
        中央「配额日志」的 in / out 是 <b>token</b> (模型读写的字数), 不是一个单位 —— 上行大约 3～4 字节 ≈ 1 token,
        下行因为每一小段都有包装, 字节数会比 token 多得多。1.1.92 之前小鲶对话的下行没记上, 显示 0。
      </div>

      <OutboundSummary since={timeRangeToIsoSince(timeRange)} />
      <IntegrityPanel />

      {/* 累计 stat */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 12,
          marginBottom: "var(--space-3)",
        }}
      >
        <Stat label="共计记录" value={data ? String(data.total) : "—"} />
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
            url / status / 上下行 bytes / category / summary / error / 谁发的 / 去向 / 对方主机 / 用途.
            请求内容本来就不存, 只有大小.
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
              <th style={thStyle}>谁发的</th>
              <th style={thStyle}>做什么</th>
              <th style={thStyle}>发给</th>
              <th style={thStyle}>状态</th>
              <th style={{ ...thStyle, textAlign: "right" }} title={BYTES_HINT}>上行 ↑ (字节)</th>
              <th style={{ ...thStyle, textAlign: "right" }} title={BYTES_HINT}>下行 ↓ (字节)</th>
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
  const [open, setOpen] = React.useState(false);
  return (
    <>
    <tr
      style={{ borderTop: "1px solid var(--catfish-border)", cursor: "pointer",
               background: open ? "var(--catfish-bg)" : undefined }}
      onClick={() => setOpen((v) => !v)}
      title="点一下看这条发了什么"
    >
      <td style={tdStyle} title={e.tsRequest}>{open ? "▾ " : "▸ "}{localTime(e.tsRequest)}</td>
      <td style={tdStyle}>{ORIGIN_LABEL[e.origin ?? ""] ?? e.origin ?? "-"}</td>
      <td style={tdStyle} title={`${e.method} ${path}`}>{(e.purpose && displayPurpose(e.purpose, e.destHost ?? "", e.url)) || path}</td>
      <td
        style={{
          ...tdStyle,
          maxWidth: 260,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: e.destKind === "third_party" ? "var(--catfish-orange)" : undefined,
        }}
        title={e.url}
      >
        {e.destKind === "third_party" ? "第三方 · " : "中央 · "}{e.destHost || path}
      </td>
      <td style={{ ...tdStyle, color: statusColor }}>
        {e.status ?? (e.error ? "ERR" : "?")}
      </td>
      <td style={{ ...tdStyle, textAlign: "right" }}>{e.requestBytes.toLocaleString("en-US")}</td>
      <td style={{ ...tdStyle, textAlign: "right" }}>{e.responseBytes.toLocaleString("en-US")}</td>
    </tr>
    {open && (
      <tr>
        <td colSpan={7} style={{ padding: "0 6px 8px" }}>
          <PayloadPanel id={e.id} />
        </td>
      </tr>
    )}
    </>
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

/** 时间显示成本机时区 (记录里是 UTC ISO) */
function localTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 19);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** 10/24: 「发给谁、为什么」—— 按去向分两组, 每个用途一行: 次数 / 上行 / 最近一次 */
export function OutboundSummary({ since }: { since?: string }) {
  const [rows, setRows] = React.useState<TransparentLogSummaryRow[] | null>(null);
  React.useEffect(() => {
    let alive = true;
    transparentLogSummary(since)
      .then((r) => { if (alive) setRows(r); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [since]);
  if (!rows) return null;
  const groups: { kind: string; title: string; hint: string }[] = [
    { kind: "central", title: "带你的内容发给中央", hint: "对话、资料、记忆等发到公司中央服务 (模型网关等)" },
    { kind: "third_party", title: "发给第三方", hint: "公司以外的网站和服务" },
  ];
  const kb = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${(n / 1024).toFixed(1)} KB`);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, marginBottom: "var(--space-3)" }}>
      {groups.map((g) => {
        const mine = mergeSummaryRows(rows.filter((r) => r.destKind === g.kind));
        return (
          <section key={g.kind} style={{ border: "1px solid var(--catfish-border)", borderRadius: 6, padding: 10, background: "var(--catfish-bg)" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: g.kind === "third_party" ? "var(--catfish-orange)" : "var(--catfish-text)" }}>
              {g.title}
            </div>
            <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 6 }}>{g.hint}</div>
            {mine.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>没有记录</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <tbody>
                  {mine.map((r) => (
                    <tr key={`${r.purpose}|${r.destHost}`} style={{ borderTop: "1px solid var(--catfish-border)" }}>
                      <td style={{ padding: "4px 2px" }}>{r.purpose || "(未标注)"}</td>
                      <td style={{ padding: "4px 2px", color: "var(--catfish-text-muted)" }}>{r.destHost}</td>
                      <td style={{ padding: "4px 2px", textAlign: "right" }}>{r.count} 次</td>
                      <td style={{ padding: "4px 2px", textAlign: "right" }}>{kb(r.bytesUp)}</td>
                      <td style={{ padding: "4px 2px", textAlign: "right", color: "var(--catfish-text-muted)" }}>{localTime(r.lastTs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        );
      })}
    </div>
  );
}
