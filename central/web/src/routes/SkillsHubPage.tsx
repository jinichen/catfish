/** /skills — Skills Hub 全公司广场 (BL-ARCH1 5/10).
 *
 * 列表 + 详情 + publish UI 三视图. Companion 卡只列订阅 + 快速链接, 详细在这看.
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../components/Card";
import {
  deleteSkillVersion,
  getSkill,
  listSkills,
  publishSkill,
  type SkillSummary,
} from "../lib/hub";
import { useAuthStore } from "../store/auth";

export function SkillsHubPage() {
  return (
    <Routes>
      <Route index element={<SkillList />} />
      <Route path="publish" element={<PublishForm />} />
      <Route path=":namespace/:name" element={<SkillDetail />} />
    </Routes>
  );
}

function humanTime(iso?: string): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso.slice(0, 10);
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return iso.slice(0, 10);
}

function SkillList() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    listSkills()
      .then((r) => setSkills(r.skills || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  // 按 namespace 分组
  const filtered = filter
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(filter.toLowerCase()) ||
          (s.description || "").toLowerCase().includes(filter.toLowerCase()) ||
          s.namespace.toLowerCase().includes(filter.toLowerCase()),
      )
    : skills;

  const grouped: Record<string, SkillSummary[]> = {};
  for (const s of filtered) {
    (grouped[s.namespace] ||= []).push(s);
  }
  const namespaces = Object.keys(grouped).sort();

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
    >
      <Card
        title={`Skills Hub · ${skills.length} skill / ${namespaces.length} namespace`}
        action={
          <Link
            to="/skills/publish"
            style={{
              background: "var(--accent)",
              color: "white",
              padding: "6px 14px",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
            }}
          >
            + 发布
          </Link>
        }
      >
        <input
          type="text"
          placeholder="搜 skill 名 / 描述 / namespace…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            width: "100%",
            padding: "6px 10px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            boxSizing: "border-box",
          }}
        />
      </Card>

      {loading && <div>加载中…</div>}
      {error && <div style={{ color: "var(--status-err)" }}>{error}</div>}
      {!loading && !error && skills.length === 0 && (
        <Card title="还没人发布过 skill">
          <div style={{ color: "var(--text-muted)" }}>
            写完一个 skill 后, 让小鲶帮你发布 (调 catfish_skill_publish), 或者
            <Link to="/skills/publish"> 在这里直接上传</Link>.
          </div>
        </Card>
      )}

      {namespaces.map((ns) => (
        <div key={ns}>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              marginBottom: "var(--space-2)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {ns}/ · {grouped[ns].length} skill
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "var(--space-2)",
            }}
          >
            {grouped[ns].map((s) => (
              <Link
                key={`${s.namespace}/${s.name}`}
                to={`/skills/${s.namespace}/${s.name}`}
                style={{
                  background: "var(--bg-elev)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-3)",
                  fontSize: 13,
                  color: "var(--text)",
                  textDecoration: "none",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.borderColor = "var(--accent)")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.borderColor = "var(--border)")
                }
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    marginBottom: "var(--space-1)",
                  }}
                >
                  <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}>
                    {s.name}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    v{s.latest_version}
                  </span>
                </div>
                {s.description && (
                  <div
                    style={{
                      color: "var(--text-muted)",
                      fontSize: 12,
                      marginBottom: "var(--space-1)",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {s.description}
                  </div>
                )}
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>👤 {s.published_by || "?"}</span>
                  <span>{humanTime(s.published_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SkillDetail() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof getSkill>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!namespace || !name) return;
    getSkill(namespace, name)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [namespace, name]);

  if (!namespace || !name) return <div>路径错</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!detail) return <div>加载中…</div>;

  const handleDelete = async () => {
    setBusy(true);
    const r = await deleteSkillVersion(detail.namespace, detail.name, detail.version);
    setBusy(false);
    if (r.ok) {
      navigate("/skills");
    } else {
      alert(`删除失败: ${r.error}`);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title={`${detail.namespace} / ${detail.name}`}
        action={
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            v{detail.version}
            {detail.deprecated && (
              <span style={{ color: "var(--status-warn)", marginLeft: 8 }}>
                (deprecated)
              </span>
            )}
          </span>
        }
      >
        <div style={{ marginBottom: "var(--space-3)" }}>{detail.description}</div>
        <Row label="文件数" value={detail.files.length} />
        {me?.role === "admin" && (
          <div style={{ marginTop: "var(--space-3)" }}>
            {!confirming ? (
              <button
                onClick={() => setConfirming(true)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--status-err)",
                  color: "var(--status-err)",
                  padding: "4px 12px",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                删除版本 (admin)
              </button>
            ) : (
              <div style={{ display: "flex", gap: "var(--space-2)" }}>
                <button
                  onClick={handleDelete}
                  disabled={busy}
                  style={{
                    background: "var(--status-err)",
                    color: "white",
                    border: "none",
                    padding: "4px 12px",
                    borderRadius: "var(--radius-sm)",
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  {busy ? "..." : "确认删除"}
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--border)",
                    padding: "4px 12px",
                    borderRadius: "var(--radius-sm)",
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  取消
                </button>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card title="SKILL.md">
        <pre
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            background: "var(--bg-secondary)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-sm)",
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {detail.skill_md}
        </pre>
      </Card>

      <Card title={`文件 (${detail.files.length})`}>
        <ul style={{ margin: 0, paddingLeft: 20, fontFamily: "var(--font-mono)", fontSize: 12 }}>
          {detail.files.map((f) => (
            <li key={f}>
              {f}
              {detail.files_sha256[f] && (
                <span
                  style={{
                    marginLeft: 8,
                    color: "var(--text-muted)",
                    fontSize: 10,
                  }}
                >
                  sha256:{detail.files_sha256[f].slice(0, 12)}…
                </span>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function PublishForm() {
  const navigate = useNavigate();
  const [namespace, setNamespace] = useState("personal");
  const [files, setFiles] = useState<FileList | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files || files.length === 0) {
      alert("请选文件 (必须含 SKILL.md)");
      return;
    }
    if (!/^[a-z][a-z0-9_-]{0,40}$/.test(namespace)) {
      alert("namespace 只允许小写字母 / 数字 / -/ _ (1-40 字符)");
      return;
    }
    setBusy(true);
    const r = await publishSkill(namespace, Array.from(files));
    setBusy(false);
    if (r.ok) {
      setResult(`✓ 已发布 ${namespace}/${r.name}/${r.version}`);
      setTimeout(() => navigate(`/skills/${namespace}/${r.name}`), 1200);
    } else {
      setResult(`✗ ${r.error}`);
    }
  };

  return (
    <Card title="发布 skill 到 hub">
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
      >
        <label>
          <div style={{ marginBottom: 4, fontSize: 13 }}>Namespace</div>
          <input
            type="text"
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            placeholder="personal / department / 你部门名"
            style={{
              width: "100%",
              padding: "6px 10px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-mono)",
              boxSizing: "border-box",
            }}
          />
        </label>

        <label>
          <div style={{ marginBottom: 4, fontSize: 13 }}>文件 (必须含 SKILL.md)</div>
          <input
            type="file"
            multiple
            onChange={(e) => setFiles(e.target.files)}
            style={{ fontSize: 13 }}
          />
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              marginTop: 4,
            }}
          >
            选你 skill 目录里的所有文件 (含 SKILL.md + 可选的 *.py / *.yaml 等).
            浏览器不支持选目录直接上传, 用 Cmd/Ctrl 多选文件.
          </div>
        </label>

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="submit"
            disabled={busy}
            style={{
              background: "var(--accent)",
              color: "white",
              border: "none",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {busy ? "发布中…" : "发布"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/skills")}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            取消
          </button>
        </div>

        {result && (
          <div
            style={{
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-sm)",
              background: result.startsWith("✓")
                ? "var(--bg-secondary)"
                : "var(--bg-secondary)",
              color: result.startsWith("✓")
                ? "var(--status-ok)"
                : "var(--status-err)",
              fontSize: 13,
            }}
          >
            {result}
          </div>
        )}
      </form>
    </Card>
  );
}
