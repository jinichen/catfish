/** Audit 仪表盘卡片 — 中央可审 demo 卖点兑现.
 *
 * 展示:
 *   - 今日请求数 (ok / error)
 *   - 今日 token 总量
 *   - 模型分布 (private vs public, 兑现"本地优先")
 *   - TTFT p50 + p95 (上游性能)
 *   - 安全事件计数 (密码泄漏 / 重路由 等)
 *   - 数据新鲜度
 *
 * 不展示:
 *   - 对话内容 (那是 audit 边界写死的)
 *   - 单条 trace (这是 dashboard 不是 log viewer)
 */

import { useAudit } from "../../hooks/useAudit";
import { formatTokens } from "../../lib/format";
import type { AuditSummary } from "../../types/audit";
import { useAgentStore } from "../../store/agent";

export default function AuditCard() {
  const { summary, error } = useAudit();
  // BL-E11 后续: 提示语用员工自定义名
  const agentName = useAgentStore((s) => s.name);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1", // 占整行 (卡片信息密度高)
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0 }}>📊 今日审计</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          中央只看 metadata (无对话内容) · 每 30s 自动刷新
        </span>
        {summary && summary.data_freshness_secs >= 0 && (
          <FreshnessTag secs={summary.data_freshness_secs} />
        )}
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>
          读取失败: {error}
        </div>
      )}
      {!summary && !error && <div>加载中…</div>}

      {summary && summary.request_count === 0 && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          今天还没请求 — 跟{agentName}聊点什么试试.
        </div>
      )}

      {summary && summary.request_count > 0 && <SummaryGrid summary={summary} />}
    </div>
  );
}

function FreshnessTag({ secs }: { secs: number }) {
  const label =
    secs < 60 ? `${secs}s 前` : secs < 3600 ? `${Math.floor(secs / 60)}min 前` : "数据较旧";
  const color =
    secs < 120
      ? "var(--catfish-cyan)"
      : secs < 1800
      ? "var(--catfish-text-muted)"
      : "var(--status-warn, orange)";
  return (
    <span style={{ fontSize: 11, color, marginLeft: "auto" }}>
      最新数据: {label}
    </span>
  );
}

function SummaryGrid({ summary }: { summary: AuditSummary }) {
  const errorRate =
    summary.request_count === 0
      ? 0
      : (summary.error_count / summary.request_count) * 100;
  const privateCount = summary.by_model
    .filter((m) => m.is_private)
    .reduce((acc, m) => acc + m.count, 0);
  const privatePct =
    summary.request_count === 0
      ? 0
      : (privateCount / summary.request_count) * 100;

  return (
    <>
      {/* 4 个核心数字 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "var(--space-3)",
          marginBottom: "var(--space-4)",
        }}
      >
        <Stat label="请求" value={String(summary.request_count)} />
        <Stat
          label="错误率"
          value={`${errorRate.toFixed(1)}%`}
          tone={errorRate > 5 ? "warn" : undefined}
        />
        <Stat label="Token" value={formatTokens(summary.total_tokens)} />
        <Stat
          label="本地优先"
          value={`${privatePct.toFixed(0)}%`}
          tone={privatePct >= 60 ? "good" : "warn"}
        />
      </div>

      {/* TTFT 性能 */}
      {summary.ttft_p50_ms !== null && (
        <div
          style={{
            marginBottom: "var(--space-4)",
            padding: "var(--space-3)",
            background: "var(--catfish-bg)",
            borderRadius: "var(--radius-sm)",
            display: "flex",
            gap: "var(--space-4)",
            fontSize: 12,
          }}
        >
          <span style={{ color: "var(--catfish-text-muted)" }}>
            首 token 延迟 (TTFT):
          </span>
          <span>
            <strong>p50</strong>{" "}
            {formatLatencyMs(summary.ttft_p50_ms)}
          </span>
          <span>
            <strong>p95</strong>{" "}
            <span
              style={{
                color:
                  summary.ttft_p95_ms !== null && summary.ttft_p95_ms > 30_000
                    ? "var(--status-warn, orange)"
                    : undefined,
              }}
            >
              {formatLatencyMs(summary.ttft_p95_ms ?? 0)}
            </span>
          </span>
          {summary.ttft_p95_ms !== null && summary.ttft_p95_ms > 30_000 && (
            <span
              style={{ color: "var(--status-warn, orange)", marginLeft: "auto" }}
            >
              ⚠ p95 &gt; 30s, 上游可能拥堵
            </span>
          )}
        </div>
      )}

      {/* 模型分布 */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: "var(--space-2)",
          }}
        >
          模型用量分布:
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          {summary.by_model.slice(0, 5).map((m) => (
            <ModelRow
              key={m.model}
              usage={m}
              total={summary.request_count}
            />
          ))}
          {summary.by_model.length > 5 && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              + {summary.by_model.length - 5} 个其他模型
            </span>
          )}
        </div>
      </div>

      {/* 安全事件 */}
      <SecurityRow concerns={summary.security_concerns} />
    </>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "warn";
}) {
  const color =
    tone === "warn"
      ? "var(--status-warn, orange)"
      : tone === "good"
      ? "var(--catfish-cyan)"
      : "var(--catfish-text)";
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 600,
          fontFamily: "var(--font-mono)",
          color,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function ModelRow({
  usage,
  total,
}: {
  usage: { model: string; count: number; total_tokens: number; is_private: boolean };
  total: number;
}) {
  const pct = total === 0 ? 0 : (usage.count / total) * 100;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr 60px 80px",
        gap: "var(--space-2)",
        fontSize: 12,
        alignItems: "center",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: usage.is_private
            ? "var(--catfish-cyan)"
            : "var(--catfish-text-muted)",
        }}
        title={usage.is_private ? "私有模型 (内网, 数据不出)" : "公网模型"}
      />
      <span style={{ fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {usage.model}
      </span>
      <span
        style={{
          textAlign: "right",
          color: "var(--catfish-text-muted)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {usage.count} 次
      </span>
      <span
        style={{
          textAlign: "right",
          color: "var(--catfish-text-muted)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {formatTokens(usage.total_tokens)} tok
      </span>
      <span
        style={{
          gridColumn: "1 / -1",
          height: 3,
          background: "var(--catfish-border)",
          borderRadius: 2,
          overflow: "hidden",
          marginTop: 2,
        }}
      >
        <span
          style={{
            display: "block",
            height: "100%",
            width: `${pct}%`,
            background: usage.is_private
              ? "var(--catfish-cyan)"
              : "var(--catfish-text-muted)",
          }}
        />
      </span>
    </div>
  );
}

function SecurityRow({
  concerns,
}: {
  concerns: { kind: string; count: number }[];
}) {
  if (concerns.length === 0) {
    return (
      <div
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          padding: "var(--space-2) var(--space-3)",
          background: "var(--catfish-bg)",
          borderRadius: "var(--radius-sm)",
        }}
      >
        ✓ 今日没检测到安全事件
      </div>
    );
  }
  return (
    <div
      style={{
        padding: "var(--space-2) var(--space-3)",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        borderLeft: "3px solid var(--status-warn, orange)",
      }}
    >
      <div
        style={{
          fontSize: 13,
          color: "var(--catfish-text)",
          marginBottom: "var(--space-2)",
          fontWeight: 500,
        }}
      >
        {/* 5/5 鸿波两轮反馈:
            v1 "今日安全事件 ×434" 看着吓人 → 改"提醒类型"中性化 (5/5 早)
            v2 "提醒类型 + 建议改用 secret_ref" 仍看不懂 → 改员工话 (5/5 晚) */}
        💡 鲶鱼小贴士
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        {concerns.map((c) => (
          <div
            key={c.kind}
            style={{
              fontSize: 12,
              padding: "var(--space-2) var(--space-3)",
              background: "var(--catfish-bg-elevated)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              lineHeight: 1.5,
            }}
          >
            {kindBody(c.kind)}
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 8 }}>
        ℹ️ 这些不是错误, 也不影响使用. 同一句话在多次对话里会重复触发, 数字不用较真.
      </div>
    </div>
  );
}

/** 把内部 event kind 翻成员工能看懂的人话.
 *
 * 5/5 鸿波: 'prompt_credential_detected / 建议改用 secret_ref' 是工程术语堆砌,
 * 员工看不懂. 改成"我做了啥, 鲶鱼建议我下次咋做"的口语化描述.
 */
function kindBody(kind: string): JSX.Element {
  if (kind === "prompt_credential_detected") {
    return (
      <span>
        <strong>你今天打字时直接发过密码/token</strong> — 不影响使用, 但<strong>下次更安全的做法</strong>:
        告诉鲶鱼&nbsp;<code style={{
          background: "var(--catfish-bg-cream)",
          padding: "0 4px",
          borderRadius: 3,
          fontSize: 11,
        }}>用 keychain://eis_password 登录</code>
        &nbsp;(密码留在你 macOS Keychain 里, 不进对话, 不进日志).
      </span>
    );
  }
  if (kind === "credential_field_filled") {
    return (
      <span>
        <strong>浏览器自动填了密码字段</strong> — 鲶鱼帮你跑浏览器任务时, Keychain 里的密码被填进网页表单了, 已记录到审计日志.
      </span>
    );
  }
  return <span>{kind}</span>;
}

function formatLatencyMs(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
