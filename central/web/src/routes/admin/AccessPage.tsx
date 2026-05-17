/** /admin/access — 4 维 RBAC dept 管理 (BL-RBAC-DAY7 5/17).
 *
 * 全公司 dept 列表 + 单 dept 编辑 (allowed_models / allowed_tools /
 * allowed_skills / quota / description). 跟 UsersPage 路由套路一致.
 *
 * RBAC 入口:
 *   sysadmin / admin 都能进 (跟 AdminPage RoleGate 一致).
 *   manager 暂时只读 (后续 admin_router require_admin_or_above 收紧).
 *
 * 数据来源: GET /api/admin/departments (list) + GET /api/admin/departments/{name}
 * 写: PUT /api/admin/departments/{name} (allowed_* + quota + description).
 *
 * UI 设计:
 *   - 列表: 表格显示 name / dept 描述 / 各维度条目数 / quota_models_day / 编辑按钮
 *   - 详情: 4 个 textarea (一行一条 glob/name) + quota + description, 保存
 *   - 解锁 / 收紧 / 全允许 三态明确 (空 list = 全允许, 跟后端语义对齐)
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card } from "../../components/Card";
import { adminApi, type Department } from "../../lib/admin";

export function AccessPage() {
  return (
    <Routes>
      <Route index element={<DeptList />} />
      <Route path=":name" element={<DeptDetail />} />
    </Routes>
  );
}

// ── List ──────────────────────────────────────────────────────────


function DeptList() {
  const [depts, setDepts] = useState<Department[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await adminApi.listDepartments();
      setDepts(r.departments);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title={`部门 RBAC 管理 · ${depts.length} 个部门`}>
        <p style={{ color: "var(--text-muted)", margin: "0 0 var(--space-3) 0", fontSize: 13 }}>
          配置每个部门可见的模型 / 工具 / 技能 / 房间. 空 list = 全允许 (开放默认).
          员工级 override 走 <Link to="/admin/users">用户管理</Link> 页面.
        </p>

        {loading && <p style={{ color: "var(--text-muted)" }}>加载中…</p>}
        {error && (
          <p style={{ color: "var(--accent-error)" }}>加载失败: {error}</p>
        )}

        {!loading && !error && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={th}>部门</th>
                <th style={th}>说明</th>
                <th style={th}>Models</th>
                <th style={th}>Tools</th>
                <th style={th}>Skills</th>
                <th style={th}>每日 token</th>
                <th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {depts.map((d) => (
                <tr
                  key={d.name}
                  style={{ borderBottom: "1px solid var(--border-soft)" }}
                >
                  <td style={td}>
                    <strong>{d.name}</strong>
                  </td>
                  <td style={td}>
                    <span style={{ color: "var(--text-muted)" }}>
                      {d.description || "—"}
                    </span>
                  </td>
                  <td style={td}>{summarize(d.allowed_models)}</td>
                  <td style={td}>{summarize(d.allowed_tools)}</td>
                  <td style={td}>{summarize(d.allowed_skills)}</td>
                  <td style={td}>
                    {d.quota_models_day === 0 ? "∞" : fmtNum(d.quota_models_day)}
                  </td>
                  <td style={td}>
                    <Link
                      to={`/admin/access/${encodeURIComponent(d.name)}`}
                      style={linkBtn}
                    >
                      编辑
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}


function summarize(list: string[] | null | undefined): string {
  if (!list || list.length === 0) return "全允许";
  if (list.length === 1) return list[0];
  return `${list.length} 项`;
}

function fmtNum(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}


// ── Detail ───────────────────────────────────────────────────────


function DeptDetail() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [dept, setDept] = useState<Department | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 4 个 textarea 的本地状态 (一行一条)
  const [modelsText, setModelsText] = useState("");
  const [toolsText, setToolsText] = useState("");
  const [skillsText, setSkillsText] = useState("");
  const [description, setDescription] = useState("");
  const [quotaText, setQuotaText] = useState("0");

  useEffect(() => {
    (async () => {
      try {
        const r = await adminApi.getDepartment(name);
        setDept(r.department);
        setModelsText((r.department.allowed_models || []).join("\n"));
        setToolsText((r.department.allowed_tools || []).join("\n"));
        setSkillsText((r.department.allowed_skills || []).join("\n"));
        setDescription(r.department.description || "");
        setQuotaText(String(r.department.quota_models_day || 0));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [name]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const allowed_models = parseLines(modelsText);
      const allowed_tools = parseLines(toolsText);
      const allowed_skills = parseLines(skillsText);
      const quota_models_day = parseInt(quotaText, 10) || 0;
      const r = await adminApi.updateDepartment(name, {
        allowed_models,
        allowed_tools,
        allowed_skills,
        quota_models_day,
        description,
      });
      setDept(r.department);
      setError(null);
      // 成功提示, 暂时用 alert
      alert("保存成功. 员工下次 chat 时生效 (token refresh 周期 ~5 分钟).");
      navigate("/admin/access");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (error && !dept) {
    return (
      <Card title={`部门: ${name}`}>
        <p style={{ color: "var(--accent-error)" }}>加载失败: {error}</p>
        <Link to="/admin/access" style={linkBtn}>
          ← 返回部门列表
        </Link>
      </Card>
    );
  }

  if (!dept) {
    return <Card title="加载中…"><span /></Card>;
  }

  return (
    <Card title={`编辑部门 RBAC: ${dept.name}`}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        <Field
          label="说明"
          hint="部门描述, 给其他 admin 看"
          value={description}
          onChange={setDescription}
          rows={2}
        />

        <Field
          label="允许的模型 (allowed_models)"
          hint="一行一个 model name (空白 = 全允许). 例: catfish-public-deepseek-flash"
          value={modelsText}
          onChange={setModelsText}
          rows={6}
          mono
        />

        <Field
          label="允许的工具 (allowed_tools)"
          hint={`一行一个 tool name. 空白 = 全允许. 例: memory / execute_code / web_search.
ALWAYS_ON 工具 (memory / execute_code / ...) 永远保留, 不被砍.`}
          value={toolsText}
          onChange={setToolsText}
          rows={8}
          mono
        />

        <Field
          label="允许的技能 (allowed_skills)"
          hint={`glob pattern, 命名空间 catfish: / hermes:bundled: / hermes:github:owner/* / hermes:hf:owner/* / hermes:local:*.
例: catfish:* 只 catfish 自家; hermes:github:zarazhangrui/* 限 GitHub 某用户.`}
          value={skillsText}
          onChange={setSkillsText}
          rows={6}
          mono
        />

        <div>
          <label style={labelStyle}>
            每日 token 上限 (quota_models_day)
            <br />
            <span style={hintStyle}>0 = 不限</span>
          </label>
          <input
            type="number"
            value={quotaText}
            onChange={(e) => setQuotaText(e.target.value)}
            min={0}
            style={{
              width: 200,
              padding: "var(--space-1) var(--space-2)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              fontFamily: "monospace",
            }}
          />
        </div>

        {error && (
          <p style={{ color: "var(--accent-error)" }}>保存失败: {error}</p>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "var(--space-2) var(--space-3)",
              backgroundColor: "var(--accent-primary)",
              color: "white",
              border: "none",
              borderRadius: 4,
              cursor: saving ? "wait" : "pointer",
              fontWeight: 500,
            }}
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <Link
            to="/admin/access"
            style={{
              ...linkBtn,
              padding: "var(--space-2) var(--space-3)",
            }}
          >
            取消
          </Link>
        </div>
      </div>
    </Card>
  );
}


function parseLines(s: string): string[] {
  return s
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}


function Field({
  label,
  hint,
  value,
  onChange,
  rows = 4,
  mono = false,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  mono?: boolean;
}) {
  return (
    <div>
      <label style={labelStyle}>
        {label}
        {hint && (
          <>
            <br />
            <span style={hintStyle}>{hint}</span>
          </>
        )}
      </label>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        style={{
          width: "100%",
          padding: "var(--space-2)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          fontFamily: mono ? "monospace" : "inherit",
          fontSize: mono ? 13 : 14,
          resize: "vertical",
        }}
      />
    </div>
  );
}


const th: React.CSSProperties = {
  padding: "var(--space-2)",
  fontSize: 13,
  fontWeight: 500,
  color: "var(--text-muted)",
};

const td: React.CSSProperties = {
  padding: "var(--space-2)",
  verticalAlign: "middle",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  marginBottom: "var(--space-1)",
  fontSize: 14,
  fontWeight: 500,
};

const hintStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  fontWeight: 400,
  whiteSpace: "pre-line",
};

const linkBtn: React.CSSProperties = {
  display: "inline-block",
  padding: "var(--space-1) var(--space-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  textDecoration: "none",
  color: "var(--text-primary)",
  fontSize: 13,
};
