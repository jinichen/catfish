/** /skills — Skills Hub 全公司广场 (BL-ARCH1 5/10).
 *
 * 列表 + 详情 + publish UI 三视图. Companion 卡只列订阅 + 快速链接, 详细在这看.
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../components/Card";
import { ConfirmDialog } from "../components/Dialog";
import { roleAllows } from "../components/RoleGate";
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
            to="/market/skills/publish"
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
            <Link to="/market/skills/publish"> 在这里直接上传</Link>.
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
                to={`/market/skills/${s.namespace}/${s.name}`}
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
  const [delErr, setDelErr] = useState<string | null>(null);

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
    setDelErr(null);
    try {
      const r = await deleteSkillVersion(detail.namespace, detail.name, detail.version);
      if (r.ok) {
        navigate("/market/skills");
      } else {
        // 留在对话框里 —— 抛到页面上的话正被遮罩盖着, 看起来像"点了没反应"。
        setDelErr(r.error ?? "未知错误");
      }
    } catch (e) {
      // hub.ts 的 deleteSkillVersion 自己吞了异常, 所以这条理论上到不了。
      // 留着是因为"理论上到不了"在它哪天改了实现之后就不成立, 而那时候
      // 表现会是按钮永远停在"处理中…"。
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
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
        {/* ⚠ 权限判断用 roleAllows, 不能写 `role === "admin"`。
            后端 skills-hub 的 require_admin 明确接受 admin **和 sysadmin**
            (它自己的注释: "BL-ARCH1 P2 (5/10): sysadmin 继承 admin 权限"),
            而这里原来写死了 === "admin" —— 于是全公司最高权限的账号
            **看不到删除按钮**, 尽管它有权限。前端比后端严, 表现是
            "这个功能不见了", 没人会想到是前端判断写窄了。 */}
        {me && roleAllows(me.role, "admin") && (
          <div style={{ marginTop: "var(--space-3)" }}>
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
              删除这个版本
            </button>
          </div>
        )}

        {confirming && (
          <ConfirmDialog
            danger
            // ⚠ 标题里**不加 v**。卡片角标是 `v0.1.0`, 而 requireText 要的是
            // `0.1.0` —— 屏幕上最显眼的那个字符串跟要输的差一个字符, 是标准的
            // 近似陷阱。(facts 采纳生成的版本号更长: `0.1.0.fact-a1b2c3d4`。)
            title={`删除 ${detail.namespace}/${detail.name} ${detail.version}?`}
            confirmLabel="永久删除"
            busy={busy}
            /* ⚠ 要求原样输入版本号。不是仪式感:
               后端 storage.delete_skill_version 是 `shutil.rmtree(version_dir)`
               **物理删目录** + PG 硬删行 (它自己的注释: "硬删, 因为 FS 也
               物理删")。没有回收站、没有软删标记、界面上也没有恢复入口 ——
               删错了只能让人重新上传。

               原来的防护是"点一下变成'确认删除'再点一下", 两个按钮在同一个
               位置, 手滑连点就过去了。 */
            requireText={detail.version}
            onCancel={() => {
              setConfirming(false);
              setDelErr(null);
            }}
            onConfirm={() => void handleDelete()}
          >
            <b>物理删除，不可恢复</b> —— 文件直接从磁盘删掉，没有回收站。
            <div style={{ marginTop: 6 }}>
              只删这一个版本，同一个 skill 的其他版本保留；如果这是最后一个
              版本，整个 skill 会消失。
            </div>
            <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
              已经装到本机的员工不受影响，但之后拉不到了。
            </div>
            {delErr && (
              <div style={{ marginTop: 8, color: "var(--status-err)" }}>
                删除失败：{delErr}
              </div>
            )}
          </ConfirmDialog>
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
  /** 发布成功后要跳去哪。放 state 里而不是直接 setTimeout —— 见下面。 */
  const [goTo, setGoTo] = useState<string | null>(null);

  // ⚠ 必须 clearTimeout。react-router 的 navigate 在组件卸载后**不会**短路
  // (activeRef 只在 layout effect 里设 true, 没有 cleanup 设回 false),
  // React 18 也早就不打"卸载后 setState"的警告 —— 所以裸 setTimeout 的表现是:
  // 发布成功后一秒内点去别的页, 到点被硬拽回来, 而控制台干净。
  useEffect(() => {
    if (!goTo) return;
    const t = setTimeout(() => navigate(goTo), 1200);
    return () => clearTimeout(t);
  }, [goTo, navigate]);

  // 8/1: 两个 alert 换成就地校验。alert 的问题不只是难看 —— 它**不指向
  // 出错的那一格**, 而这个表单有两个输入; 而且点掉之后提示就没了,
  // 改的时候看不到规则。现在错误显示在对应输入框下面, 并且不满足时
  // 提交按钮直接是禁用的。
  const nsErr =
    namespace && !/^[a-z][a-z0-9_-]{0,40}$/.test(namespace)
      ? "只能用小写字母 / 数字 / - / _，且以字母开头（1-40 字符）"
      : null;
  const canSubmit = !!files && files.length > 0 && !!namespace && !nsErr;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !files) return;
    setBusy(true);
    const r = await publishSkill(namespace, Array.from(files));
    setBusy(false);
    if (r.ok) {
      setResult(`✓ 已发布 ${namespace}/${r.name}/${r.version}`);
      setGoTo(`/skills/${namespace}/${r.name}`);
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
              border: `1px solid ${nsErr ? "var(--status-err)" : "var(--border)"}`,
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-mono)",
              boxSizing: "border-box",
            }}
          />
          <div
            style={{
              fontSize: 11,
              marginTop: 4,
              color: nsErr ? "var(--status-err)" : "var(--text-muted)",
            }}
          >
            {nsErr ?? "小写字母 / 数字 / - / _，以字母开头"}
          </div>
        </label>

        <label>
          <div style={{ marginBottom: 4, fontSize: 13 }}>文件 (必须含 SKILL.md)</div>
          <input
            type="file"
            multiple
            onChange={(e) => setFiles(e.target.files)}
            style={{ fontSize: 13 }}
          />
          {/* ⚠ 这行提示必须在页面上, 不能只放按钮的 title。
              Chrome / Safari **不给 disabled 的控件派发鼠标事件**, 所以
              disabled 按钮上的原生 tooltip 根本不显示 (只有 Firefox 显示)。
              第一版就是这么写的, 结果"忘了选文件"的人看到的是一个点不动的
              灰按钮 + 零解释 —— 比原来那个 alert 还退了一步。 */}
          <div
            style={{
              fontSize: 11,
              color: files && files.length > 0 ? "var(--text-muted)" : "var(--status-err)",
              marginTop: 4,
            }}
          >
            {files && files.length > 0
              ? `已选 ${files.length} 个文件`
              : "还没选文件 —— 必须包含 SKILL.md"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            选 skill 目录里的所有文件（SKILL.md + 可选的 *.py / *.yaml 等）。
            浏览器不支持直接选目录，用 Cmd/Ctrl 多选。
          </div>
        </label>

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="submit"
            disabled={busy || !canSubmit}
            title={
              canSubmit
                ? undefined
                : !files || files.length === 0
                  ? "先选文件（必须含 SKILL.md）"
                  : !namespace
                    ? "先填 namespace"
                    : "namespace 不合法"
            }
            style={{
              background: "var(--accent)",
              color: "white",
              border: "none",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: busy || !canSubmit ? "not-allowed" : "pointer",
              opacity: busy || !canSubmit ? 0.5 : 1,
              fontSize: 13,
            }}
          >
            {busy ? "发布中…" : "发布"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/market/skills")}
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
