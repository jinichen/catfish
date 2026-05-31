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
          这页让你自己看 — 不用问 IT · 30s 自动刷新
        </span>
      </div>

      {/* ── 区 1: 本机数据 ───────────────────────── */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          这些东西在你电脑这, <strong style={{ color: "var(--catfish-cyan)" }}>公司一个字都看不到</strong>
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
              <span
                style={{
                  fontFamily: "monospace",
                  fontSize: 10,
                  color: "var(--catfish-text-muted)",
                  flexShrink: 0,
                }}
                title="技术路径 (IT 自查时用)"
              >
                {it.path}
              </span>
            </li>
          ))}
        </ul>
        <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 10 }}>
          不放心? 在 Finder 按 ⌘+⇧+G 输入上面任一路径, 自己进去看. 或终端跑{" "}
          <code>catfish privacy-audit</code> 一次性扫全.
        </p>
      </section>

      {/* ── 区 2: 公司服务器看到啥 ─────────────────── */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          公司服务器看到的 — 全部在这了
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
            {/* 看到 / 看不到 二分清楚 */}
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
              <div>
                <span style={{ color: "var(--catfish-cyan)", marginRight: 6 }}>✓ 公司看到</span>
                你的邮箱 · 调用时间 · 用了哪个模型 · 用了多少额度
              </div>
              <div>
                <span style={{ color: "var(--status-warn, #c98b00)", marginRight: 6 }}>✗ 公司看不到</span>
                你说的内容 · 我回的内容 · 你上传的文件
              </div>
            </div>

            {/* 今天活动 */}
            {data.request_count > 0 ? (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                  gap: "var(--space-2)",
                  marginBottom: "var(--space-2)",
                }}
              >
                <Stat label="今天找我" value={`${data.request_count} 次`} />
                <Stat label="今天用了" value={`${formatTokens(data.total_tokens)} 额度`} />
                {data.last_seen_ts && (
                  <Stat
                    label="最近一次"
                    value={new Date(data.last_seen_ts).toLocaleTimeString()}
                  />
                )}
              </div>
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

            {data.by_model.length > 0 && (
              <details style={{ fontSize: 12 }}>
                <summary style={{ cursor: "pointer", color: "var(--catfish-text-muted)" }}>
                  按模型拆开 ({data.by_model.length} 个)
                </summary>
                <ul style={{ marginTop: 4, paddingLeft: 20 }}>
                  {data.by_model.slice(0, 10).map((m) => (
                    <li key={m.model}>
                      {m.model}: {m.count} 次, {formatTokens(m.total_tokens)} 额度
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {/* 技术细节折叠 — 默认收起, 老板 / IT 自验时点开 */}
            <details
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginTop: 10,
              }}
            >
              <summary style={{ cursor: "pointer" }}>技术细节 (IT / 合规审计自验用)</summary>
              <p style={{ marginTop: 6, padding: "6px 8px", border: "1px dashed var(--catfish-border)", borderRadius: 4 }}>
                数据从 <code>GET /api/audit/me</code> 实时拉, 30 秒自动刷新. 服务器响应只含
                metadata: count / tokens / model / 时间戳, 代码层面没有 prompt / response
                字段. 完整 schema: {data.schema_note}
              </p>
            </details>
          </>
        )}
      </section>

      {/* ── 区 3: 给老板看 / 想自己核 ─────────────── */}
      <section>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>想给老板 / 合规看?</h4>
        <ul style={{ fontSize: 13, paddingLeft: 20, margin: 0, lineHeight: 1.8 }}>
          <li>
            <strong>最快</strong>: 把这页截图发给老板. 上面看到啥老板就看到啥, 你跟我聊的内容
            不在.
          </li>
          <li>
            <strong>给 IT / 合规</strong>: 终端跑 <code>catfish privacy-audit --json</code>,
            生成一份本机 + 服务器的扫描报告 (机读 / 入存档).
          </li>
          <li>
            <strong>看每条数据具体啥意思</strong>:{" "}
            <a
              href="https://github.com/example/catfish/blob/main/docs/EMPLOYEE-PRIVACY-VERIFICATION.md"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--catfish-cyan)" }}
            >
              员工隐私自验手册
            </a>{" "}
            — 写给员工看, 不是给程序员.
          </li>
          <li>
            <strong>想撤掉服务器上的记录</strong>? 你自己改不了 (admin 权限). 走 catfish-web
            /admin 提工单, IT 处理.
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
