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
import { PageShell } from "../../components/PageShell";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Badge, BTN, DataTable, Section, Toolbar } from "../../components/DataTable";
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
      // 跟用户页 P3.5.80 同一条: 失败时**必须清空**。留着上一次的 12 条,
      // 标题就会写"12 个部门"而下面是一句加载失败 —— 旧数据冒充当前状态。
      setDepts([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <PageShell scroll="data">
      <Toolbar
        title={`部门权限 · ${
          error ? "读取失败" : loading ? "加载中…" : `${depts.length} 个部门`
        }`}
      >
        <button style={BTN} onClick={() => void refresh()} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </Toolbar>

      {/* 这两句是**约定**, 不是装饰 —— "空 = 全允许"跟直觉相反 (看起来像
          "什么都不允许"), 不写在眼前的话, 有人会以为清空是最严的设置。
          压成一行小字, 但不能删。 */}
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        每个部门可见的模型 / 工具 / 技能。<b>留空 = 全允许</b>（开放默认）。
        员工级 override 在 <Link to="/admin/users">用户</Link>；
        {/* P3.5.93 (6/23 鸿波): 部门 token quota 编辑搬到 /admin/quota.
            原"每日 token" 列 6 周来 dead UI (gateway 不读 identity-server,
            真生效在 quotas.yaml). */}
        部门配额在 <Link to="/admin/quota">配额</Link>（走 quotas.yaml）。
      </div>

      {error && (
        <Section>
          <div style={{ fontSize: 12, color: "var(--status-err)" }}>加载失败: {error}</div>
        </Section>
      )}

      {!error && (
        <Section fill>
          <DataTable
            fill
            rows={depts}
            rowKey={(d) => d.name}
            empty={loading ? "加载中…" : "还没有部门。"}
            columns={[
              {
                header: "部门",
                // 真 <Link> 而不是只靠整行可点 —— 后者会让用户失去中键 /
                // ⌘+点击开新标签、右键"在新标签打开"、悬停看目标 URL。
                cell: (d) => (
                  <Link
                    to={`/admin/access/${encodeURIComponent(d.name)}`}
                    style={{ fontWeight: 600, color: "var(--text)" }}
                    title={d.name}
                  >
                    {d.name}
                  </Link>
                ),
                truncate: true,
                width: 160,
              },
              {
                header: "说明",
                cell: (d) => (
                  <span style={{ color: "var(--text-muted)" }} title={d.description || undefined}>
                    {d.description || "—"}
                  </span>
                ),
                truncate: true,
                width: 280,
              },
              {
                header: "模型",
                cell: (d) => <Scope list={d.allowed_models} />,
                width: 130,
                // 只有一项时 Scope 把原值渲染进徽章, 而
                // catfish-public-deepseek-flash 这种名字会把 130px 的列
                // 撑到 200px, 顶出整表的横向滚动条。
                truncate: true,
              },
              {
                header: "工具",
                cell: (d) => <Scope list={d.allowed_tools} />,
                width: 130,
                // 只有一项时 Scope 把原值渲染进徽章, 而
                // catfish-public-deepseek-flash 这种名字会把 130px 的列
                // 撑到 200px, 顶出整表的横向滚动条。
                truncate: true,
              },
              {
                header: "技能",
                cell: (d) => <Scope list={d.allowed_skills} />,
                width: 130,
                // 只有一项时 Scope 把原值渲染进徽章, 而
                // catfish-public-deepseek-flash 这种名字会把 130px 的列
                // 撑到 200px, 顶出整表的横向滚动条。
                truncate: true,
              },
              {
                header: "",
                align: "right",
                width: 64,
                cell: (d) => (
                  <Link
                    to={`/admin/access/${encodeURIComponent(d.name)}`}
                    style={{ ...BTN, textDecoration: "none", color: "var(--text)" }}
                  >
                    编辑
                  </Link>
                ),
              },
            ]}
          />
        </Section>
      )}
    </PageShell>
  );
}

/** 某一维的授权范围。
 *
 * "全允许"和"3 项"是**性质不同**的两件事, 原来都是同样的黑色文字, 一列扫下来
 * 分不出哪些部门是收紧过的。全允许用描边徽章弱化, 收紧过的给个数字 ——
 * 这一页的用处正是"谁被收紧了"。
 */
function Scope({ list }: { list: string[] | null | undefined }) {
  if (!list || list.length === 0) return <Badge tone="neutral">全允许</Badge>;
  return (
    <span title={list.join("\n")}>
      <Badge tone="accent">{list.length === 1 ? list[0] : `${list.length} 项`}</Badge>
    </span>
  );
}

// P3.5.93 (6/23 鸿波): fmtNum 唯一调用是部门列表 quota 列, 那列砍了, 函数也砍.
// QuotaConfigPage 自己有 fmtNum (略不同 UX).


// ── Detail ───────────────────────────────────────────────────────


function DeptDetail() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [dept, setDept] = useState<Department | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 保存成功。展示一下再返回列表 —— 直接跳的话页面一闪而过, 用户不确定成没成。 */
  const [saved, setSaved] = useState(false);

  // ⚠ 必须 clearTimeout。react-router 的 navigate 在组件卸载后**不会**短路
  // (activeRef 只在 layout effect 里设 true, 没有 cleanup 设回 false),
  // React 18 也早就不打"卸载后 setState"的警告了 —— 所以裸 setTimeout 的
  // 表现是: 保存成功后一秒内点侧栏去别的页, 到点被硬拽回来, 控制台干净。
  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => navigate("/admin/access"), 1200);
    return () => clearTimeout(t);
  }, [saved, navigate]);
  const [saving, setSaving] = useState(false);

  // 4 个 textarea 的本地状态 (一行一条)
  // P3.5.93 (6/23 鸿波): quotaText 砍 — 部门 quota 改在 /admin/quota (走 yaml).
  const [modelsText, setModelsText] = useState("");
  const [toolsText, setToolsText] = useState("");
  const [skillsText, setSkillsText] = useState("");
  const [description, setDescription] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const r = await adminApi.getDepartment(name);
        setDept(r.department);
        setModelsText((r.department.allowed_models || []).join("\n"));
        setToolsText((r.department.allowed_tools || []).join("\n"));
        setSkillsText((r.department.allowed_skills || []).join("\n"));
        setDescription(r.department.description || "");
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
      const r = await adminApi.updateDepartment(name, {
        allowed_models,
        allowed_tools,
        allowed_skills,
        description,
      });
      setDept(r.department);
      setError(null);
      // 8/1: 原来是 `alert()`, 注释写着"暂时用 alert" —— 那个"暂时"从 5/17
      // 留到了现在。就地提示 + 稍后返回, 跟别处一致。
      //
      // "下次 chat 时生效"这句必须保留: 保存完立刻去问员工, 他多半还是老权限,
      // 不写清楚会被当成没保存成功。
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (error && !dept) {
    return (
      <Card title={`部门: ${name}`}>
        <p style={{ color: "var(--status-err)" }}>加载失败: {error}</p>
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
      {/* 普通 div, 不是 PageShell —— PageShell 是**页面根**容器 (要吃满
          内容列的高度), 塞在 Card 里面它的 flex:1 无处可依。 */}
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

        {/* P3.5.93 (6/23 鸿波): 部门 token quota 编辑搬到 /admin/quota.
            6 周来这 input 写 identity-server PG, 但 gateway check_quota 走 quotas.yaml,
            一直没生效. 一处编辑收口到 quotas.yaml (real source of truth). */}
        <div style={{
          padding: "var(--space-2)",
          background: "var(--bg-secondary)",
          borderRadius: 4,
          fontSize: 12,
          color: "var(--text-muted)",
        }}>
          部门 token quota 改在 <Link to="/admin/quota">配额规则</Link> 页面.
          (P3.5.93 6 周来 dead UI 治本: 真生效路径走 quotas.yaml 而不是这里.)
        </div>

        {error && (
          <p style={{ color: "var(--status-err)" }}>保存失败: {error}</p>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "var(--space-2) var(--space-3)",
              backgroundColor: "var(--accent)",
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
          {saved && (
            <span style={{ fontSize: 12, color: "var(--status-ok)" }}>
              ✓ 已保存 —— 员工<b>下次对话时</b>生效（token 刷新周期约 5 分钟）
            </span>
          )}
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


// 8/1: th / td 删了 —— 列表页换成共享 DataTable 之后没人用。
// (它俩是 DataTable 文件头说的"7 张表各写各的 th/td"里的一份, 14px 字 +
//  8px 内边距, 是全后台最松的一套。)

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
  color: "var(--text)",
  fontSize: 13,
};
