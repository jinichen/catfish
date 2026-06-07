/** /admin/advisory — Advisory 管理 (6/7 BL-MANIFESTO-ADVISORY-PHASE2).
 *
 * sysadmin only — admin / manager 看不到 (跟 advisory_router._require_sysadmin 一致).
 *
 * Phase 2 范围:
 *   - 列表 (含 revoked)
 *   - publish 表单 (新建 advisory)
 *   - revoke 按钮 (软删, 不真 drop)
 *
 * Phase 3 BL: 聚合统计 (处理率 / no_response_count)
 */

import { useEffect, useState } from "react";

import { Card, Row } from "../../components/Card";
import { RoleGate } from "../../components/RoleGate";
import {
  advisoryApi,
  type Advisory,
  type AdvisoryCategory,
  type AdvisorySeverity,
} from "../../lib/advisory";

const SEVERITIES: AdvisorySeverity[] = ["critical", "high", "medium", "low", "info"];
const CATEGORIES: AdvisoryCategory[] = [
  "skill_vulnerability",
  "mcp_vulnerability",
  "catfish_update",
  "policy_recommendation",
  "external_status",
  "deprecation_notice",
];

const SEVERITY_COLOR: Record<AdvisorySeverity, string> = {
  critical: "#d9534f",
  high: "#d9534f",
  medium: "#E8A33D",
  low: "#6B8589",
  info: "#0E5F66",
};

export function AdvisoryPage() {
  return (
    <RoleGate require={["sysadmin"]}>
      <AdvisoryList />
    </RoleGate>
  );
}

function AdvisoryList() {
  const [advisories, setAdvisories] = useState<Advisory[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const refresh = async () => {
    setErr(null);
    try {
      const { advisories } = await advisoryApi.list(true);
      setAdvisories(advisories);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const onRevoke = async (id: string) => {
    if (!confirm(`真要 revoke ${id}? 不真删, 改 revoked_at, 审计可见.`)) return;
    try {
      await advisoryApi.revoke(id);
      await refresh();
    } catch (e) {
      alert(`revoke 失败: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title="🛡 Advisory 管理 (sysadmin)"
        action={
          <button onClick={() => setShowForm((v) => !v)}>
            {showForm ? "取消" : "+ 发布 advisory"}
          </button>
        }
      >
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          中央 publish + 客户端 pull. 跟 fleet push 反向 — 员工有"忽略"选项,
          中央不知道员工是否真处理. <br />
          spec: docs/ADVISORY-FEED-SPEC.md · manifesto: docs/CATFISH-CENTRAL-MANIFESTO.md
        </div>
      </Card>

      {showForm && (
        <PublishForm
          onPublished={() => {
            setShowForm(false);
            void refresh();
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {err && (
        <Card title="错误">
          <div style={{ color: "#d9534f" }}>{err}</div>
        </Card>
      )}

      {advisories === null && !err && (
        <Card title="加载中…">
          <div>请稍候</div>
        </Card>
      )}

      {advisories && advisories.length === 0 && (
        <Card title="空">
          <div style={{ color: "var(--text-muted)" }}>
            还没有任何 advisory. 点 "+ 发布 advisory" 创建第一个.
          </div>
        </Card>
      )}

      {advisories && advisories.length > 0 && (
        <Card title={`列表 (${advisories.length})`}>
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-muted)" }}>
                <th style={{ padding: "6px 4px" }}>ID</th>
                <th>Severity</th>
                <th>Category</th>
                <th>Title</th>
                <th>Published</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {advisories.map((a) => {
                const isRevoked = !!a.revoked_at;
                return (
                  <tr
                    key={a.id}
                    style={{
                      borderTop: "1px solid var(--border)",
                      opacity: isRevoked ? 0.55 : 1,
                    }}
                  >
                    <td style={{ padding: "6px 4px", fontFamily: "monospace" }}>
                      {a.id}
                    </td>
                    <td style={{ color: SEVERITY_COLOR[a.severity], fontWeight: 600 }}>
                      {a.severity}
                    </td>
                    <td style={{ color: "var(--text-muted)" }}>{a.category}</td>
                    <td>{a.title}</td>
                    <td style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                      {new Date(a.published).toLocaleString()}
                    </td>
                    <td>
                      {isRevoked ? (
                        <span
                          title={`revoked by ${a.revoked_by} at ${a.revoked_at}`}
                          style={{ color: "var(--text-muted)" }}
                        >
                          ✗ revoked
                        </span>
                      ) : (
                        <span style={{ color: "#16a34a" }}>● active</span>
                      )}
                    </td>
                    <td>
                      {!isRevoked && (
                        <button
                          onClick={() => void onRevoke(a.id)}
                          style={{
                            background: "transparent",
                            border: "1px solid #d9534f",
                            color: "#d9534f",
                            padding: "2px 8px",
                            cursor: "pointer",
                            fontSize: 11,
                            borderRadius: 4,
                          }}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ── publish 表单 ─────────────────────────────────────────────

function nextAdvisoryId(): string {
  const year = new Date().getFullYear();
  const rnd = Math.floor(Math.random() * 900) + 100; // 3-digit
  return `CATFISH-ADV-${year}-${rnd}`;
}

interface PublishFormProps {
  onPublished: () => void;
  onCancel: () => void;
}

function PublishForm({ onPublished, onCancel }: PublishFormProps) {
  const [id, setId] = useState(nextAdvisoryId());
  const [severity, setSeverity] = useState<AdvisorySeverity>("medium");
  const [category, setCategory] = useState<AdvisoryCategory>("policy_recommendation");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [recommendation, setRecommendation] = useState("");
  const [skillTarget, setSkillTarget] = useState("");
  const [skillVersionPattern, setSkillVersionPattern] = useState("");
  const [catfishVersionPattern, setCatfishVersionPattern] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const target: Record<string, string> = {};
      if (skillTarget) target.skill = skillTarget;
      if (skillVersionPattern) target.skill_version_pattern = skillVersionPattern;
      if (catfishVersionPattern) target.catfish_version_pattern = catfishVersionPattern;

      await advisoryApi.publish({
        id,
        severity,
        category,
        title,
        description: description || undefined,
        recommendation: recommendation || undefined,
        target: Object.keys(target).length ? target : undefined,
        published: new Date().toISOString(),
        expires: expires || undefined,
      });
      onPublished();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="发布 advisory">
      <form
        onSubmit={onSubmit}
        style={{ display: "flex", flexDirection: "column", gap: 10 }}
      >
        <Field label="ID (CATFISH-ADV-YYYY-NNN)">
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            required
            pattern="^CATFISH-ADV-\d{4}-\d{3,}$"
            style={{ fontFamily: "monospace", width: 240 }}
          />
        </Field>

        <Field label="Severity">
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as AdvisorySeverity)}
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Category">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as AdvisoryCategory)}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Title">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={120}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label="Description (markdown)">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={5}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
          />
        </Field>

        <Field label="Recommendation">
          <input
            value={recommendation}
            onChange={(e) => setRecommendation(e.target.value)}
            maxLength={500}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label="Target — Skill name (可选)">
          <input
            value={skillTarget}
            onChange={(e) => setSkillTarget(e.target.value)}
            placeholder="e.g. eis-login"
          />
        </Field>

        <Field label="Target — Skill version pattern (可选)">
          <input
            value={skillVersionPattern}
            onChange={(e) => setSkillVersionPattern(e.target.value)}
            placeholder="<0.2.0"
          />
        </Field>

        <Field label="Target — Catfish version pattern (可选)">
          <input
            value={catfishVersionPattern}
            onChange={(e) => setCatfishVersionPattern(e.target.value)}
            placeholder="<0.16.0"
          />
        </Field>

        <Field label="Expires (ISO 8601, 可空)">
          <input
            type="datetime-local"
            value={expires}
            onChange={(e) =>
              setExpires(e.target.value ? new Date(e.target.value).toISOString() : "")
            }
          />
        </Field>

        {err && (
          <div style={{ color: "#d9534f", fontSize: 12 }}>{err}</div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button type="submit" disabled={busy}>
            {busy ? "发布中…" : "发布"}
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            取消
          </button>
        </div>
      </form>
    </Card>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Row
      label={label}
      value={<div style={{ textAlign: "left" }}>{children}</div>}
    />
  );
}
