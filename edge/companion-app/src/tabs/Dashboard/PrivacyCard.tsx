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
// 这里展示用, 不真去 stat 文件 (Tauri 命令成本 vs 信息价值不值得).
const LOCAL_DATA_ITEMS: { path: string; what: string; uploaded: boolean }[] = [
  { path: "~/.hermes/sessions/", what: "对话历史 (prompt + response 全文)", uploaded: false },
  { path: "~/.hermes/memories/", what: "鲶鱼对你的长期记忆 (USER.md / MEMORY.md)", uploaded: false },
  { path: "~/.hermes/.env", what: "第三方 API key (Tavily 等, 本机调外网用)", uploaded: false },
  { path: "~/.catfish/auth/token.json", what: "你的 OAuth token (中央认证用, 不是对话)", uploaded: false },
  { path: "~/.catfish/gateway_audit.jsonl", what: "本机 audit log 历史归档 (PG-only 模式不再写, 见段 3 的 PG 数据)", uploaded: false },
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
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ margin: 0 }}>🔒 隐私状态</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          你能验证, 不需要纯信任公司说辞 · 30s 自动刷新
        </span>
      </div>

      {/* ── 区 1: 本机数据 ───────────────────────── */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          本机数据 — 在你电脑这, <strong style={{ color: "var(--catfish-cyan)" }}>不上传</strong>
        </h4>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 12,
          }}
        >
          <thead>
            <tr style={{ textAlign: "left", color: "var(--catfish-text-muted)" }}>
              <th style={{ padding: "4px 8px 4px 0", fontWeight: 400 }}>路径</th>
              <th style={{ padding: "4px 8px", fontWeight: 400 }}>是什么</th>
              <th style={{ padding: "4px 0", fontWeight: 400, width: 80 }}>上传?</th>
            </tr>
          </thead>
          <tbody>
            {LOCAL_DATA_ITEMS.map((it) => (
              <tr key={it.path} style={{ borderTop: "1px solid var(--catfish-border)" }}>
                <td style={{ padding: "6px 8px 6px 0", fontFamily: "monospace", color: "var(--catfish-text)" }}>
                  {it.path}
                </td>
                <td style={{ padding: "6px 8px", color: "var(--catfish-text-muted)" }}>
                  {it.what}
                </td>
                <td style={{ padding: "6px 0" }}>
                  {it.uploaded ? (
                    <span style={{ color: "var(--status-warn, #c98b00)", fontSize: 11 }}>
                      metadata 上传
                    </span>
                  ) : (
                    <span style={{ color: "var(--catfish-cyan)", fontSize: 11 }}>
                      ✓ 不上传
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 6 }}>
          想自己进文件夹核对? Finder → 前往 → 输入路径. 跑 <code>catfish privacy-audit</code> 一次性看全.
        </p>
      </section>

      {/* ── 区 2: 中央存了我啥 ───────────────────── */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          中央存了我啥 — <code>GET /api/audit/me</code> 实时拉
        </h4>

        {error && (
          <div style={{ fontSize: 12, color: "var(--status-err, #c93a3a)" }}>
            拉失败: {error} <br />
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
              (离线 / 未登录 / 中央挂. 本机段不受影响.)
            </span>
          </div>
        )}

        {!data && !error && <div style={{ fontSize: 12 }}>加载中…</div>}

        {data && (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                gap: "var(--space-2)",
                marginBottom: "var(--space-2)",
              }}
            >
              <Stat label="今日请求" value={String(data.request_count)} />
              <Stat label="今日 tokens" value={formatTokens(data.total_tokens)} />
              <Stat
                label="最早记录"
                value={
                  data.first_seen_ts
                    ? new Date(data.first_seen_ts).toLocaleDateString()
                    : "(无)"
                }
              />
              <Stat
                label="最新记录"
                value={
                  data.last_seen_ts
                    ? new Date(data.last_seen_ts).toLocaleString()
                    : "(无)"
                }
              />
            </div>

            {data.by_model.length > 0 && (
              <details style={{ fontSize: 12 }}>
                <summary style={{ cursor: "pointer", color: "var(--catfish-text-muted)" }}>
                  今日按模型 ({data.by_model.length} 个)
                </summary>
                <ul style={{ marginTop: 4, paddingLeft: 20 }}>
                  {data.by_model.slice(0, 10).map((m) => (
                    <li key={m.model}>
                      <code>{m.model}</code>: {m.count} 次, {formatTokens(m.total_tokens)} tokens
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <p
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginTop: 8,
                padding: "6px 8px",
                background: "var(--catfish-bg, transparent)",
                border: "1px dashed var(--catfish-border)",
                borderRadius: 4,
              }}
            >
              <strong style={{ color: "var(--catfish-text)" }}>schema 契约:</strong>{" "}
              {data.schema_note}
            </p>
          </>
        )}
      </section>

      {/* ── 区 3: 一键查看 / 文档 ─────────────────── */}
      <section>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>深入查 / 给老板看</h4>
        <ul style={{ fontSize: 12, paddingLeft: 20, margin: 0 }}>
          <li>
            终端跑 <code>catfish privacy-audit</code> — 本机 + 中央一次性扫, 给老板看可以加{" "}
            <code>--json</code>
          </li>
          <li>
            员工 doc:{" "}
            <a
              href="https://github.com/example/catfish/blob/main/docs/EMPLOYEE-PRIVACY-VERIFICATION.md"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--catfish-cyan)" }}
            >
              docs/EMPLOYEE-PRIVACY-VERIFICATION.md
            </a>{" "}
            — 解释每个数据项 / 怎么自验 / 异常怎么报
          </li>
          <li>
            想撤数据 / 改 quota / 改部门? 走 catfish-web /admin 提工单, 员工自己改不了
          </li>
        </ul>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "8px 10px",
        background: "var(--catfish-bg, transparent)",
        border: "1px solid var(--catfish-border)",
        borderRadius: 4,
      }}
    >
      <div style={{ fontSize: 10, color: "var(--catfish-text-muted)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  );
}
