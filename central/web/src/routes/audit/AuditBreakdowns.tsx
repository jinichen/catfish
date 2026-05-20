/** AuditPage 分组明细 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * InternalLoopbackCard / ModelBreakdownCard / DeptBreakdownCard /
 * UserBreakdownCard + Table + Bar (inline 柱状条).
 */

import type { ReactNode } from "react";

import { Card } from "../../components/Card";
import type { GlobalAudit } from "../../lib/me";
import { costRMB, fmtRMB, getModelDisplay } from "../../lib/modelDisplay";
import { _palette, fmtTokens } from "./helpers";
import { SubStat } from "./AuditKPI";


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

/** BL-AUDIT-UX-P0: 按模型 — 横向 bar + 比例 % + 友好名 + 颜色.
 *  BL-AUDIT-UX-P1: + RMB 列 (按 model 单价精算)
 *  BL-AUDIT-UX-P2: + onClickRow drill-down, activeModel 高亮 */
function ModelBreakdownCard({
  audit,
  onClickRow,
  activeModel,
}: {
  audit: GlobalAudit;
  onClickRow: (model: string) => void;
  activeModel: string | null;
}) {
  const total = audit.by_model.reduce((s, m) => s + m.total_tokens, 0);
  return (
    <Card title="按模型用量">
      <Table
        showRmbColumn
        rows={audit.by_model.map((m) => {
          const md = getModelDisplay(m.model);
          const pct = total > 0 ? (m.total_tokens / total) * 100 : 0;
          return {
            key: m.model,
            onClick: () => onClickRow(m.model),
            active: activeModel === m.model,
            label: (
              <span
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
                title={m.model}
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
              </span>
            ),
            count: m.count,
            tokens: m.total_tokens,
            rmb: costRMB(m.model, m.total_tokens),
            pct,
            barColor: md.color,
          };
        })}
      />
    </Card>
  );
}

function DeptBreakdownCard({
  audit,
  onClickRow,
  activeDept,
}: {
  audit: GlobalAudit;
  onClickRow: (dept: string) => void;
  activeDept: string | null;
}) {
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
            onClick: () => onClickRow(d.department),
            active: activeDept === d.department,
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

function UserBreakdownCard({
  audit,
  onClickRow,
  activeUser,
}: {
  audit: GlobalAudit;
  onClickRow: (user_email: string) => void;
  activeUser: string | null;
}) {
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
            onClick: () => onClickRow(u.user_email),
            active: activeUser === u.user_email,
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
 *   [label........]  [extra (可选)] [横向 bar with %]  [请求数]  [tokens]  [RMB?]
 *
 * BL-AUDIT-UX-P1: + showRmbColumn 可选 — 模型表用 (按 model 精算), 部门 / 员工表
 * 不显示 (需要后端把 tokens × model 拆出来才能精算).
 */
function Table({
  rows,
  showExtraColumn,
  showRmbColumn,
}: {
  rows: {
    key: string;
    label: ReactNode;
    extra?: string;
    count: number;
    tokens: number;
    rmb?: number;
    pct: number;
    barColor: string;
    /** BL-AUDIT-UX-P2: 行点击 drill down. 不传 = 行不可点 (光标不变) */
    onClick?: () => void;
    /** BL-AUDIT-UX-P2: 高亮当前 filter 中的那一行 */
    active?: boolean;
  }[];
  showExtraColumn?: string;
  showRmbColumn?: boolean;
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
          {showRmbColumn && (
            <th style={{ textAlign: "right", padding: "6px 8px", width: 90 }}>
              RMB 估算
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.key}
            onClick={r.onClick}
            style={{
              borderBottom: "1px solid var(--bg-secondary)",
              cursor: r.onClick ? "pointer" : "default",
              background: r.active
                ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                : "transparent",
              transition: "background 0.1s",
            }}
            onMouseEnter={(e) => {
              if (r.onClick && !r.active) {
                e.currentTarget.style.background = "var(--bg-elev)";
              }
            }}
            onMouseLeave={(e) => {
              if (r.onClick && !r.active) {
                e.currentTarget.style.background = "transparent";
              }
            }}
            title={r.onClick ? "点击筛选只看这一行" : undefined}
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
            {showRmbColumn && (
              <td
                style={{
                  padding: "8px",
                  textAlign: "right",
                  color: "var(--text-muted)",
                  fontSize: 12,
                }}
              >
                {r.rmb !== undefined ? fmtRMB(r.rmb).replace("≈ ", "") : "—"}
              </td>
            )}
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

export { InternalLoopbackCard, ModelBreakdownCard, DeptBreakdownCard, UserBreakdownCard };
