/** /market/skills — 技能 (原 Skills Hub 全公司广场, BL-ARCH1 5/10).
 *
 * 列表 + 详情 + publish UI 三视图. Companion 卡只列订阅 + 快速链接, 详细在这看.
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { BTN_PRIMARY, DataTable, Section, Toolbar, type Column } from "../components/DataTable";
import { ConfirmDialog } from "../components/Dialog";
import { PageShell } from "../components/PageShell";
import { roleAllows } from "../components/RoleGate";
import { SimpleMarkdown } from "../components/SimpleMarkdown";
import {
  deleteSkillVersion,
  getSkill,
  listSkills,
  publishSkill,
  SCOPE_LABEL,
  type ShareScope,
  type SkillReview,
  type SkillSummary,
} from "../lib/hub";
import { metaList, metaText, namespaceLabel, parseSkillMd } from "../lib/skillMd";
import { useAuthStore } from "../store/auth";
import { FilterBar, NameCell, tabLabel, useRefreshMarketCounts, when } from "./marketShared";
import {
  mineColumns,
  pendingColumns,
  ReviewBadge,
  reviewKey,
  scopeText,
  SkillReviewDialog,
  useSkillReviews,
} from "./SkillReviewDialog";

type Tab = "market" | "review" | "mine";

export function SkillsHubPage() {
  return (
    <Routes>
      <Route index element={<SkillList />} />
      <Route path="publish" element={<PublishForm />} />
      <Route path=":namespace/:name" element={<SkillDetail />} />
    </Routes>
  );
}

function SkillList() {
  const me = useAuthStore((s) => s.me);
  // 11/08: 部门主管审本部门范围的, 管理员审全公司范围的 —— 后端按 managed_depts / role 过滤, 这里只决定要不要拉
  const reviewer = !!me && roleAllows(me.role, "manager");
  const refreshCounts = useRefreshMarketCounts();
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<Tab>("market");
  const [reviewing, setReviewing] = useState<SkillReview | null>(null);
  const rv = useSkillReviews(reviewer);

  useEffect(() => {
    listSkills()
      .then((r) => setSkills(r.skills || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const needle = q.trim().toLowerCase();
  const hit = (...vals: Array<string | null | undefined>) => !needle || vals.some((v) => (v || "").toLowerCase().includes(needle));
  const market = skills
    .filter((s) => hit(s.name, s.description, s.namespace, namespaceLabel(s.namespace), s.published_by))
    .sort((a, b) => a.namespace.localeCompare(b.namespace) || a.name.localeCompare(b.name));
  const pending = (rv.pending ?? []).filter((r) => hit(r.name, r.namespace, r.submitted_by, r.submit_note));
  const mine = (rv.mine ?? []).filter((r) => hit(r.name, r.namespace, r.submit_note));

  const tabs = [
    { key: "market" as const, label: tabLabel("已上架", loading ? null : skills.length) },
    ...(reviewer ? [{ key: "review" as const, label: tabLabel("待我审核", rv.pending?.length) }] : []),
    // 大多数员工从没提交过: 没有就不占一个分段
    ...(rv.mine?.length ? [{ key: "mine" as const, label: tabLabel("我提交的", rv.mine.length) }] : []),
  ];
  const current = tabs.some((t) => t.key === tab) ? tab : "market";

  const marketCols: Column<SkillSummary>[] = [
    {
      header: "技能",
      cell: (s) => (
        <NameCell
          mono
          name={<Link to={`/market/skills/${s.namespace}/${s.name}`}>{s.name}</Link>}
          sub={s.description}
        />
      ),
    },
    { header: "范围", width: "16%", truncate: true, cell: (s) => <span title={s.namespace}>{namespaceLabel(s.namespace)}</span> },
    { header: "状态", width: 70, cell: (s) => <ReviewBadge review={s.review} /> },
    { header: "版本", width: 70, nowrap: true, cell: (s) => `v${s.latest_version}` },
    { header: "发布人", width: "20%", truncate: true, cell: (s) => <span title={s.published_by ?? ""}>{s.published_by || "?"}</span> },
    { header: "发布时间", width: 110, nowrap: true, cell: (s) => when(s.published_at) },
  ];

  const hint =
    current === "review"
      ? "你管的部门 (管理员: 全公司范围) 有人提交的技能。看清楚 SKILL.md 写了什么、脚本会做什么, 通过才上架; 没通过的同事看不到。"
      : current === "mine"
        ? "待审核的要等部门主管 (全公司范围: 管理员) 通过后同事才能看到; 被驳回的看原因改好再发一版。"
        : "已经审核通过、你能装的技能 (本部门的 + 全公司的), 加上你自己提交的。在 Companion「设置」→「技能」里安装。";

  return (
    <PageShell scroll="data">
      <Toolbar title="技能">
        <Link to="/market/skills/publish" style={{ ...BTN_PRIMARY, textDecoration: "none" }}>
          + 发布
        </Link>
      </Toolbar>
      <FilterBar
        tabs={tabs}
        tab={current}
        onTab={setTab}
        q={q}
        onQ={setQ}
        placeholder="搜技能名 / 说明 / 范围 / 发布人…"
        hint={hint}
      />
      {(error || rv.error) && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error || rv.error}</div>}
      <Section fill>
        {current === "market" && (
          <DataTable
            fill
            columns={marketCols}
            rows={market}
            rowKey={(s) => `${s.namespace}/${s.name}`}
            empty={
              loading ? "加载中…" : skills.length === 0 ? (
                <>
                  还没人发布过技能。写完一个后让小鲶帮你发布, 或者
                  <Link to="/market/skills/publish"> 在这里直接上传</Link>。
                </>
              ) : "没有匹配的技能"
            }
          />
        )}
        {current === "review" && (
          <DataTable fill columns={pendingColumns(setReviewing)} rows={pending} rowKey={reviewKey} empty="没有待审核的。" />
        )}
        {current === "mine" && (
          <DataTable fill columns={mineColumns((r) => void rv.withdraw(r))} rows={mine} rowKey={reviewKey} empty="没有匹配的" />
        )}
      </Section>
      {reviewing && (
        <SkillReviewDialog
          rec={reviewing}
          onClose={() => setReviewing(null)}
          onDone={() => {
            setReviewing(null);
            void rv.reload();
            refreshCounts();
          }}
        />
      )}
    </PageShell>
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

  if (!namespace || !name) return <PageShell><Toolbar title="技能" /><div>路径错</div></PageShell>;
  if (error) return <PageShell><Toolbar title="技能" /><div style={{ color: "var(--status-err)" }}>{error}</div></PageShell>;
  if (!detail) return <PageShell><Toolbar title="技能" /><div style={{ fontSize: 12 }}>加载中…</div></PageShell>;

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

  const { meta, body } = parseSkillMd(detail.skill_md);
  const title = metaText(meta, "title") ?? detail.name;
  const triggers = metaList(meta, "triggers");
  const author = metaText(meta, "author");
  const updated = metaText(meta, "updated") ?? metaText(meta, "created");
  const muted = { fontSize: 12, color: "var(--text-muted)" } as const;

  return (
    <PageShell gap="var(--space-4)">
      <Toolbar title={<><Link to="/market/skills" style={{ color: "var(--text-muted)", fontWeight: 400 }}>技能</Link> / {title}</>} />
      {/* 10/22 鸿波: 原来标题是 slug、正文是整份 SKILL.md 原文 (含 YAML 头), 而且页面上
          没说怎么装 —— 「自己上传的就不能安装?」。安装本来就在 Companion 里 (网页装不到员工
          电脑上), 这里把它说清楚; 头部字段拆成人话, 正文按 Markdown 排版, 原文收进折叠区。 */}
      <Card
        title={title}
        action={
          <span style={{ ...muted, display: "flex", gap: 8, alignItems: "center" }}>
            <ReviewBadge review={detail.review} />
            v{detail.version}
            {detail.deprecated && (
              <span style={{ color: "var(--status-warn)", marginLeft: 8 }}>（已停用）</span>
            )}
          </span>
        }
      >
        <div style={{ ...muted, marginTop: -8, marginBottom: "var(--space-3)", fontFamily: "var(--font-mono)" }}>
          {namespaceLabel(detail.namespace)} · {detail.namespace}/{detail.name}
        </div>
        <div style={{ marginBottom: "var(--space-3)", lineHeight: 1.7 }}>{detail.description}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)", ...muted }}>
          {author && <span>作者：{author}</span>}
          {updated && <span>更新：{updated}</span>}
          <span>文件：{detail.files.length} 个</span>
          {detail.review && !detail.review.legacy && <span>范围：{scopeText(detail.review)}</span>}
          {detail.review?.reviewed_by && detail.review.status === "approved" && (
            <span>审核：{detail.review.reviewed_by}</span>
          )}
        </div>
        {detail.review?.status === "pending" && (
          <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
            这一版还在等审核，同事暂时看不到。
          </div>
        )}
        {detail.review?.status === "rejected" && detail.review.review_note && (
          <div style={{ marginTop: "var(--space-3)", color: "var(--status-err)", fontSize: 13 }}>
            被驳回 ({detail.review.reviewed_by})：{detail.review.review_note}
          </div>
        )}
        {triggers.length > 0 && (
          <div style={{ marginTop: "var(--space-3)" }}>
            <div style={{ ...muted, marginBottom: 6 }}>员工这样说时，小鲶会用上它：</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {triggers.map((t) => (
                <span
                  key={t}
                  style={{
                    fontSize: 12,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border-soft)",
                  }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}
        <div
          style={{
            marginTop: "var(--space-4)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-sm)",
            background: "var(--bg-secondary)",
            fontSize: 13,
            lineHeight: 1.7,
          }}
        >
          <b>怎么装到自己电脑上：</b>打开鲶鱼 Companion →「设置」→「技能」→「🌐 内部技能分享」，
          找到它点「安装」。网页上装不了 —— 技能要装进员工自己的电脑。
          <div style={muted}>
            从哪台电脑上传的，那台本来就装着（Companion 里显示「✓ 已装」）；换电脑或同事要用，就在
            Companion 里装。
          </div>
        </div>
      </Card>

      <Card title="使用说明">
        {body.trim() ? <SimpleMarkdown source={body} /> : <div style={muted}>（SKILL.md 没有正文）</div>}
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
        <details style={{ marginTop: "var(--space-3)" }}>
          <summary style={{ cursor: "pointer", ...muted }}>查看原始 SKILL.md</summary>
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
        </details>
      </Card>

      {/* ⚠ 权限判断用 roleAllows, 不能写 `role === "admin"`。
          后端 skills-hub 的 require_admin 明确接受 admin **和 sysadmin**
          (它自己的注释: "BL-ARCH1 P2 (5/10): sysadmin 继承 admin 权限"),
          而这里原来写死了 === "admin" —— 于是全公司最高权限的账号
          **看不到删除按钮**, 尽管它有权限。前端比后端严, 表现是
          "这个功能不见了", 没人会想到是前端判断写窄了。
          10/22: 从摘要卡挪到页面最下面 —— 不可恢复的操作不该是看技能时第一眼的按钮。 */}
      {me && roleAllows(me.role, "admin") && (
        <Card title="管理">
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
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
            <span style={muted}>只删 v{detail.version}；已经装到员工电脑上的不受影响。</span>
          </div>
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
      )}
    </PageShell>
  );
}

function PublishForm() {
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [namespace, setNamespace] = useState("personal");
  const [files, setFiles] = useState<FileList | null>(null);
  // 11/08 分享治理: 发到哪个范围 + 给审批人看的说明。部门范围默认提交人自己的部门 (后端也这么兜底)
  const [scope, setScope] = useState<ShareScope>("department");
  const [note, setNote] = useState("");
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
    const r = await publishSkill(namespace, Array.from(files), { scope, note: note.trim() || undefined });
    setBusy(false);
    if (r.ok) {
      const who = scope === "company" ? "管理员" : "部门主管";
      setResult(`✓ 已提交 ${namespace}/${r.name}/${r.version}，等${who}审核通过后同事才能看到。可以在列表页「我提交的」里看进度。`);
      // 11/08: 原来跳技能详情 (而且路径还是老的 /skills/…, 会被兜底跳回首页); 现在回列表页看「我提交的」
      setGoTo("/market/skills");
    } else {
      setResult(`✗ ${r.error}`);
    }
  };

  return (
    <PageShell gap="var(--space-4)">
    <Toolbar title={<><Link to="/market/skills" style={{ color: "var(--text-muted)", fontWeight: 400 }}>技能</Link> / 发布</>} />
    <Card title="发布技能">
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-3)", lineHeight: 1.6 }}>
        发布后先进「待审核」：本部门范围由部门主管审，全公司范围由管理员审。审核通过并签名后同事才能看到、才能装。
      </div>
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
      >
        <label>
          <div style={{ marginBottom: 4, fontSize: 13 }}>存放位置 <span style={{ color: "var(--text-muted)", fontSize: 11 }}>(namespace)</span></div>
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

        <div>
          <div style={{ marginBottom: 4, fontSize: 13 }}>范围（发给谁用）</div>
          <div style={{ display: "flex", gap: "var(--space-4)", fontSize: 13 }}>
            {(["department", "company"] as ShareScope[]).map((v) => (
              <label key={v} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="radio" name="scope" value={v} checked={scope === v} onChange={() => setScope(v)} />
                {SCOPE_LABEL[v]}
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {v === "department" ? `（${me?.department || "你的部门"}，由部门主管审核）` : "（由管理员审核）"}
                </span>
              </label>
            ))}
          </div>
        </div>

        <label>
          <div style={{ marginBottom: 4, fontSize: 13 }}>说明（给审核的人看，选填）</div>
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, 300))}
            placeholder="这个技能做什么、会打开哪些网站、要哪些账号…"
            style={{
              width: "100%",
              padding: "6px 10px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
              fontFamily: "inherit",
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
            选技能目录里的所有文件（SKILL.md + 可选的 *.py / *.yaml 等）。
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
                    ? "先填存放位置"
                    : "存放位置不合法"
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
            {busy ? "提交中…" : "提交审核"}
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
    </PageShell>
  );
}
