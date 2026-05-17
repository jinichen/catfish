/** /audit — 审计大查询 (manager + admin) (BL-ARCH1 5/10).
 *
 * 改造历史:
 *   BL-ARCH1 (5/10):     P0 初版 — 4 stat 卡 + 3 表格
 *   BL-AUDIT-P0-FIX:     修数据信任 bug + (未分组) 桶
 *   BL-AUDIT-P0-FIX-V2:  sysadmin 也算全公司视角
 *   BL-AUDIT-UX-P0 (5/17): UX 工程师化重做 — KPI hero / 模型 friendly /
 *                          inline bar / 异常告警占位
 */

import { useEffect, useState, type ReactNode } from "react";

import { Card } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import { getModelDisplay } from "../lib/modelDisplay";
import {
  fetchGlobalAudit,
  type GlobalAudit,
} from "../lib/me";

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** 粗估成本 (RMB) — 公网平均 0.02¥/1K tokens (中位 catalog 价).
 *  这是 P0 用的 rough number, BL-AUDIT-UX-P2 真做时按 model × tokens 精算. */
function estimateCostRMB(tokens: number): string {
  const rmb = (tokens / 1000) * 0.02;
  if (rmb < 1) return `≈ ¥${rmb.toFixed(2)}`;
  if (rmb < 100) return `≈ ¥${rmb.toFixed(1)}`;
  return `≈ ¥${Math.round(rmb)}`;
}

export function AuditPage() {
  const [audit, setAudit] = useState<GlobalAudit | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGlobalAudit()
      .then((a) => {
        if (!a) setError("拉取 audit 失败 (没权限或后端报错)");
        else setAudit(a);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <RoleGate require={["manager", "admin"]}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        {error && (
          <Card title="错误">
            <div style={{ color: "var(--status-err)" }}>{error}</div>
          </Card>
        )}
        {!audit && !error && <div>加载中…</div>}
        {audit && (
          <>
            {/* ── 标题区 ── */}
            <PageHeader audit={audit} />

            {/* ── 异常告警条 (BL-AUDIT-UX-P0 占位, P1 backend 出 trend 后实数) ── */}
            <AnomalyBanner audit={audit} />

            {/* ── KPI 区: 主指标 hero + 3 个辅助 ── */}
            <KPIHero audit={audit} />

            {/* BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 单独透明度. */}
            {(audit.internal_tokens ?? 0) > 0 && (
              <InternalLoopbackCard audit={audit} />
            )}

            {/* ── 数据对账 (BL-AUDIT-P0-FIX, 数字不一致时显, 一致时静默) ── */}
            <DataReconciliation audit={audit} />

            {/* ── 按模型 (横向 bar + 比例 % + 友好名 + 颜色) ── */}
            <ModelBreakdownCard audit={audit} />

            {/* ── 按部门 (横向 bar) ── */}
            <DeptBreakdownCard audit={audit} />

            {/* ── 按员工 (横向 bar, top N) ── */}
            <UserBreakdownCard audit={audit} />
          </>
        )}
      </div>
    </RoleGate>
  );
}

// ════════════════════════════════════════════════════════════════════
//                          各 section 组件
// ════════════════════════════════════════════════════════════════════

function PageHeader({ audit }: { audit: GlobalAudit }) {
  const scope =
    audit.viewer_role === "admin" || audit.viewer_role === "sysadmin"
      ? "全公司视角"
      : "本部门视角";
  return (
    <div>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 600,
          margin: 0,
          marginBottom: 4,
        }}
      >
        LLM 使用审计
      </h1>
      <div
        style={{
          fontSize: 13,
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        最近 24 小时 · {scope}
        <span
          title="本表不含 gateway 内部循环消耗 (summarizer / proactive / 5 维 inject). 内部消耗见下方独立卡."
          style={{ cursor: "help" }}
        >
          ⓘ
        </span>
      </div>
    </div>
  );
}

/** BL-AUDIT-UX-P0: 异常告警条占位 (放最顶, 第一时间抓眼).
 *
 * 当前数据无 trend 字段, 这个组件**永远 return null**. P1 后端在
 * GlobalAudit 加 `anomalies: [{user, dept, model, ratio, threshold}]` 后,
 * 这里直接 map 出红/黄条 — 已经准备好接口位置.
 */
function AnomalyBanner({ audit: _audit }: { audit: GlobalAudit }) {
  // P1 占位: backend 加 anomalies 后这里就有实数据
  const anomalies: { kind: string; text: string }[] = [];
  if (anomalies.length === 0) return null;
  return (
    <Card title="⚠️ 异常告警">
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {anomalies.map((a, i) => (
          <li key={i} style={{ color: "var(--status-warn, #b45309)" }}>
            {a.text}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** BL-AUDIT-UX-P0: KPI 主指标 hero.
 *
 * - 总 tokens 是主指标 (烧钱的): 36pt 大字 + 成本估算
 * - 总请求 / 活跃员工 / 活跃部门 是辅助: 18pt 中字, 横向并列
 */
function KPIHero({ audit }: { audit: GlobalAudit }) {
  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.6fr 1fr 1fr 1fr",
          gap: "var(--space-4)",
          alignItems: "stretch",
        }}
      >
        {/* Hero: 总 tokens */}
        <div
          style={{
            borderRight: "1px solid var(--border)",
            paddingRight: "var(--space-4)",
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: 0.6,
              marginBottom: 4,
            }}
          >
            💰 总 tokens · 24h
          </div>
          <div
            style={{
              fontSize: 40,
              fontWeight: 600,
              lineHeight: 1.1,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmtTokens(audit.total_tokens)}
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              marginTop: 4,
            }}
          >
            {estimateCostRMB(audit.total_tokens)} 估算成本
          </div>
        </div>

        {/* 辅 1: 总请求 */}
        <SubStat
          label="📊 总请求"
          value={audit.request_count.toLocaleString()}
        />
        {/* 辅 2: 活跃员工 */}
        <SubStat label="👤 活跃员工" value={audit.active_users.toLocaleString()} />
        {/* 辅 3: 活跃部门 */}
        <SubStat
          label="🏢 活跃部门"
          value={audit.active_departments.toLocaleString()}
          hint="只数有部门归属的员工"
        />
      </div>
    </div>
  );
}

function SubStat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: 0.6,
          marginBottom: 4,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        {label}
        {hint && (
          <span style={{ cursor: "help" }} title={hint}>
            ⓘ
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function InternalLoopbackCard({ audit }: { audit: GlobalAudit }) {
  return (
    <Card title="🔁 Gateway 内部循环消耗">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-3)",
        }}
      >
        <SubStat
          label="内部请求"
          value={(audit.internal_request_count ?? 0).toLocaleString()}
        />
        <SubStat
          label="内部 tokens"
          value={fmtTokens(audit.internal_tokens ?? 0)}
          hint="summarizer / proactive / 5 维 inject 等 gateway 自调消耗. 跟员工业务无关, 但占真实 LLM 账单."
        />
      </div>
    </Card>
  );
}

/** BL-AUDIT-UX-P0: 按模型 — 横向 bar + 比例 % + 友好名 + 颜色. */
function ModelBreakdownCard({ audit }: { audit: GlobalAudit }) {
  const total = audit.by_model.reduce((s, m) => s + m.total_tokens, 0);
  return (
    <Card title="按模型用量">
      <Table
        rows={audit.by_model.map((m) => {
          const md = getModelDisplay(m.model);
          const pct = total > 0 ? (m.total_tokens / total) * 100 : 0;
          return {
            key: m.model,
            label: (
              <span
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                <span>{md.dotEmoji}</span>
                <span style={{ fontWeight: 500 }}>{md.friendly}</span>
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    padding: "1px 6px",
                    background: "var(--bg-elev)",
                    borderRadius: 8,
                  }}
                >
                  {md.tier}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                  }}
                  title={m.model}
                >
                  {/* 鼠标 hover 显示 catalog ID */}
                </span>
              </span>
            ),
            count: m.count,
            tokens: m.total_tokens,
            pct,
            barColor: md.color,
          };
        })}
      />
    </Card>
  );
}

function DeptBreakdownCard({ audit }: { audit: GlobalAudit }) {
  if (!audit.by_department || audit.by_department.length === 0) return null;
  const total = audit.by_department.reduce((s, d) => s + d.total_tokens, 0);
  return (
    <Card title="按部门用量">
      <Table
        rows={audit.by_department.map((d, i) => {
          const isUnassigned = d.department === "(未分组)";
          const pct = total > 0 ? (d.total_tokens / total) * 100 : 0;
          return {
            key: d.department,
            label: (
              <span
                style={{
                  fontStyle: isUnassigned ? "italic" : "normal",
                  color: isUnassigned ? "var(--text-muted)" : undefined,
                }}
                title={
                  isUnassigned
                    ? "该桶里员工没绑部门 (dev_token / 老员工 / OIDC 缺 dept claim). Day 8 客户接入手册要求 SSO 必传 dept."
                    : undefined
                }
              >
                {d.department}
              </span>
            ),
            count: d.count,
            tokens: d.total_tokens,
            pct,
            barColor: _palette(i),
          };
        })}
      />
    </Card>
  );
}

function UserBreakdownCard({ audit }: { audit: GlobalAudit }) {
  if (audit.by_user.length === 0) {
    return (
      <Card title="按员工用量">
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          本期暂无员工业务请求.
        </div>
      </Card>
    );
  }
  const total = audit.by_user.reduce((s, u) => s + u.total_tokens, 0);
  const capped = audit.by_user.length >= 50;
  return (
    <Card
      title={
        <span>
          按员工用量
          {capped && (
            <span
              style={{
                marginLeft: 8,
                fontSize: 11,
                color: "var(--text-muted)",
                fontWeight: 400,
              }}
            >
              (top 50 上限)
            </span>
          )}
        </span>
      }
    >
      <Table
        showExtraColumn="部门"
        rows={audit.by_user.map((u, i) => {
          const isUnassigned = u.user_email === "(未分组员工)";
          const pct = total > 0 ? (u.total_tokens / total) * 100 : 0;
          return {
            key: `${u.user_email}::${u.department}`,
            label: (
              <span
                style={{
                  fontStyle: isUnassigned ? "italic" : "normal",
                  color: isUnassigned ? "var(--text-muted)" : undefined,
                }}
                title={
                  isUnassigned
                    ? "该桶里请求源没拿到 user_email (dev_token / OIDC 缺 email claim)."
                    : undefined
                }
              >
                {u.user_email}
              </span>
            ),
            extra: u.department,
            count: u.count,
            tokens: u.total_tokens,
            pct,
            barColor: _palette(i),
          };
        })}
      />
    </Card>
  );
}

/** BL-AUDIT-UX-P0: 通用表格组件 + 内嵌横向 bar.
 *
 * 每行结构:
 *   [label........]  [横向 bar with %]  [请求数]  [tokens]
 */
function Table({
  rows,
  showExtraColumn,
}: {
  rows: {
    key: string;
    label: ReactNode;
    extra?: string;
    count: number;
    tokens: number;
    pct: number;
    barColor: string;
  }[];
  showExtraColumn?: string;
}) {
  return (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 13,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <thead>
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          <th style={{ textAlign: "left", padding: "6px 8px", width: "30%" }}>
            名称
          </th>
          {showExtraColumn && (
            <th style={{ textAlign: "left", padding: "6px 8px", width: "20%" }}>
              {showExtraColumn}
            </th>
          )}
          <th style={{ textAlign: "left", padding: "6px 8px" }}>占比</th>
          <th style={{ textAlign: "right", padding: "6px 8px", width: 80 }}>
            请求
          </th>
          <th style={{ textAlign: "right", padding: "6px 8px", width: 90 }}>
            tokens
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.key}
            style={{ borderBottom: "1px solid var(--bg-secondary)" }}
          >
            <td style={{ padding: "8px" }}>{r.label}</td>
            {showExtraColumn && (
              <td
                style={{
                  padding: "8px",
                  color: "var(--text-muted)",
                  fontSize: 12,
                }}
              >
                {r.extra}
              </td>
            )}
            <td style={{ padding: "8px" }}>
              <Bar pct={r.pct} color={r.barColor} />
            </td>
            <td style={{ padding: "8px", textAlign: "right" }}>
              {r.count.toLocaleString()}
            </td>
            <td style={{ padding: "8px", textAlign: "right", fontWeight: 500 }}>
              {fmtTokens(r.tokens)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 内嵌横向条 — 比例 % + 染色. */
function Bar({ pct, color }: { pct: number; color: string }) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <div
        style={{
          flex: 1,
          height: 8,
          background: "var(--bg-elev)",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${w}%`,
            height: "100%",
            background: color,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

/** 给部门 / 员工分组用的备用调色板 (前 8 个清亮, 后面回滚). */
function _palette(i: number): string {
  const C = [
    "#5fa8d3", // blue
    "#76b900", // green
    "#7c3aed", // violet
    "#f59e0b", // amber
    "#ef4444", // red
    "#06b6d4", // cyan
    "#ec4899", // pink
    "#8b5cf6", // purple
  ];
  return C[i % C.length];
}

/**
 * BL-AUDIT-P0-FIX (5/17): 数据对账 banner.
 * 全对得上 — 静默不显示. 一致性 sanity check.
 */
function DataReconciliation({ audit }: { audit: GlobalAudit }) {
  const modelSum = audit.by_model.reduce((s, m) => s + m.count, 0);
  const deptSum = audit.by_department?.reduce((s, d) => s + d.count, 0) ?? 0;
  const userSum = audit.by_user.reduce((s, u) => s + u.count, 0);

  const modelOk = modelSum === audit.request_count;
  const deptOk = deptSum === audit.request_count;
  const userOk = userSum === audit.request_count;

  if (modelOk && deptOk && userOk) return null;

  const rows = [
    { label: "按模型加和", sum: modelSum, ok: modelOk },
    { label: "按部门加和", sum: deptSum, ok: deptOk },
    { label: "按员工加和", sum: userSum, ok: userOk },
  ];

  return (
    <Card title="⚠️ 数据对账 (部分分组加和 ≠ 总数)">
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        总请求 <strong>{audit.request_count.toLocaleString()}</strong>, 但:
        <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
          {rows.map((r) => (
            <li
              key={r.label}
              style={{
                color: r.ok ? "inherit" : "var(--status-warn, #b45309)",
              }}
            >
              {r.label} = <strong>{r.sum.toLocaleString()}</strong>{" "}
              {r.ok
                ? "✓"
                : `✗ (差 ${(audit.request_count - r.sum).toLocaleString()})`}
            </li>
          ))}
        </ul>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          典型原因: 老 audit 数据 user_email / department 缺 OIDC claim → 进 "(未分组)"
          桶 (本表已显式列出).
        </div>
      </div>
    </Card>
  );
}
