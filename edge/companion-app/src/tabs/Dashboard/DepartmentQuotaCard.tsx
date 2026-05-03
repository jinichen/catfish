/** Manager / Admin 看本部门 Quota 聚合 — 五一 sprint 5/2 RBAC.
 *
 * 数据: GET /api/quota/department/{dept}
 * 内容:
 *   - 部门今日 token 用量 + limit (limit=0 = 不限)
 *   - Top 10 员工今日用量 (raw email 不脱敏 — manager 自己部门, 默认看得到)
 *
 * 五一 sprint 5/2 收尾加 inline edit: 点 "改限额" → input + 保存 →
 * PUT /api/quota/department/{dept}, gateway 写 quotas.yaml + 自动重读.
 */

import { useEffect, useState } from "react";

import {
  fetchDepartmentQuota,
  type DepartmentQuota,
  updateDepartmentQuota,
} from "../../lib/me";
import { formatTokens } from "../../lib/format";

interface Props {
  department: string;
}

export default function DepartmentQuotaCard({ department }: Props) {
  const [data, setData] = useState<DepartmentQuota | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const q = await fetchDepartmentQuota(department);
        if (!cancelled) {
          setData(q);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const t = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [department]);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0 }}>👥 部门 · {department}</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          经理视图 · 今日聚合
        </span>
      </div>

      {error && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          quota 服务未就绪 ({error})
        </div>
      )}
      {!data && !error && (
        <div style={{ height: 40, background: "var(--catfish-border)", opacity: 0.4, borderRadius: 4 }} />
      )}

      {data && <DeptQuotaBody data={data} onUpdated={() => window.location.reload()} />}
    </div>
  );
}

function QuotaEditor({
  department,
  current,
  onSaved,
}: {
  department: string;
  current: number;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(String(current));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSave = async () => {
    const n = parseInt(val, 10);
    if (Number.isNaN(n) || n < 0) {
      setErr("必须是非负整数 (0 = 不限)");
      return;
    }
    setSaving(true);
    setErr(null);
    const r = await updateDepartmentQuota(department, n);
    setSaving(false);
    if (r.ok) {
      setEditing(false);
      onSaved();
    } else {
      setErr(r.detail || "保存失败");
    }
  };

  if (!editing) {
    return (
      <button
        onClick={() => {
          setVal(String(current));
          setEditing(true);
          setErr(null);
        }}
        style={{
          fontSize: 11,
          padding: "2px 8px",
          background: "transparent",
          border: "1px solid var(--catfish-border)",
          borderRadius: 3,
          cursor: "pointer",
          color: "var(--catfish-text-muted)",
        }}
        title="改本部门日 quota (写 quotas.yaml)"
      >
        改限额
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        <input
          type="number"
          min={0}
          value={val}
          onChange={(e) => setVal(e.target.value)}
          disabled={saving}
          style={{
            width: 130,
            fontSize: 12,
            padding: "2px 6px",
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            fontFamily: "var(--font-mono)",
          }}
          placeholder="tokens/day, 0=不限"
        />
        <button
          onClick={() => void handleSave()}
          disabled={saving}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "var(--catfish-cyan)",
            color: "white",
            border: "none",
            borderRadius: 3,
            cursor: saving ? "wait" : "pointer",
          }}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={saving}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: "pointer",
            color: "var(--catfish-text-muted)",
          }}
        >
          取消
        </button>
      </div>
      {err && (
        <div style={{ fontSize: 11, color: "var(--status-err, #d33)" }}>{err}</div>
      )}
    </div>
  );
}

function DeptQuotaBody({
  data,
  onUpdated,
}: {
  data: DepartmentQuota;
  onUpdated: () => void;
}) {
  const unlimited = data.day.limit === 0;
  const pct = unlimited ? 0 : Math.min((data.day.used / data.day.limit) * 100, 100);

  return (
    <>
      {/* 部门日聚合 + 改限额按钮 */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: 4,
          }}
        >
          <span>部门今日 (滑动 24h)</span>
          <QuotaEditor department={data.department} current={data.day.limit} onSaved={onUpdated} />
        </div>
        <div style={{ fontSize: 22, fontWeight: 600, fontFamily: "var(--font-mono)" }}>
          {formatTokens(data.day.used)}
          <span style={{ fontSize: 13, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
            {" "}/ {unlimited ? "不限" : `${formatTokens(data.day.limit)} tok`}
          </span>
        </div>
        {!unlimited && (
          <div style={{ height: 6, background: "var(--catfish-border)", borderRadius: 3, overflow: "hidden", marginTop: 4 }}>
            <div
              style={{
                width: `${pct}%`,
                height: "100%",
                background:
                  pct > 90 ? "var(--catfish-danger, #d33)" :
                  pct > 70 ? "var(--catfish-warning, #d70)" : "var(--catfish-cyan)",
              }}
            />
          </div>
        )}
      </div>

      {/* Top 员工 */}
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-2)" }}>
        Top {data.top_users.length || 0} 员工今日用量:
      </div>
      {data.top_users.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", padding: "var(--space-2)" }}>
          今日部门暂无活动
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {data.top_users.map((u) => (
          <UserRow key={u.user_email} email={u.user_email} tokens={u.tokens_used} max={data.top_users[0]?.tokens_used || 1} />
        ))}
      </div>
    </>
  );
}

function UserRow({ email, tokens, max }: { email: string; tokens: number; max: number }) {
  const pct = max === 0 ? 0 : (tokens / max) * 100;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr auto", fontSize: 12, gap: 8, alignItems: "center" }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font-mono)" }} title={email}>
        {email}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", color: "var(--catfish-text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {formatTokens(tokens)}
      </span>
      <span style={{ gridColumn: "1 / -1", height: 3, background: "var(--catfish-border)", borderRadius: 2, overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${pct}%`, background: "var(--catfish-cyan)" }} />
      </span>
    </div>
  );
}
