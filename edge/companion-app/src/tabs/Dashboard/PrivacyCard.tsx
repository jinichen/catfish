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

      {/* ── 本机存储 — 6/1 BL-PRIVACY-CARD-SIMPLIFY 鸿波拍 ─────────────
          原来 3 区 + 5 折叠 + 技术路径 + "看不到 X" 暗示 + IT 命令, 国企
          员工/领导消化不了. 简化到 2 大块, 没折叠, 没暗示, 没技术名词. */}
      <section style={{ marginBottom: "var(--space-4)" }}>
        <h4 style={{ margin: "0 0 var(--space-2)", fontSize: 13 }}>
          🟢 本机存储 — <strong style={{ color: "var(--catfish-cyan)" }}>公司一个字都看不到</strong>
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
          🟡 服务器存储 — 公司能看到这些
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
    </div>
  );
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
