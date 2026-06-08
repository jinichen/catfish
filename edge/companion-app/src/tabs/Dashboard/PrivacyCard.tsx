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
      {/* 6/6 鸿波: 副标 "这页让你自己看 — 不用问 IT · 30s 自动刷新" 删, 卡内信息密度
       *  已经足够自解释 (绿/黄 灯 + 复选 list), 副标重复反而稀释主旨. */}
      <h3 style={{ margin: "0 0 var(--space-3) 0" }}>🔒 隐私状态</h3>

      {/* ── 本机存储 — 6/1 BL-PRIVACY-CARD-SIMPLIFY 鸿波拍 ─────────────
          原来 3 区 + 5 折叠 + 技术路径 + "看不到 X" 暗示 + IT 命令, 国企
          员工/领导消化不了. 简化到 2 大块, 没折叠, 没暗示, 没技术名词. */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          🟢 本机存储
        </h4>
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            fontSize: 13,
          }}
        >
          {LOCAL_DATA_ITEMS.map((it) => (
            <li
              key={it.path}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                padding: "8px 0",
                borderTop: "1px solid var(--catfish-border)",
              }}
            >
              <span style={{ color: "var(--catfish-cyan)", fontSize: 14, flexShrink: 0 }}>✓</span>
              <span style={{ flex: 1, color: "var(--catfish-text)" }}>{it.what}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ── 服务器存储 — 简化版 ──────────────────────── */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          🟡 服务器存储
        </h4>

        {error && (() => {
          // BL-LONG-RUNNING-V1-FOLLOWUP (5/31): 401 是 token 过期, 不是 "Error"
          // 别用冷红色吓员工 — 改友好提示 + 引导重登. 其它错误 (5xx / 网络)
          // 保持原灰色, 跟"你本机数据不受影响"同一调.
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
            {/* 服务器存储内容 — 只正面陈述. "看不到 X" 那行删 (反向暗示让员工联想). */}
            <div
              style={{
                padding: "10px 12px",
                background: "var(--catfish-bg, transparent)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                marginBottom: "var(--space-3)",
                fontSize: 13,
                lineHeight: 1.7,
              }}
            >
              邮箱 · 调用时间 · 用了哪个模型 · 用了多少额度
            </div>

            {/* 6/2 BL-PRIVACY-CARD-QUOTA-PROGRESS (鸿波 6/2 凌晨): 原 3 Stat (今天找我
                221 次 / 今天用了 7.99M / 最近一次 07:16:54) 砍 2 留 1 + 改进度条.
                - "今天找我 X 次": 砍. 次数对员工 99% 没用 (quota 按 token 不按次数).
                - "最近一次": 砍. 员工现在就在用 Companion, 显然知道. 冗余信息.
                - "今天用了 X": 留并升级 — 加 quota_day_limit 进度条, 真兑现 quota.py
                  三维限流的"员工能自查". 之前光显示数字没参照系 = 死数字.
                quota_day_limit=0 表示该 user 不限 → 显示"今天用了 X (不限)". */}
            {data.request_count > 0 ? (
              <QuotaProgressBar used={data.total_tokens} limit={data.quota_day_limit} />
            ) : (
              <p
                style={{
                  fontSize: 13,
                  color: "var(--catfish-text-muted)",
                  margin: "0 0 var(--space-2)",
                  padding: "10px 12px",
                  background: "var(--catfish-bg, transparent)",
                  border: "1px dashed var(--catfish-border)",
                  borderRadius: 4,
                }}
              >
                今天还没找过我 — 服务器今天没记你任何调用.
              </p>
            )}

            {/* 历史 — 简短一行, 不抢眼 */}
            {(data.first_seen_ts || data.last_seen_ts) && (
              <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", margin: "0 0 var(--space-2)" }}>
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

            {/* "按模型拆开" / "技术细节" / "想给老板看" 区全删 (6/1 鸿波拍简化) —
                普通员工/领导消化不了, 反而引发"为啥要技术细节"联想. IT/合规有自己
                的手段 (catfish privacy-audit CLI), 不需要在这卡里教. */}
          </>
        )}
      </section>

      {/* 6/8 BL-EMPLOYEE-SELF-SERVE A1+A2+A4: 员工自助工具入口. manifesto 公理 1
          (员工主权) 产品落地 — IT 没远程触发能力, 全员工自己点 button. */}
      <SelfServeButtons />
    </div>
  );
}

/** 6/8 BL-EMPLOYEE-SELF-SERVE UI v2 真根因 fix: macOS WKWebView 默认禁用
 *  window.prompt() / window.alert() / window.confirm() (Apple 安全策略,
 *  Tauri 1+ 都遵守), 静默返回 null/undefined — 看起来按钮"无效".
 *
 *  改 React state inline panel:
 *  - 重置: 两段 inline 确认 (preview 展示 + 输入"我确认"+ 按钮)
 *  - 导出: inline path input + button (不依赖 file picker)
 *  - 外发记录: 展开 inline table (10 条) + inline 上下行总量, 没 modal
 *
 *  操作完, inline 显示状态 toast (5s 后自动消失或下次操作清除).
 */
type Mode =
  | { kind: "idle" }
  | {
      kind: "reset-preview";
      summary: import("../../lib/tauri").ResetSummary;
      confirmStr: string;
    }
  | { kind: "export"; path: string }
  | {
      kind: "log";
      result: import("../../lib/tauri").TransparentLogQueryResult;
    };

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

  const onLogClick = async () => {
    setToast(null);
    setBusy(true);
    try {
      const lib = await import("../../lib/tauri");
      const result = await lib.transparentLogQuery(undefined, undefined, 50);
      setMode({ kind: "log", result });
    } catch (e) {
      setToast({ kind: "err", msg: `查询失败: ${e}` });
    }
    setBusy(false);
  };

  return (
    <section style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-3)" }}>
      <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>🔧 员工自助工具</h4>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 10 }}>
        manifesto 公理 1: 员工主权. 没人能远程触发这些 — 全你自己点.
      </div>
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
          onClick={() => void onLogClick()}
          disabled={busy}
          style={btnStyle("normal")}
          title="审计本机跟 catfish 中央服务交换的每个 HTTP 请求"
        >
          {busy && mode.kind === "idle" ? "查询中…" : "我的数据外发记录"}
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

      {/* inline panel — outbound log 表 */}
      {mode.kind === "log" && (
        <div
          style={{
            marginTop: 10,
            padding: 12,
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            fontSize: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: 8,
            }}
          >
            <span style={{ fontWeight: 600 }}>
              📊 数据外发记录 (近 {mode.result.entries.length} / 共 {mode.result.total})
            </span>
            <button onClick={() => setMode({ kind: "idle" })} style={btnStyle("normal")}>
              收起
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 8 }}>
            累计上行 {(mode.result.bytesUploadedTotal / 1024).toFixed(1)} KB / 下行{" "}
            {(mode.result.bytesDownloadedTotal / 1024).toFixed(1)} KB.
            <br />
            sqlite3 ~/.catfish/outbound_log.db 也可看原始数据 (绕过此 UI, 物理可证).
          </div>
          <div
            style={{
              maxHeight: 280,
              overflow: "auto",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              fontFamily: "monospace",
              fontSize: 11,
            }}
          >
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "var(--catfish-bg-elevated)" }}>
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
                {mode.result.entries.map((e) => (
                  <tr key={e.id} style={{ borderTop: "1px solid var(--catfish-border)" }}>
                    <td style={tdStyle}>{e.tsRequest.slice(11, 19)}</td>
                    <td style={tdStyle}>{e.method}</td>
                    <td
                      style={{
                        ...tdStyle,
                        maxWidth: 280,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={e.url}
                    >
                      {e.url.replace(/^https?:\/\/[^/]+/, "")}
                    </td>
                    <td style={tdStyle}>{e.category ?? "-"}</td>
                    <td style={tdStyle}>{e.status ?? (e.error ? "ERR" : "?")}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{e.requestBytes}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{e.responseBytes}</td>
                  </tr>
                ))}
                {mode.result.entries.length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ ...tdStyle, color: "var(--catfish-text-muted)" }}>
                      还没有 outbound 请求记录 — 启动 catfish 后会自动累积.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

const thStyle: React.CSSProperties = {
  padding: "4px 6px",
  textAlign: "left",
  fontWeight: 600,
  fontSize: 11,
  color: "var(--catfish-text-muted)",
};

const tdStyle: React.CSSProperties = {
  padding: "3px 6px",
  fontSize: 11,
  color: "var(--catfish-text)",
};

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
