/** BL-EMPLOYEE-PRIVACY-VERIFICATION (#77, 5/25) — 员工隐私自查卡.
 *
 * 跟 #76 CLI / #79 gateway / #78 doc 配套. 卖点:
 * "你不需要纯信任公司, 你能验证."
 *
 * 卡内分 3 块:
 *   1. 本机数据存哪 (员工本机 = 不上传的): 静态文本 + 路径展示, 让员工知道
 *      对话 / memory / token 全在自己电脑
 *   2. 中央存了我啥 (调 /api/audit/me): 实时数字, 全是 metadata
 *   3. 一键查看: 跑 `catfish privacy-audit` / 打开员工 doc 链接
 *
 * 故意不做的:
 *   - 不把 prompt 内容 / token 字符串 / API key 显示到 UI (UI 一旦显示就有截屏泄漏风险)
 *   - 不提供"清空中央数据"按钮 (admin 才有权, 不该出在员工 dashboard)
 *   - 不直接 invoke CLI (跨进程, 安全模型不允许. 给路径让员工自己跑)
 */

import * as React from "react";

import { fetchMyAudit, type MyAuditSummary } from "../../lib/me";
import { formatTokens } from "../../lib/format";

// 30s 轮询 — 跟 AuditCard 同节奏 (避免一个卡 30s 一个卡 5s 让 UI 不同步)
const POLL_MS = 30_000;

// 本机数据清单 — 跟 catfish-cli 的 _PRIVACY_SCAN_TARGETS 同源真理, 改一处两处都要改.
// 5/26 文案改 (鸿波 review): 给普通员工 (会计 / HR / 销售) 看, 不是给程序员. 用"我跟你说过的内容"
// 这种"我"/"你"的口语化措辞, 路径塞到展开里, 默认隐藏.
const LOCAL_DATA_ITEMS: { what: string; path: string; uploaded: boolean }[] = [
  { what: "你跟我聊过的所有内容", path: "~/.hermes/sessions/", uploaded: false },
  { what: "我对你的长期了解 (你是谁、喜欢啥、工作什么的)", path: "~/.hermes/memories/", uploaded: false },
  { what: "你的第三方服务密钥 (Tavily 搜索等, 我在你本机上用)", path: "~/.hermes/.env", uploaded: false },
  { what: "你的登录凭证 (跟密码同等级, 我们也不上传)", path: "~/.catfish/auth/token.json", uploaded: false },
  { what: "我跑过的调用记录 (本机历史归档, 公司服务器单独存自己一份)", path: "~/.catfish/gateway_audit.jsonl", uploaded: false },
  // 6/2 BL-DASHBOARD-DROP-RECORDINGS-CARD: RecordingsCard 整卡删后 (录屏 99% 时间空,
  // 卡是 UI noise), 录屏说明 1 行挪进来. 路径仍可在 Finder/CLI 进, 但默认 skill
  // 生成完自动清原料 (#17), 员工大多数情况下进去看就是空的.
  { what: "你录过的屏 (skill 生成完自动清原料, 没生成的留着等你保存)", path: "~/.catfish/recordings/", uploaded: false },
  // 6/2 BL-MEMORY-AUDIT-TRAIL: 替 hermes 原生 memory_tool 的"replace/remove 直接覆盖
  // 无 history" 兜底. 每次 add/replace/remove 落 jsonl, prev_value 全保留. 周一拍
  // 专卡 + Tauri 命令前, 员工/IT 想查直接 cat / jq 这文件.
  { what: "我对你的记忆修改历史 (覆盖/删除全留, 可查可追溯)", path: "~/.catfish/memory_audit.jsonl", uploaded: false },
];

export default function PrivacyCard() {
  const [data, setData] = React.useState<MyAuditSummary | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const d = await fetchMyAudit();
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

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
      {/* 6/8 layout-v2 (鸿波 6/8): 改 macOS Settings 风格 2 列 row.
          左列 ~180px 固定 (label + 1 行说明), 右列流式 (实际数据). 减纵向长度
          + 视觉分组更清晰. 6/6 删的副标 (那是顶部副标) 不变.
          6/1 BL-PRIVACY-CARD-SIMPLIFY 简化精神保留 — 仍 2 大块本机/服务器
          + 自助, 没折叠 / 没技术路径 / 没"看不到 X"暗示. */}
      <h3 style={{ margin: "0 0 var(--space-3) 0" }}>🔒 隐私状态</h3>

      {/* row 1: 本机存储 */}
      <Row
        title="🟢 本机存储"
        subtitle="只在你电脑上, 不上传"
      >
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            fontSize: 13,
          }}
        >
          {LOCAL_DATA_ITEMS.map((it, i) => (
            <li
              key={it.path}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                padding: "6px 0",
                borderTop: i === 0 ? "none" : "1px solid var(--catfish-border)",
              }}
            >
              <span style={{ color: "var(--catfish-cyan)", fontSize: 14, flexShrink: 0 }}>✓</span>
              <span style={{ flex: 1, color: "var(--catfish-text)" }}>{it.what}</span>
            </li>
          ))}
        </ul>
      </Row>

      {/* row 2: 服务器存储 */}
      <Row
        title="🟡 服务器存储"
        subtitle="只 meta data, 不存对话"
      >
        {error && (() => {
          const is401 = /401|unauthorized/i.test(error);
          if (is401) {
            return (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text)",
                  background: "var(--catfish-bg)",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-2)",
                }}
              >
                登录过期了, 公司服务器那条拉不到 — 这页 30s 自动重试.
                <br />
                <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
                  你跟我聊的内容仍只在你本机. 重登就行: 顶上 "工作台" → 头像 → 退出 → 重新登录.
                </span>
              </div>
            );
          }
          return (
            <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
              暂时拉不到 ({error}).
              <br />
              <span style={{ fontSize: 11 }}>
                (离线 / 服务器挂 — 你本机数据不受影响, 30s 自动重试.)
              </span>
            </div>
          );
        })()}

        {!data && !error && <div style={{ fontSize: 12 }}>加载中…</div>}

        {data && (
          <>
            <div
              style={{
                padding: "8px 10px",
                background: "var(--catfish-bg, transparent)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                marginBottom: "var(--space-2)",
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              邮箱 · 调用时间 · 用了哪个模型 · 用了多少额度
            </div>

            {data.request_count > 0 ? (
              <QuotaProgressBar used={data.total_tokens} limit={data.quota_day_limit} />
            ) : (
              <p
                style={{
                  fontSize: 13,
                  color: "var(--catfish-text-muted)",
                  margin: "0 0 var(--space-2)",
                  padding: "8px 10px",
                  background: "var(--catfish-bg, transparent)",
                  border: "1px dashed var(--catfish-border)",
                  borderRadius: 4,
                }}
              >
                今天还没找过我 — 服务器今天没记你任何调用.
              </p>
            )}

            {(data.first_seen_ts || data.last_seen_ts) && (
              <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", margin: "var(--space-2) 0 0" }}>
                历史记录:{" "}
                {data.first_seen_ts && (
                  <>
                    从 <strong>{new Date(data.first_seen_ts).toLocaleDateString()}</strong> 起
                  </>
                )}
                {data.last_seen_ts && data.request_count === 0 && (
                  <>
                    , 最近一次{" "}
                    <strong>{new Date(data.last_seen_ts).toLocaleString()}</strong>
                  </>
                )}
              </p>
            )}
          </>
        )}
      </Row>

      {/* row 3: 员工自助工具 (manifesto 公理 1 落地) */}
      <Row
        title="🔧 员工自助工具"
        subtitle="你自己管, IT 不能动"
        last
      >
        <SelfServeButtons />
      </Row>
    </div>
  );
}

/** 6/8 BL-EMPLOYEE-SELF-SERVE UI v2 (Phase 1 fix + Phase 2 减负):
 *
 *  Phase 1 真根因 fix: macOS WKWebView 默认禁用 window.prompt()/alert()/
 *  confirm() (Apple 安全策略), 静默 null. 改 React state inline panel.
 *
 *  Phase 2 减负: "我的数据外发记录" 按钮 + inline log table 迁到独立
 *  OutboundLogCard (filter / paginate / export CSV 全功能). PrivacyCard
 *  只留重置 / 导出 (manifesto 公理 1 直接自助), log 走独立卡.
 */
type Mode =
  | { kind: "idle" }
  | {
      kind: "reset-preview";
      summary: import("../../lib/tauri").ResetSummary;
      confirmStr: string;
    }
  | { kind: "export"; path: string };

function SelfServeButtons() {
  const [mode, setMode] = React.useState<Mode>({ kind: "idle" });
  const [busy, setBusy] = React.useState(false);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // toast 5s 自动消
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // 默认 export path (员工 home Desktop, 后端会自动 ~ → $HOME 展开 ... 实际不会, 这里用占位)
  const defaultExportPath = React.useMemo(() => {
    const date = new Date().toISOString().slice(0, 10);
    return `/tmp/catfish-export-${date}.tar.gz`;
  }, []);

  const onResetClick = async () => {
    setToast(null);
    setBusy(true);
    try {
      const lib = await import("../../lib/tauri");
      const summary = await lib.selfServePreviewReset();
      setMode({ kind: "reset-preview", summary, confirmStr: "" });
    } catch (e) {
      setToast({ kind: "err", msg: `预览失败: ${e}` });
    }
    setBusy(false);
  };

  const onResetConfirm = async () => {
    if (mode.kind !== "reset-preview") return;
    if (mode.confirmStr !== "我确认") {
      setToast({ kind: "err", msg: '必须输入"我确认"4 字' });
      return;
    }
    setBusy(true);
    try {
      const lib = await import("../../lib/tauri");
      const result = await lib.selfServeExecuteReset("我确认");
      setMode({ kind: "idle" });
      setToast({
        kind: "ok",
        msg: `✓ 重置完成. trash: ${result.trashPath} (5 秒内可 sqlite3/Finder 手工恢复)`,
      });
    } catch (e) {
      setToast({ kind: "err", msg: `重置失败: ${e}` });
    }
    setBusy(false);
  };

  const onExportClick = () => {
    setToast(null);
    setMode({ kind: "export", path: defaultExportPath });
  };

  const onExportRun = async () => {
    if (mode.kind !== "export") return;
    let path = mode.path.trim();
    if (!path) {
      setToast({ kind: "err", msg: "路径不能为空" });
      return;
    }
    if (path.startsWith("~/")) {
      setToast({
        kind: "err",
        msg: "需要给绝对路径 (e.g. /Users/你的名字/Desktop/catfish-export.tar.gz)",
      });
      return;
    }
    setBusy(true);
    try {
      const lib = await import("../../lib/tauri");
      const result = await lib.selfServeExportData(
        {
          includeConversations: true,
          includeRecordings: true,
          includeWiki: true,
          includeSkills: true,
          includeStrategicDocs: true,
          includeConfig: true,
        },
        path,
      );
      const mb = (result.bytesWritten / (1024 * 1024)).toFixed(1);
      setMode({ kind: "idle" });
      setToast({ kind: "ok", msg: `✓ 导出完成. ${mb} MB → ${result.outputPath}` });
    } catch (e) {
      setToast({ kind: "err", msg: `导出失败: ${e}` });
    }
    setBusy(false);
  };

  // log 走独立 OutboundLogCard, button 改成 scrollTo
  const onLogClick = () => {
    setToast(null);
    const el = document.getElementById("outbound-log-card");
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      setToast({ kind: "err", msg: "找不到 OutboundLogCard (排版问题, 滚一下找找)" });
    }
  };

  return (
    <div>
      {/* 6/8 layout-v2: 标题 + 副标 移到外层 Row, 这里只剩 3 button + inline panel. */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          onClick={() => void onResetClick()}
          disabled={busy}
          style={btnStyle("danger")}
          title="移 ~/.catfish/ 到 trash"
        >
          {busy && mode.kind === "idle" ? "处理中…" : "重置我的所有数据"}
        </button>
        <button
          onClick={onExportClick}
          disabled={busy}
          style={btnStyle("primary")}
          title="打包 ~/.catfish/ 到 .tar.gz"
        >
          导出我的所有数据
        </button>
        <button
          onClick={onLogClick}
          disabled={busy}
          style={btnStyle("normal")}
          title="跳转到下面的'数据外发记录'卡 (filter / paginate / 导出 CSV)"
        >
          ↓ 我的数据外发记录
        </button>
      </div>

      {/* toast */}
      {toast && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 10px",
            fontSize: 12,
            background: toast.kind === "ok" ? "var(--catfish-bg)" : "var(--catfish-bg)",
            border: `1px solid ${toast.kind === "ok" ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)"}`,
            color: toast.kind === "ok" ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)",
            borderRadius: 4,
          }}
        >
          {toast.msg}
        </div>
      )}

      {/* inline panel — 重置预览/二次确认 */}
      {mode.kind === "reset-preview" && (
        <div
          style={{
            marginTop: 10,
            padding: 12,
            border: "1px solid var(--status-err, #d9534f)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            fontSize: 13,
          }}
        >
          <div style={{ marginBottom: 8, fontWeight: 600 }}>⚠ 真要重置吗?</div>
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
            <li>{mode.summary.conversationsDeleted} 个对话历史</li>
            <li>{mode.summary.recordingsDeleted} 个录屏</li>
            <li>{mode.summary.wikiFilesDeleted} 个 wiki 笔记</li>
            <li>{mode.summary.skillsDeleted} 个 skill</li>
            <li>共 {(mode.summary.bytesFreedTotal / (1024 * 1024)).toFixed(1)} MB</li>
          </ul>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--catfish-text-muted)" }}>
            数据先移到 ~/.catfish-reset-trash/&lt;ts&gt;/, 你可 Finder/CLI 手工 mv 回去.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="text"
              placeholder='输入"我确认"'
              value={mode.confirmStr}
              onChange={(e) =>
                setMode({ ...mode, confirmStr: e.target.value })
              }
              style={{
                padding: "4px 8px",
                fontSize: 12,
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
              }}
            />
            <button
              onClick={() => void onResetConfirm()}
              disabled={busy || mode.confirmStr !== "我确认"}
              style={btnStyle("danger")}
            >
              {busy ? "重置中…" : "确认重置"}
            </button>
            <button
              onClick={() => setMode({ kind: "idle" })}
              disabled={busy}
              style={btnStyle("normal")}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* inline panel — 导出路径输入 */}
      {mode.kind === "export" && (
        <div
          style={{
            marginTop: 10,
            padding: 12,
            border: "1px solid var(--catfish-cyan)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            fontSize: 13,
          }}
        >
          <div style={{ marginBottom: 6 }}>导出到 (.tar.gz 绝对路径):</div>
          <input
            type="text"
            value={mode.path}
            onChange={(e) => setMode({ ...mode, path: e.target.value })}
            style={{
              width: "100%",
              padding: "4px 8px",
              fontSize: 12,
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "var(--catfish-bg-elevated)",
              color: "var(--catfish-text)",
              fontFamily: "monospace",
            }}
          />
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--catfish-text-muted)" }}>
            含: conversations / recordings / wiki / skills / strategic_docs / config.
            <br />
            tar 后台跑, 大目录可能 10-30s.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              onClick={() => void onExportRun()}
              disabled={busy}
              style={btnStyle("primary")}
            >
              {busy ? "导出中…" : "开始导出"}
            </button>
            <button
              onClick={() => setMode({ kind: "idle" })}
              disabled={busy}
              style={btnStyle("normal")}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 注: outbound log table 已迁到独立 OutboundLogCard (filter/paginate/CSV) */}
    </div>
  );
}

/** 6/8 layout-v2 (鸿波 6/8): macOS Settings App 风格 row.
 *
 * 左列固定 ~180px (label + subtitle), 右列流式 (实际内容). 用于隐私 / 服务器
 * 存储 / 自助工具 等 row. 让长卡的纵向高度压缩 + 视觉分组更清晰.
 *
 * 比单纯堆 h4 + content 短约 30%, 因为左右并排不重复占行.
 *
 * - title: section 标题 (e.g. "🟢 本机存储")
 * - subtitle: 副标 (1 行内, e.g. "只在你电脑上, 不上传"), 帮员工 0.5s 理解 row 主旨
 * - last: 最后一 row 不画底分割线
 * - children: 右列内容 (list / progress / button group / ...)
 */
function Row({
  title,
  subtitle,
  last,
  children,
}: {
  title: string;
  subtitle?: string;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "180px 1fr",
        gap: "var(--space-4)",
        padding: "var(--space-3) 0",
        borderTop: "1px solid var(--catfish-border)",
        borderBottom: last ? "none" : undefined,
      }}
    >
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--catfish-text)" }}>
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4 }}>
            {subtitle}
          </div>
        )}
      </div>
      <div style={{ minWidth: 0 /* 防 grid blowout */ }}>{children}</div>
    </div>
  );
}

function btnStyle(kind: "danger" | "primary" | "normal"): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "6px 12px",
    fontSize: 12,
    borderRadius: 4,
    cursor: "pointer",
    border: "1px solid",
  };
  switch (kind) {
    case "danger":
      return {
        ...base,
        background: "transparent",
        color: "var(--status-err, #d9534f)",
        borderColor: "var(--status-err, #d9534f)",
      };
    case "primary":
      return {
        ...base,
        background: "var(--catfish-cyan)",
        color: "white",
        borderColor: "var(--catfish-cyan)",
      };
    case "normal":
      return {
        ...base,
        background: "transparent",
        color: "var(--catfish-text)",
        borderColor: "var(--catfish-border)",
      };
  }
}

/** 6/2 BL-PRIVACY-CARD-QUOTA-PROGRESS: quota 进度条 — 真兑现 quota.py 三维限流的
 * "员工能自查". 之前光显 "今天用了 X" 是死数字, 现在加上限对比 + 百分比.
 *
 * limit=0 → 该 user 不限 (quotas.yaml 默认 1M, 客户可改 0 表示不限). 这时不渲
 * 染百分比条, 显纯"今天用了 X (不限)".
 *
 * Stat 组件 (原通用 label+value 框) 已删 — 调用方全砍了 (B/D 砍, C 升级成本组件),
 * 留着是 dead code.
 */
function QuotaProgressBar({ used, limit }: { used: number; limit: number }) {
  const usedStr = formatTokens(used);
  // limit=0 = 不限
  if (limit <= 0) {
    return (
      <div
        style={{
          padding: "10px 12px",
          background: "var(--catfish-bg, transparent)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          marginBottom: "var(--space-2)",
          fontSize: 13,
          color: "var(--catfish-text)",
        }}
      >
        今天用了 <strong>{usedStr}</strong>{" "}
        <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>· 不限</span>
      </div>
    );
  }
  const pct = Math.min(100, Math.max(0, (used / limit) * 100));
  const remain = Math.max(0, limit - used);
  // 颜色分级: <70% 青绿 / 70-90% 暖黄 / >90% 警告红 — 让员工接近上限时立即注意
  let barColor = "var(--catfish-cyan, #38b2ac)";
  if (pct >= 90) barColor = "var(--status-err, #c93a3a)";
  else if (pct >= 70) barColor = "var(--status-warn, #c98b00)";
  const limitStr = formatTokens(limit);
  const remainStr = formatTokens(remain);
  return (
    <div
      style={{
        padding: "10px 12px",
        background: "var(--catfish-bg, transparent)",
        border: "1px solid var(--catfish-border)",
        borderRadius: 4,
        marginBottom: "var(--space-2)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          fontSize: 13,
          color: "var(--catfish-text)",
          marginBottom: 6,
        }}
      >
        <span>
          今天用了 <strong>{usedStr}</strong> / {limitStr}
        </span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {pct.toFixed(0)}% · 还剩 {remainStr}
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{
          width: "100%",
          height: 8,
          background: "var(--catfish-border)",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: barColor,
            transition: "width 0.3s ease",
          }}
        />
      </div>
    </div>
  );
}
